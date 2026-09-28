from unittest.mock import AsyncMock, patch

from app.ingestion.pipeline import summarize_sections

ELEMENTS = [
    {"type": "heading", "level": 1, "text": "Financial Review"},
    {"type": "heading", "level": 2, "text": "Revenue"},
    {"type": "paragraph", "text": "Revenue was $112M."},
    {"type": "heading", "level": 2, "text": "Costs"},
    {"type": "paragraph", "text": "Costs fell 3%."},
]
SECTIONS = [
    {"order_index": 0, "title": "Financial Review", "parent_order_index": None, "start_index": 1, "end_index": 1},
    {"order_index": 1, "title": "Revenue", "parent_order_index": 0, "start_index": 2, "end_index": 3},
    {"order_index": 2, "title": "Costs", "parent_order_index": 0, "start_index": 4, "end_index": 5},
]


async def test_empty_parent_section_gets_outline_summary_without_an_llm_call():
    fake = AsyncMock(return_value={1: "Revenue figures.", 2: "Cost figures."})

    with patch("app.ingestion.pipeline.generate_all_section_summaries", fake):
        summaries, _, blocked = await summarize_sections(SECTIONS, ELEMENTS, config={})

    sent = fake.call_args.args[0]
    assert [s["order_index"] for s in sent] == [1, 2]  # the empty parent is never sent to the model
    assert summaries == {0: "Contains: Revenue; Costs", 1: "Revenue figures.", 2: "Cost figures."}
    assert not blocked
