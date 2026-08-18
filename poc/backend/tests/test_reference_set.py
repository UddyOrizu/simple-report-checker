import os

import pytest
import yaml

REFERENCE_SET_DIR = os.path.join(os.path.dirname(__file__), "reference_set")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set — LLM stage is BLOCKED-CREDENTIALS")


def load_reference_set() -> list[dict]:
    entries = []
    for filename in sorted(os.listdir(REFERENCE_SET_DIR)):
        if filename.endswith(".yaml"):
            with open(os.path.join(REFERENCE_SET_DIR, filename)) as f:
                entries.append(yaml.safe_load(f))
    return entries


def test_reference_set_has_three_to_five_documents():
    entries = load_reference_set()
    assert 3 <= len(entries) <= 5


def test_reference_set_documents_are_real_phase_2_fixtures():
    for doc in load_reference_set():
        fixture_path = os.path.join(FIXTURES_DIR, doc["document"])
        assert os.path.exists(fixture_path), f"{doc['document']} not found in tests/fixtures/"


def test_reference_set_covers_all_three_scope_values():
    scopes = {claim["scope"] for doc in load_reference_set() for claim in doc["expected_claims"]}
    assert scopes == {"internal", "external", "both"}


def test_reference_set_covers_at_least_three_claim_types():
    claim_types = {claim["claim_type"] for doc in load_reference_set() for claim in doc["expected_claims"]}
    assert len(claim_types) >= 3


async def test_deterministic_claims_match_hand_checked_verdicts(fixtures_dir):
    """Runs the real ingest -> deterministic-verify path against every reference-set document's
    deterministic claims and confirms the recomputed verdict matches the hand-checked
    expectation. No ANTHROPIC_API_KEY needed — arithmetic recomputation never calls an LLM,
    unlike the agent-resolved claims elsewhere in the reference set."""
    import yaml as _yaml
    from sqlalchemy import select

    from app.agents.deterministic_verifier import resolves_deterministically, verify_deterministic
    from app.db import async_session
    from app.ingestion.chunker import chunk_document
    from app.ingestion.parsers.docx_parser import parse_docx
    from app.ingestion.parsers.pdf_parser import parse_pdf
    from app.models import Claim, Document, DocumentChunk, ExtractedTable

    registry = _yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")))
    thresholds = _yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "thresholds.yaml")))
    ingestion_config = _yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")))

    for doc in load_reference_set():
        deterministic_claims = [
            c
            for c in doc["expected_claims"]
            if resolves_deterministically(c["domain"], c["claim_type"], registry)
        ]
        if not deterministic_claims:
            continue

        path = os.path.join(fixtures_dir, doc["document"])
        elements = parse_docx(path) if path.endswith(".docx") else parse_pdf(path, ingestion_config)
        title = next((e["text"] for e in elements if e["type"] == "heading"), doc["document"])
        chunks = chunk_document(elements, document_title=title)

        file_type = "docx" if path.endswith(".docx") else "pdf"
        async with async_session() as session:
            document = Document(filename=doc["document"], file_type=file_type, storage_path=path, status="ingested")
            session.add(document)
            await session.flush()

            for chunk in chunks:
                element = elements[chunk["element_index"]]
                session.add(
                    DocumentChunk(
                        document_id=document.id,
                        chunk_type=chunk["chunk_type"],
                        chunk_text=chunk["chunk_text"],
                        context_capsule=chunk["context_capsule"],
                        page_number=chunk.get("page_number"),
                        char_start=chunk["char_start"],
                        char_end=chunk["char_end"],
                    )
                )
                if chunk["chunk_type"] == "table":
                    session.add(ExtractedTable(document_id=document.id, page_number=chunk.get("page_number"), table_data=element["data"]))
            await session.flush()

            origin_chunk = (
                await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == document.id).where(DocumentChunk.chunk_type == "paragraph"))
            ).scalars().first()

            for expected in deterministic_claims:
                claim = Claim(
                    document_id=document.id,
                    chunk_id=origin_chunk.id,
                    claim_text=expected["source_sentence"],
                    source_span=expected["source_sentence"],
                    claim_type=expected["claim_type"],
                    scope=expected["scope"],
                    domain=expected["domain"],
                    requires=["current period revenue", "prior period revenue"],
                )
                session.add(claim)
                await session.flush()

                result = await verify_deterministic(session, claim, thresholds)

                assert result["final_verdict"] == expected["expected_verdict"], (
                    f"{doc['document']}: expected {expected['expected_verdict']}, got {result['final_verdict']}"
                )
                assert result["resolved_by"] == expected["expected_resolved_by"]

            await session.rollback()


@requires_llm
async def test_agent_resolved_claims_match_hand_checked_verdicts(fixtures_dir):
    """The agent-path counterpart to the deterministic test above — runs every non-deterministic
    reference-set claim through the real verifier+challenger pipeline (Phase 6) and checks the
    reconciled verdict against the hand-checked expectation. Needs a real API key; each YAML
    entry's `notes` field flags where a claim depends on a judgment call the live agent could
    reasonably resolve differently."""
    import yaml as _yaml
    from sqlalchemy import select

    from app.agents.deterministic_verifier import resolves_deterministically
    from app.agents.verify_claim import verify_claim_via_agents
    from app.db import async_session
    from app.ingestion.chunker import chunk_document
    from app.ingestion.parsers.docx_parser import parse_docx
    from app.ingestion.parsers.pdf_parser import parse_pdf
    from app.models import Claim, Document, DocumentChunk, ExtractedTable

    registry = _yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")))
    ingestion_config = _yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")))

    for doc in load_reference_set():
        agent_claims = [
            c for c in doc["expected_claims"] if not resolves_deterministically(c["domain"], c["claim_type"], registry)
        ]
        if not agent_claims:
            continue

        path = os.path.join(fixtures_dir, doc["document"])
        elements = parse_docx(path) if path.endswith(".docx") else parse_pdf(path, ingestion_config)
        title = next((e["text"] for e in elements if e["type"] == "heading"), doc["document"])
        chunks = chunk_document(elements, document_title=title)
        file_type = "docx" if path.endswith(".docx") else "pdf"

        async with async_session() as session:
            document = Document(filename=doc["document"], file_type=file_type, storage_path=path, status="ingested")
            session.add(document)
            await session.flush()

            for chunk in chunks:
                element = elements[chunk["element_index"]]
                session.add(
                    DocumentChunk(
                        document_id=document.id,
                        chunk_type=chunk["chunk_type"],
                        chunk_text=chunk["chunk_text"],
                        context_capsule=chunk["context_capsule"],
                        page_number=chunk.get("page_number"),
                        char_start=chunk["char_start"],
                        char_end=chunk["char_end"],
                    )
                )
                if chunk["chunk_type"] == "table":
                    session.add(ExtractedTable(document_id=document.id, page_number=chunk.get("page_number"), table_data=element["data"]))
            await session.flush()

            origin_chunk = (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == document.id).where(DocumentChunk.chunk_type == "paragraph")
                )
            ).scalars().first()

            for expected in agent_claims:
                claim = Claim(
                    document_id=document.id,
                    chunk_id=origin_chunk.id,
                    claim_text=expected["source_sentence"],
                    source_span=expected["source_sentence"],
                    claim_type=expected["claim_type"],
                    scope=expected["scope"],
                    domain=expected["domain"],
                    requires=["current period revenue", "prior period revenue"],
                )
                session.add(claim)
                await session.flush()

                result = await verify_claim_via_agents(session, claim, config={})

                assert result["reconciled"]["final_verdict"] == expected["expected_verdict"], (
                    f"{doc['document']}: expected {expected['expected_verdict']}, "
                    f"got {result['reconciled']['final_verdict']} — see notes: {expected['notes']}"
                )

        # verify_claim_via_agents commits internally (it persists agent_traces/verdicts rows for
        # real), so — unlike the deterministic test above — a plain rollback can't undo it; clean
        # up explicitly instead
        async with async_session() as cleanup_session:
            await cleanup_session.execute(Document.__table__.delete().where(Document.id == document.id))
            await cleanup_session.commit()
