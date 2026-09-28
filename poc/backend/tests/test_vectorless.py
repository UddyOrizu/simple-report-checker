from unittest.mock import patch

from sqlalchemy import select

from app.models import AgentTrace, Claim, Document, DocumentChunk, DocumentSection
from app.retrieval.vectorless import (
    ExtractedQuote,
    NavigationChoice,
    QuoteExtraction,
    _build_tree,
    _outline,
    find_section_evidence,
)

CONFIG = {
    "enabled": True,
    "max_sections": 2,
    "max_section_chars": 24000,
    "max_quotes_per_section": 3,
    "quote_match_threshold": 0.85,
}
CLAIM_SENTENCE = "Revenue grew 12% year on year."


def _fake_llm(nav_ids: list[str], quotes: list[ExtractedQuote]):
    prompts: list[str] = []

    async def fake(prompt, output_schema, tier="standard"):
        prompts.append(prompt)
        if output_schema is NavigationChoice:
            return NavigationChoice(section_ids=nav_ids, reasoning="revenue figures live in Financials > Revenue")
        assert output_schema is QuoteExtraction
        return QuoteExtraction(quotes=quotes)

    return fake, prompts


async def _document_with_tree(db_session) -> tuple[Claim, dict[str, DocumentSection]]:
    document = Document(filename="report.pdf", file_type="pdf", storage_path="x", status="ingested", has_structural_index=True)
    db_session.add(document)
    await db_session.flush()

    def section(title, order_index, level, parent=None, content="", summary=None, page=None):
        return DocumentSection(
            document_id=document.id, title=title, is_pseudo_section=False, order_index=order_index, level=level,
            parent_id=parent.id if parent else None, content=content, summary=summary, page_start=page, page_end=page,
        )

    summary = section("Executive Summary", 0, 1, content=CLAIM_SENTENCE, summary="Headline results.", page=1)
    db_session.add(summary)
    financials = section("Financials", 1, 1, content="Audited figures follow.", summary="Financial statements.", page=4)
    db_session.add(financials)
    await db_session.flush()
    revenue = section(
        "Revenue", 2, 2, parent=financials, page=5, summary="Revenue by period.",
        content="Revenue for FY24 was **$112M**, compared with $100M in FY23.\n\n| Region | Revenue |\n|---|---|\n| APAC | $40M |",
    )
    db_session.add(revenue)
    await db_session.flush()

    origin = DocumentChunk(document_id=document.id, section_id=summary.id, chunk_type="paragraph", chunk_text=CLAIM_SENTENCE, page_number=1)
    db_session.add_all([
        origin,
        DocumentChunk(document_id=document.id, section_id=revenue.id, chunk_type="paragraph",
                      chunk_text="Revenue for FY24 was $112M, compared with $100M in FY23.", page_number=5),
    ])
    await db_session.flush()

    claim = Claim(
        document_id=document.id, chunk_id=origin.id, claim_text=CLAIM_SENTENCE, source_span=CLAIM_SENTENCE,
        claim_type="statistical", scope="internal", requires=["current revenue", "prior revenue"],
    )
    db_session.add(claim)
    await db_session.flush()
    return claim, {"summary": summary, "financials": financials, "revenue": revenue}


async def test_outline_nests_subsections_under_parents(db_session):
    claim, sections = await _document_with_tree(db_session)

    outline = _outline(_build_tree([sections["summary"], sections["financials"], sections["revenue"]]))

    assert outline.splitlines() == [
        "- [1] Executive Summary: Headline results.",
        "- [2] Financials: Financial statements.",
        "  - [2.1] Revenue: Revenue by period.",
    ]


async def test_returns_verified_cited_quotes_and_drops_fabricated_and_self_citations(db_session):
    claim, sections = await _document_with_tree(db_session)
    fake, prompts = _fake_llm(
        ["2"],  # picks the parent — its subsection must be read too
        [
            ExtractedQuote(quote="Revenue for FY24 was $112M, compared with $100M in FY23.", stance="supports", explanation="112 vs 100 is +12%"),
            ExtractedQuote(quote="Revenue for FY24 was $150M.", stance="contradicts", explanation="fabricated"),
            ExtractedQuote(quote=CLAIM_SENTENCE, stance="supports", explanation="the claim itself"),
        ],
    )

    with patch("app.retrieval.vectorless.llm_call_structured", side_effect=fake):
        evidence = await find_section_evidence(db_session, claim, CONFIG)

    assert len(evidence) == 1
    (item,) = evidence
    assert item.source_type == "internal_vectorless"
    # Cited to the deepest section the quote sits in, with its path and page — not the navigated parent.
    assert item.source_ref == f"document_section:{sections['revenue'].id} page 5 section 'Financials > Revenue'"
    assert item.content_snippet.startswith('SUPPORTS: "Revenue for FY24 was $112M')
    assert item.authority_score == 1.0

    quoter_prompt = prompts[1]
    assert "Audited figures follow." in quoter_prompt and "| APAC | $40M |" in quoter_prompt

    traces = (await db_session.execute(select(AgentTrace).where(AgentTrace.claim_id == claim.id))).scalars().all()
    assert sorted(t.agent_name for t in traces) == ["vectorless_navigator", "vectorless_quoter"]


async def test_navigator_finding_nothing_returns_no_evidence(db_session):
    claim, _ = await _document_with_tree(db_session)
    fake, prompts = _fake_llm([], [])

    with patch("app.retrieval.vectorless.llm_call_structured", side_effect=fake):
        evidence = await find_section_evidence(db_session, claim, CONFIG)

    assert evidence == []
    assert len(prompts) == 1  # no section chosen, so no quoting call


async def test_document_without_sections_quotes_from_whole_markdown(db_session):
    document = Document(filename="memo.docx", file_type="docx", storage_path="x", status="ingested",
                        markdown="# Memo\n\nHeadcount was 250 at year end.\n\nHeadcount grew 25%.")
    db_session.add(document)
    await db_session.flush()
    origin = DocumentChunk(document_id=document.id, chunk_type="paragraph", chunk_text="Headcount grew 25%.")
    db_session.add(origin)
    await db_session.flush()
    claim = Claim(document_id=document.id, chunk_id=origin.id, claim_text="Headcount grew 25%.", source_span="Headcount grew 25%.",
                  claim_type="statistical", scope="internal", requires=["headcount"])
    db_session.add(claim)
    await db_session.flush()
    fake, prompts = _fake_llm([], [ExtractedQuote(quote="Headcount was 250 at year end.", stance="context", explanation="end figure")])

    with patch("app.retrieval.vectorless.llm_call_structured", side_effect=fake):
        evidence = await find_section_evidence(db_session, claim, CONFIG)

    assert len(prompts) == 1  # straight to quoting — no outline to navigate
    assert [e.source_ref for e in evidence] == [f"document:{document.id} section 'memo.docx'"]


async def test_disabled_makes_no_llm_calls(db_session):
    claim, _ = await _document_with_tree(db_session)

    with patch("app.retrieval.vectorless.llm_call_structured") as mock_llm:
        evidence = await find_section_evidence(db_session, claim, {**CONFIG, "enabled": False})

    assert evidence == []
    mock_llm.assert_not_called()
