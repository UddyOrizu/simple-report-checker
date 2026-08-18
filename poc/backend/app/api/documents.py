import json
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from app.agents.process_document import process_document
from app.db import async_session
from app.events.broadcaster import broadcaster
from app.ingestion.pipeline import load_config
from app.models import Claim, Document, PipelineRun, Verdict

router = APIRouter()

STORAGE_DIR = os.environ.get("STORAGE_DIR", "./storage")
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB
ALLOWED_EXTENSIONS = {".docx", ".pdf"}


def _document_dict(document: Document) -> dict:
    return {
        "id": str(document.id),
        "filename": document.filename,
        "file_type": document.file_type,
        "status": document.status,
        "page_count": document.page_count,
        "file_size_bytes": document.file_size_bytes,
        "has_structural_index": document.has_structural_index,
        "created_at": document.created_at.isoformat(),
    }


@router.post("/documents", status_code=202)
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)) -> dict:
    """Streams the upload to disk in bounded chunks (never the whole file in memory), enforcing
    max_upload_size_mb *during* the write rather than trusting Content-Length, then returns
    immediately and starts processing in the background — the response doesn't wait for it."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext!r}. Expected .docx or .pdf.")

    config = load_config()
    max_bytes = config["max_upload_size_mb"] * 1024 * 1024

    os.makedirs(STORAGE_DIR, exist_ok=True)
    document_id = uuid.uuid4()
    storage_path = os.path.join(STORAGE_DIR, f"{document_id}{ext}")

    bytes_written = 0
    exceeded = False
    with open(storage_path, "wb") as f:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > max_bytes:
                exceeded = True
                break
            f.write(chunk)

    if exceeded:
        os.remove(storage_path)
        raise HTTPException(
            status_code=413, detail=f"File exceeds the {config['max_upload_size_mb']}MB upload size limit."
        )

    file_type = "docx" if ext == ".docx" else "pdf"
    async with async_session() as session:
        document = Document(
            id=document_id,
            filename=file.filename,
            file_type=file_type,
            storage_path=storage_path,
            file_size_bytes=bytes_written,
            status="queued",
        )
        session.add(document)
        await session.commit()

    background_tasks.add_task(process_document, document_id, storage_path)

    return {"id": str(document_id), "status": "queued"}


@router.get("/documents")
async def list_documents(limit: int = 20, offset: int = 0, status: str | None = None) -> list[dict]:
    """History list — newest first, each with a claim-verdict summary computed at query time."""
    async with async_session() as session:
        stmt = select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
        if status is not None:
            stmt = stmt.where(Document.status == status)
        documents = (await session.execute(stmt)).scalars().all()

        results = []
        for document in documents:
            summary_stmt = (
                select(Verdict.final_verdict, func.count())
                .join(Claim, Claim.id == Verdict.claim_id)
                .where(Claim.document_id == document.id)
                .group_by(Verdict.final_verdict)
            )
            claim_summary = {verdict: count for verdict, count in (await session.execute(summary_stmt)).all()}
            results.append({**_document_dict(document), "claim_summary": claim_summary})
        return results


@router.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID) -> dict:
    async with async_session() as session:
        document = await session.get(Document, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return _document_dict(document)


async def stream_events(document_id: uuid.UUID):
    """Subscribes to the in-process broadcaster (Phase 7.1) and formats each event for SSE.
    Event types published across the pipeline: ingest_progress, ingest_complete,
    claim_extracted, claim_verified, verification_complete. A standalone generator (not nested
    in the route handler) so it's directly testable without a live HTTP connection."""
    queue = broadcaster.subscribe(document_id)
    try:
        while True:
            event = await queue.get()
            yield {"event": event.get("event", "message"), "data": json.dumps(event)}
    finally:
        broadcaster.unsubscribe(document_id, queue)


@router.get("/documents/{document_id}/events")
async def document_events(document_id: uuid.UUID) -> EventSourceResponse:
    """SSE stream of processing progress for one document."""
    return EventSourceResponse(stream_events(document_id))


@router.get("/documents/{document_id}/claims")
async def list_claims(document_id: uuid.UUID) -> list[dict]:
    """All claims + their latest verdict for a document, including scope and domain."""
    async with async_session() as session:
        claims = (await session.execute(select(Claim).where(Claim.document_id == document_id))).scalars().all()

        results = []
        for claim in claims:
            verdict = (
                await session.execute(
                    select(Verdict).where(Verdict.claim_id == claim.id).order_by(Verdict.resolved_at.desc())
                )
            ).scalars().first()
            results.append(
                {
                    "id": str(claim.id),
                    "claim_text": claim.claim_text,
                    "claim_type": claim.claim_type,
                    "scope": claim.scope,
                    "domain": claim.domain,
                    "status": claim.status,
                    "final_verdict": verdict.final_verdict if verdict else None,
                    "final_confidence": verdict.final_confidence if verdict else None,
                    "severity": verdict.severity if verdict else None,
                }
            )
        return results


@router.get("/documents/{document_id}/runs")
async def list_pipeline_runs(document_id: uuid.UUID, stage: str | None = None) -> list[dict]:
    async with async_session() as session:
        stmt = select(PipelineRun).where(PipelineRun.document_id == document_id).order_by(PipelineRun.created_at.desc())
        if stage is not None:
            stmt = stmt.where(PipelineRun.stage == stage)
        runs = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(run.id),
                "stage": run.stage,
                "config_hash": run.config_hash,
                "input_ref": run.input_ref,
                "raw_output": run.raw_output,
                "duration_ms": run.duration_ms,
                "created_at": run.created_at.isoformat(),
            }
            for run in runs
        ]
