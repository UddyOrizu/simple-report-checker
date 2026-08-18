import os

import yaml

from app.db import async_session
from app.models import Claim, Document, DocumentChunk, ExtractedTable
from app.tuning.snapshot import diff_snapshots, take_snapshot

THRESHOLDS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "thresholds.yaml")
REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")


def _thresholds():
    return yaml.safe_load(open(THRESHOLDS_PATH))


def _registry():
    return yaml.safe_load(open(REGISTRY_PATH))


async def test_compare_runs_isolates_exactly_which_claims_changed_from_a_tolerance_edit():
    """The Phase 8.3 verification gate, almost verbatim: change arithmetic_tolerance_pct, re-run
    under old and new config, confirm the diff correctly isolates exactly which claims' verdicts
    changed. Stated 12.6% vs. a computed 12% (diff = 0.6 points) straddles the default 0.5%
    tolerance — contradicted under it, supported once tolerance widens past 0.6."""
    # take_snapshot uses app.db.async_session (a real, committing connection) directly, not the
    # rollback-wrapped db_session fixture — so this test commits its own fixture data too, and
    # cleans it up explicitly at the end.
    async with async_session() as session:
        document = Document(filename="borderline.docx", file_type="docx", storage_path="x", status="ingested")
        session.add(document)
        await session.flush()

        session.add(
            ExtractedTable(
                document_id=document.id,
                page_number=1,
                table_data=[["Metric", "Value"], ["Revenue (current period)", "$112M"], ["Revenue (prior period)", "$100M"]],
            )
        )
        origin_chunk = DocumentChunk(
            document_id=document.id, chunk_type="paragraph", chunk_text="Revenue grew 12.6% year-over-year", page_number=1
        )
        session.add(origin_chunk)
        await session.flush()

        claim = Claim(
            document_id=document.id,
            chunk_id=origin_chunk.id,
            claim_text="Revenue grew 12.6% year-over-year",
            source_span="Revenue grew 12.6% year-over-year",
            claim_type="statistical",
            scope="internal",
            domain="financial",
            requires=["current period revenue", "prior period revenue"],
        )
        session.add(claim)
        await session.commit()
        document_id = document.id

    original_thresholds_text = open(THRESHOLDS_PATH).read()
    try:
        registry = _registry()

        # snapshot A: default tolerance (0.5) — 0.6-point difference is contradicted
        path_a = await take_snapshot(document_id, _thresholds(), registry)
        hash_a = os.path.basename(path_a).split("__")[1].removesuffix(".json")

        with open(THRESHOLDS_PATH, "w") as f:
            f.write("arithmetic_tolerance_pct: 1.0\n")

        # snapshot B: widened tolerance (1.0) — the same 0.6-point difference now supported
        path_b = await take_snapshot(document_id, _thresholds(), registry)
        hash_b = os.path.basename(path_b).split("__")[1].removesuffix(".json")

        assert hash_a != hash_b  # the config edit really did change the hash

        from app.tuning.snapshot import load_snapshot

        snapshot_a = load_snapshot(str(document_id), hash_a)
        snapshot_b = load_snapshot(str(document_id), hash_b)

        assert snapshot_a["claims"][0]["verdict"] == "contradicted"
        assert snapshot_b["claims"][0]["verdict"] == "supported"

        diffs = diff_snapshots(snapshot_a, snapshot_b)

        assert len(diffs) == 1
        assert diffs[0]["claim_id"] == str(claim.id)
        assert diffs[0]["changes"]["verdict"] == ["contradicted", "supported"]

        os.remove(path_a)
        os.remove(path_b)
    finally:
        with open(THRESHOLDS_PATH, "w") as f:
            f.write(original_thresholds_text)

        async with async_session() as session:
            await session.execute(Document.__table__.delete().where(Document.id == document_id))
            await session.commit()
