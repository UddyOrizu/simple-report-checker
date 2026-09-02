import os

import pytest_asyncio
import yaml
from sqlalchemy import select

from app.agents.extract_claims import _has_groundable_entity, direct_claim, extract_claims_for_document, split_sentences
from app.events.broadcaster import broadcaster
from app.models import Claim, Document, DocumentChunk, DocumentSection


def _registry():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "domain_registry.yaml")
    return yaml.safe_load(open(path))


def test_split_sentences():
    sentences = split_sentences("Revenue grew 12%. The team celebrated. Next quarter looks strong.")
    assert sentences == ["Revenue grew 12%.", "The team celebrated.", "Next quarter looks strong."]


def test_direct_claim_statistical_when_sentence_has_a_number():
    claim = direct_claim("Headcount grew to 500 employees.")
    assert claim["claim_type"] == "statistical"
    assert claim["scope"] == "internal"
    assert claim["requires"] == []


def test_direct_claim_definitional_when_sentence_has_no_number():
    claim = direct_claim("The company operates in the software industry.")
    assert claim["claim_type"] == "definitional"


def test_groundable_entity_true_for_money_percent_date_law():
    for label in ("MONEY", "PERCENT", "DATE", "LAW"):
        assert _has_groundable_entity([{"text": "x", "label": label}])


def test_groundable_entity_false_for_bare_cardinal_or_org():
    """Headcount-style numbers (CARDINAL) and a bare company mention (ORG) shouldn't force a
    routing call on their own — only MONEY/PERCENT/DATE/LAW, the types router.md's rule 2 says
    always need external grounding, should override the direct_claim fast path."""
    assert not _has_groundable_entity([{"text": "500", "label": "CARDINAL"}])
    assert not _has_groundable_entity([{"text": "Acme Ltd", "label": "ORG"}])
    assert not _has_groundable_entity([])


def test_groundable_entity_is_case_insensitive():
    assert _has_groundable_entity([{"text": "£4.2M", "label": "money"}])


@pytest_asyncio.fixture
async def simple_sentence_document(db_session):
    document = Document(filename="x.docx", file_type="docx", storage_path="x", status="ingested")
    db_session.add(document)
    await db_session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        chunk_type="paragraph",
        chunk_text="Headcount grew to 500 employees.",
        context_capsule="Report > Section",
    )
    db_session.add(chunk)
    await db_session.flush()
    return document.id


@pytest_asyncio.fixture
async def complex_sentence_document(db_session):
    document = Document(filename="x.docx", file_type="docx", storage_path="x", status="ingested")
    db_session.add(document)
    await db_session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        chunk_type="paragraph",
        chunk_text=(
            "Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost "
            "discipline, positioning us ahead of our closest competitor."
        ),
        context_capsule="Report > Section",
    )
    db_session.add(chunk)
    await db_session.flush()
    return document.id


async def test_simple_sentence_produces_one_claim_without_any_llm_call(db_session, simple_sentence_document):
    queue = broadcaster.subscribe(simple_sentence_document)

    claims = await extract_claims_for_document(db_session, simple_sentence_document, _registry())

    assert len(claims) == 1
    assert claims[0].claim_type == "statistical"
    assert claims[0].scope == "internal"
    assert claims[0].domain is not None  # semantic tier always resolves to something

    persisted = (await db_session.execute(select(Claim).where(Claim.document_id == simple_sentence_document))).scalars().all()
    assert len(persisted) == 1

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    broadcaster.unsubscribe(simple_sentence_document, queue)
    assert len(events) == 1
    assert events[0]["event"] == "claim_extracted"


async def test_decomposable_sentence_yields_no_claims_without_an_api_key(db_session, complex_sentence_document):
    """Without ANTHROPIC_API_KEY, a sentence needing decomposition is skipped gracefully rather
    than crashing the whole extraction run — the BLOCKED-CREDENTIALS contract."""
    assert not os.environ.get("ANTHROPIC_API_KEY")

    claims = await extract_claims_for_document(db_session, complex_sentence_document, _registry())

    assert claims == []


@pytest_asyncio.fixture
async def boilerplate_chunk_document(db_session):
    """A chunk that's skipped for a reason unrelated to LLM availability (its section is
    boilerplate) — exercises the resume bookkeeping without needing a real embeddings call, unlike
    the other fixtures here whose chunks actually produce a claim."""
    document = Document(filename="x.docx", file_type="docx", storage_path="x", status="ingested")
    db_session.add(document)
    await db_session.flush()

    section = DocumentSection(document_id=document.id, title="Disclaimer", order_index=0)
    db_session.add(section)
    await db_session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        section_id=section.id,
        chunk_type="paragraph",
        chunk_text="This report is provided as-is and does not constitute advice.",
        context_capsule="Report > Disclaimer",
    )
    db_session.add(chunk)
    await db_session.flush()
    return document.id


async def test_boilerplate_chunk_is_marked_claims_extracted_without_producing_claims(
    db_session, boilerplate_chunk_document
):
    claims = await extract_claims_for_document(db_session, boilerplate_chunk_document, _registry())
    assert claims == []

    chunk = (
        await db_session.execute(select(DocumentChunk).where(DocumentChunk.document_id == boilerplate_chunk_document))
    ).scalars().one()
    assert chunk.claims_extracted is True


async def test_resumed_extraction_skips_a_chunk_already_marked_claims_extracted(db_session, simple_sentence_document):
    """A chunk left over from an interrupted run that already got through claim extraction (e.g.
    the crash happened on a later chunk) must not be reprocessed on resume — reprocessing it would
    decompose the same sentence again and persist a duplicate claim."""
    chunk = (
        await db_session.execute(select(DocumentChunk).where(DocumentChunk.document_id == simple_sentence_document))
    ).scalars().one()
    chunk.claims_extracted = True
    await db_session.commit()

    claims = await extract_claims_for_document(db_session, simple_sentence_document, _registry())

    assert claims == []
