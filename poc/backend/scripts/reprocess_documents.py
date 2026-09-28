"""CLI: python scripts/reprocess_documents.py [--missing-markdown | --all | --document <uuid> ...] [--confirm]

Re-runs the full pipeline (convert -> index -> extract claims -> verify) for documents already in
the database. Needed for documents ingested before Markdown conversion and the section tree
existed: they have no documents.markdown / document_sections.content, so vectorless retrieval
can't read them, and resume skips them because they're already marked ingested.

Destructive: a document's claims, verdicts, evidence and agent traces are deleted (claims point at
chunks, which re-ingestion replaces) and regenerated, with the LLM cost that implies. Without
--confirm it only lists what it would reprocess.
"""

import argparse
import asyncio
import os
import uuid

from sqlalchemy import delete, select

from app.agents.process_document import process_document
from app.db import async_session
from app.models import Claim, Document


async def _select_documents(args: argparse.Namespace) -> list[Document]:
    async with async_session() as session:
        stmt = select(Document).order_by(Document.created_at)
        if args.document:
            stmt = stmt.where(Document.id.in_([uuid.UUID(d) for d in args.document]))
        elif not args.all:
            stmt = stmt.where(Document.markdown.is_(None))
        return list((await session.execute(stmt)).scalars().all())


async def run(args: argparse.Namespace) -> None:
    documents = await _select_documents(args)
    if not documents:
        print("No documents match.")
        return

    for document in documents:
        missing = not os.path.exists(document.storage_path)
        note = "  (SKIP: stored file missing)" if missing else ""
        print(f"{document.id}  {document.status:<10}  {document.filename}{note}")

    if not args.confirm:
        print(f"\n{len(documents)} document(s) would be reprocessed. Re-run with --confirm to do it.")
        return

    for document in documents:
        if not os.path.exists(document.storage_path):
            continue
        async with async_session() as session:
            # Evidence, verdicts and agent traces cascade from claims.
            await session.execute(delete(Claim).where(Claim.document_id == document.id))
            row = await session.get(Document, document.id)
            row.status = "processing"
            row.failed_stage = None
            await session.commit()

        print(f"Reprocessing {document.id} ({document.filename}) ...")
        # Not "ingested" any more, so process_document discards the old chunks/sections/tables
        # and runs every stage from conversion onward.
        await process_document(document.id, document.storage_path)
        async with async_session() as session:
            print(f"  -> {(await session.get(Document, document.id)).status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run the full pipeline for existing documents.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--missing-markdown", action="store_true", help="documents with no Markdown yet (the default)")
    selection.add_argument("--all", action="store_true", help="every document")
    selection.add_argument("--document", action="append", metavar="UUID", help="a specific document (repeatable)")
    parser.add_argument("--confirm", action="store_true", help="actually reprocess (otherwise just list)")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
