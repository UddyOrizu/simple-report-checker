import io
import os
from unittest.mock import patch

import httpx
import pytest

from app.db import async_session
from app.main import app
from app.models import Document

HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set — LLM stage is BLOCKED-CREDENTIALS")


async def _client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _cleanup_document(document_id: str) -> None:
    async with async_session() as session:
        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()


async def test_upload_under_limit_succeeds_and_streams_correctly(tmp_path, fixtures_dir):
    with patch("app.api.documents.STORAGE_DIR", str(tmp_path)):
        async with await _client() as client:
            with open(os.path.join(fixtures_dir, "sample_report.docx"), "rb") as f:
                response = await client.post(
                    "/documents",
                    files={"file": ("sample_report.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    document_id = body["id"]

    stored_files = list(tmp_path.iterdir())
    assert len(stored_files) == 1
    assert stored_files[0].stat().st_size == os.path.getsize(os.path.join(fixtures_dir, "sample_report.docx"))

    await _cleanup_document(document_id)


async def test_upload_over_limit_rejected_with_413_and_leaves_no_partial_file(tmp_path):
    with patch("app.api.documents.STORAGE_DIR", str(tmp_path)), patch("app.api.documents.load_config") as mock_config:
        mock_config.return_value = {"max_upload_size_mb": 1}  # tiny limit so the test stays fast
        oversized_content = b"x" * (2 * 1024 * 1024)  # 2MB, over the 1MB test limit

        async with await _client() as client:
            response = await client.post(
                "/documents", files={"file": ("big.pdf", io.BytesIO(oversized_content), "application/pdf")}
            )

    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


async def test_upload_rejects_unsupported_file_type(tmp_path):
    with patch("app.api.documents.STORAGE_DIR", str(tmp_path)):
        async with await _client() as client:
            response = await client.post(
                "/documents", files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
            )

    assert response.status_code == 400
    assert list(tmp_path.iterdir()) == []


async def test_get_document_404_for_unknown_id():
    async with await _client() as client:
        response = await client.get("/documents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


async def test_get_document_returns_metadata(tmp_path, fixtures_dir):
    with patch("app.api.documents.STORAGE_DIR", str(tmp_path)):
        async with await _client() as client:
            with open(os.path.join(fixtures_dir, "sample_report.docx"), "rb") as f:
                upload = await client.post("/documents", files={"file": ("sample_report.docx", f)})
            document_id = upload.json()["id"]

            response = await client.get(f"/documents/{document_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == document_id
    assert body["filename"] == "sample_report.docx"
    # httpx's ASGITransport fully awaits the request, BackgroundTasks included, before
    # returning — so by the time we get a response, real (LLM-free) ingestion has finished
    assert body["status"] == "complete"
    assert body["page_count"] == 1

    await _cleanup_document(document_id)


async def test_history_listing_newest_first_with_pagination_and_status_filter(tmp_path, fixtures_dir):
    document_ids = []
    with patch("app.api.documents.STORAGE_DIR", str(tmp_path)):
        async with await _client() as client:
            for i in range(3):
                with open(os.path.join(fixtures_dir, "sample_report.docx"), "rb") as f:
                    upload = await client.post("/documents", files={"file": (f"doc{i}.docx", f)})
                document_ids.append(upload.json()["id"])

            response = await client.get("/documents", params={"limit": 2, "offset": 0})
            assert response.status_code == 200
            page = response.json()
            assert len(page) == 2
            # newest first: the last-uploaded document should appear before the first
            returned_ids = [d["id"] for d in page]
            assert returned_ids[0] == document_ids[-1]

            # ASGITransport fully awaits BackgroundTasks, so by now these documents are complete
            filtered = await client.get("/documents", params={"status": "complete", "limit": 50})
            assert all(d["status"] == "complete" for d in filtered.json())
            assert set(document_ids) <= {d["id"] for d in filtered.json()}

            filtered_out = await client.get("/documents", params={"status": "queued", "limit": 50})
            assert not (set(document_ids) & {d["id"] for d in filtered_out.json()})

    for document_id in document_ids:
        await _cleanup_document(document_id)


async def test_document_claims_and_runs_listings_reflect_a_real_processed_document(fixtures_dir):
    """Runs the real (LLM-free) ingestion + extraction pipeline directly — not through the
    upload endpoint's BackgroundTasks, which ASGITransport doesn't reliably await — then checks
    the read endpoints reflect it correctly."""
    from app.agents.process_document import process_document

    path = os.path.join(fixtures_dir, "sample_report.docx")
    async with async_session() as session:
        document = Document(filename="sample_report.docx", file_type="docx", storage_path=path, status="queued")
        session.add(document)
        await session.commit()
        document_id = document.id

    await process_document(document_id, path)

    async with await _client() as client:
        claims_response = await client.get(f"/documents/{document_id}/claims")
        runs_response = await client.get(f"/documents/{document_id}/runs")
        runs_filtered = await client.get(f"/documents/{document_id}/runs", params={"stage": "nonexistent"})

    assert claims_response.status_code == 200
    claims = claims_response.json()
    assert len(claims) == 1  # only the one simple, non-decomposable sentence extracts without a key
    assert claims[0]["scope"] == "internal"

    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert len(runs) == 1
    assert runs[0]["stage"] == "ingest"

    assert runs_filtered.json() == []

    await _cleanup_document(str(document_id))
