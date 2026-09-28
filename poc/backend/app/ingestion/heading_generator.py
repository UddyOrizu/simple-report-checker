"""LLM-generated section headings for documents that don't carry their own structure.

A document with no headings (or only a title) otherwise falls back to topic-shift pseudo-sections,
which cut on word overlap between consecutive paragraphs — in practice almost every paragraph
becomes its own untitled section. Instead, the model reads the document's paragraphs in order and
decides where the topic genuinely changes and what each part is about, returning headings that are
inserted into the element list as ordinary heading elements (marked `synthetic`). Everything
downstream — chunk context capsules, the structural index, section summaries, and vectorless
retrieval's table of contents — then works exactly as it does for a document with real headings.

Long documents are read in windows of `window_chars`; each window after the first says whether it
opens mid-section, so a window boundary doesn't force an artificial section break.
"""

import logging
from typing import Callable, Literal

from pydantic import BaseModel

from app.llm.client import MissingCredentialsError, llm_call_structured, load_prompt

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = load_prompt("section_headings")


class GeneratedHeading(BaseModel):
    start_index: int
    title: str
    level: Literal[1, 2]


class HeadingPlan(BaseModel):
    continues_previous: bool
    headings: list[GeneratedHeading]


def needs_generated_headings(elements: list[dict], page_count: int, config: dict) -> bool:
    settings = config.get("generated_headings", {})
    if not settings.get("enabled", False):
        return False
    # Short documents skip the structural index entirely (vectorless reads them whole), so there's
    # nothing for generated headings to feed.
    if page_count <= config["short_document_page_threshold"]:
        return False
    real_headings = sum(1 for e in elements if e["type"] == "heading")
    body = sum(1 for e in elements if e["type"] != "heading")
    return real_headings < settings.get("min_real_headings", 2) and body > 1


def _preview(element: dict, max_chars: int) -> str:
    if element["type"] == "table":
        rows = element.get("data") or []
        text = "(table) " + " / ".join(" | ".join(str(c) for c in row) for row in rows[:4])
    else:
        text = element.get("text", "")
    return text if len(text) <= max_chars else text[:max_chars] + " …"


def _windows(body_indices: list[int], elements: list[dict], window_chars: int, preview_chars: int) -> list[list[int]]:
    windows: list[list[int]] = []
    current: list[int] = []
    size = 0
    for idx in body_indices:
        length = len(_preview(elements[idx], preview_chars))
        if current and size + length > window_chars:
            windows.append(current)
            current, size = [], 0
        current.append(idx)
        size += length
    if current:
        windows.append(current)
    return windows


def _validated_starts(plan: HeadingPlan, window: list[int], first_window: bool) -> list[GeneratedHeading]:
    """Keeps only headings that start on a paragraph actually in this window, one per paragraph,
    in document order. The document's (and any non-continuing window's) first paragraph always
    opens a section, so no text ends up before the first heading, outside every section."""
    allowed = set(window)
    by_start: dict[int, GeneratedHeading] = {}
    for heading in sorted(plan.headings, key=lambda h: h.start_index):
        title = heading.title.strip()
        if heading.start_index in allowed and title and heading.start_index not in by_start:
            by_start[heading.start_index] = heading.model_copy(update={"title": title})

    opens_section = first_window or not plan.continues_previous
    if opens_section and window[0] not in by_start:
        if by_start:
            # The model's first heading starts a little way in — pull it back to cover the lead-in.
            first = min(by_start)
            by_start[window[0]] = by_start.pop(first).model_copy(update={"start_index": window[0]})
        else:
            by_start[window[0]] = GeneratedHeading(start_index=window[0], title="Introduction", level=1)
    return [by_start[i] for i in sorted(by_start)]


def _window_note(window_number: int, generated: list[GeneratedHeading]) -> str:
    if window_number == 0:
        return "This is the start of the document."
    if generated:
        return f"This window continues from earlier in the document; the previous part ended in the section titled {generated[-1].title!r}."
    return "This window continues from earlier in the document."


async def add_generated_headings(
    elements: list[dict], config: dict, on_trace: Callable[[str, str], None] | None = None
) -> list[dict]:
    """Returns a new element list with synthetic heading elements inserted, or `elements`
    unchanged when the model can't be called or its output is unusable — the structural index
    then falls back to topic-shift pseudo-sections as before. Existing headings (e.g. a lone
    document title) are kept, and generated headings nest beneath the deepest of them."""
    settings = config.get("generated_headings", {})
    preview_chars = settings.get("paragraph_preview_chars", 800)
    base_level = max((e.get("level") or 1 for e in elements if e["type"] == "heading"), default=0)
    body_indices = [i for i, e in enumerate(elements) if e["type"] != "heading"]

    generated: list[GeneratedHeading] = []
    try:
        for window_number, window in enumerate(_windows(body_indices, elements, settings.get("window_chars", 40000), preview_chars)):
            prompt = _PROMPT_TEMPLATE.format(
                target_section_words=settings.get("target_section_words", 800),
                window_note=_window_note(window_number, generated),
                paragraphs="\n\n".join(f"[{i}] {_preview(elements[i], preview_chars)}" for i in window),
            )
            plan: HeadingPlan = await llm_call_structured(prompt, HeadingPlan, tier=settings.get("tier", "standard"))
            if on_trace:
                on_trace(prompt, plan.model_dump_json())
            generated.extend(_validated_starts(plan, window, first_window=window_number == 0))
    except MissingCredentialsError:
        return elements
    except Exception:
        logger.exception("Heading generation failed — falling back to topic-shift pseudo-sections")
        return elements

    headings_by_index = {h.start_index: h for h in generated}
    result: list[dict] = []
    for i, element in enumerate(elements):
        heading = headings_by_index.get(i)
        if heading is not None:
            synthetic = {"type": "heading", "level": min(base_level + heading.level, 6), "text": heading.title, "synthetic": True}
            if element.get("page_number") is not None:
                synthetic["page_number"] = element["page_number"]
            result.append(synthetic)
        result.append(element)
    return result
