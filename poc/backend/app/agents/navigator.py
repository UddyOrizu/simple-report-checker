import uuid
import truststore
from dotenv import load_dotenv

from pydantic import BaseModel

from app.llm.client import llm_call_structured, load_prompt

truststore.inject_into_ssl()
load_dotenv()

NAVIGATOR_MODEL_ID = "gpt-4o-mini"

_PROMPT_TEMPLATE = load_prompt("navigator")


class SectionChoice(BaseModel):
    section_id: uuid.UUID | None
    reasoning: str


async def pick_section(
    requires: list[str], candidates: list[tuple[uuid.UUID, str | None, str | None]]
) -> uuid.UUID | None:
    """Reasons over a document's section titles + one-line summaries (Phase 2.5's structural
    index) to pick the section most likely to hold a claim's evidence — the same way a human
    would use a table of contents instead of reading the whole document."""
    candidate_lines = "\n".join(f"- id={section_id} title={title!r} summary={summary!r}" for section_id, title, summary in candidates)
    prompt = _PROMPT_TEMPLATE.format(requires=", ".join(requires), candidates=candidate_lines)
    choice = await llm_call_structured(prompt, SectionChoice, model_id=NAVIGATOR_MODEL_ID)
    return choice.section_id
