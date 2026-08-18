"""CLI: python scripts/run_ingest.py <path-to-docx-or-pdf>

Runs the ingestion pipeline (parsing -> chunking -> structural indexing -> section
summarization) standalone against a single file, prints the resulting chunks/tables/sections as
JSON, and persists a Document + its chunks/sections/tables plus a `pipeline_runs` row.
"""

import argparse
import asyncio
import json
import os

from app.db import async_session
from app.ingestion.chunker import is_low_confidence_ocr
from app.ingestion.pipeline import load_config, run_ingestion
from app.models import Document


async def run(path: str) -> dict:
    config = load_config()
    file_type = "docx" if path.lower().endswith(".docx") else "pdf"

    async with async_session() as session:
        document = Document(filename=os.path.basename(path), file_type=file_type, storage_path=path, status="queued")
        session.add(document)
        await session.commit()
        document_id = document.id

    result = await run_ingestion(document_id, path, config)
    return _format_for_display(result, config)


def _chunk_for_display(chunk: dict, config: dict) -> dict:
    display = {k: v for k, v in chunk.items() if k != "element_index"}
    if "ocr_confidence" in display:
        display["low_confidence_ocr"] = is_low_confidence_ocr(chunk, config)
    return display


def _format_for_display(result: dict, config: dict) -> dict:
    elements = result["elements"]
    tables = [{"page_number": e.get("page_number"), "data": e["data"]} for e in elements if e["type"] == "table"]
    sections = result["sections"] or []

    return {
        "document_id": result["document_id"],
        "pipeline_run_id": result["pipeline_run_id"],
        "filename": result["filename"],
        "file_type": result["file_type"],
        "page_count": result["page_count"],
        "has_structural_index": result["has_structural_index"],
        "chunks": [_chunk_for_display(c, config) for c in result["chunks"]],
        "tables": tables,
        "sections": [{k: v for k, v in s.items() if k not in ("start_index", "end_index")} for s in sections],
        "duration_ms": result["duration_ms"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ingestion pipeline against a single document.")
    parser.add_argument("path", help="Path to a .docx or .pdf file")
    args = parser.parse_args()

    result = asyncio.run(run(args.path))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
