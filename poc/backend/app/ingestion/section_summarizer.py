import asyncio
from typing import Callable

from app.llm.client import llm_call, load_prompt

_PROMPT_TEMPLATE = load_prompt("section_summarizer")


async def summarize_text(
    text: str, section_title: str, max_words: int, on_trace: Callable[[str, str], None] | None = None
) -> str:
    prompt = _PROMPT_TEMPLATE.format(max_words=max_words, section_title=section_title, text=text)
    response = await llm_call(prompt)
    if on_trace:
        on_trace(prompt, response)
    return response


def _split_into_batches(chunks: list[str], max_words_per_batch: int) -> list[list[str]]:
    """Greedily pack chunks into batches that each stay at/under the word limit, never splitting
    a chunk across batches."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_words = 0

    for chunk in chunks:
        words = len(chunk.split())
        if current and current_words + words > max_words_per_batch:
            batches.append(current)
            current, current_words = [], 0
        current.append(chunk)
        current_words += words

    if current:
        batches.append(current)
    return batches


async def generate_section_summary(
    section_chunks: list[str],
    section_title: str,
    config: dict,
    on_trace: Callable[[str, str], None] | None = None,
) -> str:
    """Summarize a section's chunks in at most `section_summary_max_words` words. Sections small
    enough go through one LLM call; larger sections are batched, each batch summarized
    concurrently, then the batch summaries are reduced into one final summary — recursively, if
    even the combined batch summaries are still too large."""
    full_text = "\n".join(section_chunks)
    max_words = config["section_summary_max_words"]
    direct_limit = config["section_summary_direct_word_limit"]

    if len(full_text.split()) <= direct_limit:
        return await summarize_text(full_text, section_title, max_words, on_trace)

    batches = _split_into_batches(section_chunks, max_words_per_batch=direct_limit)
    batch_summaries = await asyncio.gather(
        *[summarize_text("\n".join(b), section_title, max_words, on_trace) for b in batches]
    )

    combined = "\n".join(batch_summaries)
    if len(combined.split()) <= direct_limit:
        return await summarize_text(combined, section_title, max_words, on_trace)

    return await generate_section_summary(list(batch_summaries), section_title, config, on_trace)


async def generate_all_section_summaries(
    sections: list[dict],
    config: dict,
    concurrency: int = 8,
    on_trace: Callable[[int, str, str], None] | None = None,
) -> dict[int, str]:
    """sections: [{"order_index": int, "title": str | None, "chunks": list[str]}, ...].
    Runs concurrently across sections, capped at `concurrency` in flight, rather than
    sequentially — a document with dozens of sections would otherwise summarize one at a time."""
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(section: dict) -> tuple[int, str]:
        async with semaphore:
            trace = (lambda p, r, oi=section["order_index"]: on_trace(oi, p, r)) if on_trace else None
            title = section.get("title") or "Untitled section"
            summary = await generate_section_summary(section["chunks"], title, config, trace)
            return section["order_index"], summary

    results = await asyncio.gather(*[_bounded(s) for s in sections])
    return dict(results)
