from unittest.mock import patch

from app.ingestion.heading_generator import (
    GeneratedHeading,
    HeadingPlan,
    add_generated_headings,
    needs_generated_headings,
)
from app.ingestion.structural_index import build_structural_index
from app.llm.client import MissingCredentialsError

CONFIG = {
    "short_document_page_threshold": 5,
    "topic_shift_sensitivity": 0.35,
    "generated_headings": {
        "enabled": True,
        "min_real_headings": 2,
        "target_section_words": 800,
        "window_chars": 40000,
        "paragraph_preview_chars": 800,
        "tier": "standard",
    },
}


def _paragraphs(n: int, page_every: int = 2) -> list[dict]:
    return [{"type": "paragraph", "text": f"Paragraph {i} text.", "page_number": i // page_every + 1} for i in range(n)]


def _fake_llm(*plans: HeadingPlan):
    prompts: list[str] = []
    queue = list(plans)

    async def fake(prompt, output_schema, tier="standard"):
        assert output_schema is HeadingPlan
        prompts.append(prompt)
        return queue.pop(0)

    return fake, prompts


def test_needs_headings_only_for_long_documents_lacking_structure():
    assert needs_generated_headings(_paragraphs(6), page_count=10, config=CONFIG)
    assert not needs_generated_headings(_paragraphs(6), page_count=3, config=CONFIG)  # short: read whole
    title_only = [{"type": "heading", "level": 1, "text": "Report"}, *_paragraphs(6)]
    assert needs_generated_headings(title_only, page_count=10, config=CONFIG)
    structured = [{"type": "heading", "level": 1, "text": "A"}, *_paragraphs(3), {"type": "heading", "level": 1, "text": "B"}]
    assert not needs_generated_headings(structured, page_count=10, config=CONFIG)
    disabled = {**CONFIG, "generated_headings": {**CONFIG["generated_headings"], "enabled": False}}
    assert not needs_generated_headings(_paragraphs(6), page_count=10, config=disabled)


async def test_inserts_synthetic_headings_that_become_titled_sections():
    elements = _paragraphs(6)
    fake, prompts = _fake_llm(HeadingPlan(continues_previous=False, headings=[
        GeneratedHeading(start_index=0, title="Market overview", level=1),
        GeneratedHeading(start_index=2, title="APAC growth drivers", level=2),
        GeneratedHeading(start_index=4, title="Cost programme", level=1),
    ]))

    with patch("app.ingestion.heading_generator.llm_call_structured", side_effect=fake):
        result = await add_generated_headings(elements, CONFIG)

    headings = [(e["text"], e["level"], e["page_number"]) for e in result if e["type"] == "heading"]
    assert headings == [("Market overview", 1, 1), ("APAC growth drivers", 2, 2), ("Cost programme", 1, 3)]
    assert "[0] Paragraph 0 text." in prompts[0] and "[5] Paragraph 5 text." in prompts[0]

    sections = build_structural_index(10, result, CONFIG)
    assert [(s["title"], s["parent_order_index"], s["is_pseudo_section"]) for s in sections] == [
        ("Market overview", None, True),
        ("APAC growth drivers", 0, True),
        ("Cost programme", None, True),
    ]


async def test_invalid_indices_dropped_and_first_section_covers_the_lead_in():
    elements = _paragraphs(5)
    fake, _ = _fake_llm(HeadingPlan(continues_previous=False, headings=[
        GeneratedHeading(start_index=1, title="Background", level=1),  # skips paragraph 0 — pulled back
        GeneratedHeading(start_index=99, title="Hallucinated", level=1),  # not a real paragraph
        GeneratedHeading(start_index=3, title="  ", level=1),  # blank title
        GeneratedHeading(start_index=3, title="Findings", level=1),
    ]))

    with patch("app.ingestion.heading_generator.llm_call_structured", side_effect=fake):
        result = await add_generated_headings(elements, CONFIG)

    assert [(i, e["text"]) for i, e in enumerate(result) if e["type"] == "heading"] == [(0, "Background"), (4, "Findings")]
    assert result[1]["text"] == "Paragraph 0 text."  # no text left before the first heading


async def test_later_window_that_continues_previous_section_gets_no_forced_heading():
    elements = _paragraphs(4)
    config = {**CONFIG, "generated_headings": {**CONFIG["generated_headings"], "window_chars": 40}}  # 2 paragraphs per window
    fake, prompts = _fake_llm(
        HeadingPlan(continues_previous=False, headings=[GeneratedHeading(start_index=0, title="Opening", level=1)]),
        HeadingPlan(continues_previous=True, headings=[GeneratedHeading(start_index=3, title="Next topic", level=1)]),
    )

    with patch("app.ingestion.heading_generator.llm_call_structured", side_effect=fake):
        result = await add_generated_headings(elements, config)

    assert len(prompts) == 2
    assert "previous part ended in the section titled 'Opening'" in prompts[1]
    assert [e["text"] for e in result if e["type"] == "heading"] == ["Opening", "Next topic"]


async def test_generated_headings_nest_under_an_existing_title():
    elements = [{"type": "heading", "level": 1, "text": "Annual Report"}, *_paragraphs(3)]
    fake, _ = _fake_llm(HeadingPlan(continues_previous=False, headings=[GeneratedHeading(start_index=1, title="Results", level=1)]))

    with patch("app.ingestion.heading_generator.llm_call_structured", side_effect=fake):
        result = await add_generated_headings(elements, CONFIG)

    assert [(e["text"], e["level"]) for e in result if e["type"] == "heading"] == [("Annual Report", 1), ("Results", 2)]


async def test_falls_back_to_unchanged_elements_on_llm_failure_or_missing_credentials():
    elements = _paragraphs(4)
    for error in (RuntimeError("bad response"), MissingCredentialsError("no key")):
        with patch("app.ingestion.heading_generator.llm_call_structured", side_effect=error):
            assert await add_generated_headings(elements, CONFIG) is elements
