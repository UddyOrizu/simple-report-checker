"""Vectorless RAG over the document's own section tree (after UddyOrizu/simple-vectorless-rag).

No embeddings: instead of retrieving chunks by similarity, the model reads the structural index
the way a person reads a table of contents — titles + one-line summaries, never full text up front
— and reasons about which section holds the evidence for a claim. Then:

  1. navigate  — pick up to `max_sections` sections from the outline (mini tier)
  2. read      — load each chosen section's Markdown content, subsections included
  3. quote     — extract verbatim passages that support / contradict the claim (standard tier)
  4. verify    — keep only quotes that actually appear in the section text (quote_match), and
                 drop any that are just the claim's own sentence quoted back
  5. cite      — each surviving quote becomes an Evidence row citing its section path and page

Documents too short for a structural index skip step 1 and quote from the whole-document Markdown.
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Literal

import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_hash import compute_config_hash
from app.llm.client import MissingCredentialsError, llm_call_structured, load_prompt
from app.models import AgentTrace, Claim, Document, DocumentChunk, DocumentSection, Evidence
from app.retrieval.matching import citation, words
from app.retrieval.quote_match import locate_quote, normalize

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "retrieval.yaml")

_NAVIGATOR_PROMPT = load_prompt("vectorless_navigator")
_QUOTER_PROMPT = load_prompt("vectorless_quoter")

# A quote whose content words are nearly all the claim's own words is a restatement of the claim,
# not independent evidence for it.
SELF_CITATION_WORD_OVERLAP = 0.8
# How many of a quote's opening words are used to find the chunk (and so the page) it starts in.
PAGE_ANCHOR_WORDS = 8


class NavigationChoice(BaseModel):
    section_ids: list[str]
    reasoning: str


class ExtractedQuote(BaseModel):
    quote: str
    stance: Literal["supports", "contradicts", "context"]
    explanation: str


class QuoteExtraction(BaseModel):
    quotes: list[ExtractedQuote]


@dataclass
class _Node:
    section: DocumentSection
    outline_id: str
    path: list[str]
    children: list["_Node"] = field(default_factory=list)

    def flatten(self) -> list["_Node"]:
        nodes = [self]
        for child in self.children:
            nodes.extend(child.flatten())
        return nodes

    def full_text(self) -> str:
        """This section's content followed by every subsection's, each under its own heading."""
        parts = [self.section.content or ""]
        for child in self.children:
            heading = "#" * min(child.section.level or 1, 6) + " " + (child.section.title or "")
            parts.append(f"{heading}\n\n{child.full_text()}")
        return "\n\n".join(p.strip() for p in parts if p.strip())


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)["vectorless_rag"]


def _build_tree(sections: list[DocumentSection]) -> list[_Node]:
    """Sections arrive in order_index order, so a parent is always built before its children.
    Sections with no parent_id (top-level headings, pseudo-sections, and every section of a
    document indexed before section levels existed) become roots."""
    nodes_by_id: dict[uuid.UUID, _Node] = {}
    roots: list[_Node] = []
    for section in sections:
        parent = nodes_by_id.get(section.parent_id) if section.parent_id else None
        siblings = parent.children if parent else roots
        title = section.title or f"(untitled part, pages {section.page_start}-{section.page_end})"
        node = _Node(
            section=section,
            outline_id=f"{parent.outline_id}.{len(siblings) + 1}" if parent else str(len(siblings) + 1),
            path=[*(parent.path if parent else []), title],
        )
        siblings.append(node)
        nodes_by_id[section.id] = node
    return roots


def _outline(roots: list[_Node]) -> str:
    lines = []
    for root in roots:
        for node in root.flatten():
            indent = "  " * (len(node.path) - 1)
            lines.append(f"{indent}- [{node.outline_id}] {node.path[-1]}: {node.section.summary or '(no summary)'}")
    return "\n".join(lines)


def _is_self_citation(quote: str, claim: Claim) -> bool:
    anchor = normalize(claim.source_span or claim.claim_text or "")
    norm_quote = normalize(quote)
    if anchor and (anchor in norm_quote or norm_quote in anchor):
        return True
    quote_words = words(quote)
    return bool(quote_words) and len(quote_words & words(claim.claim_text)) / len(quote_words) >= SELF_CITATION_WORD_OVERLAP


def _page_for_quote(quote: str, chunks: list[DocumentChunk], fallback: int | None) -> int | None:
    """The page a quote starts on: the chunk containing the quote's opening words. Chunks are
    sentence-level, so a multi-sentence quote won't sit inside any single chunk — its first few
    words will."""
    anchor = normalize(" ".join(quote.split()[:PAGE_ANCHOR_WORDS]))
    for chunk in chunks:
        if anchor and anchor in normalize(chunk.chunk_text):
            return chunk.page_number
    return fallback


async def _navigate(claim: Claim, roots: list[_Node], max_sections: int) -> tuple[list[_Node], str, str]:
    prompt = _NAVIGATOR_PROMPT.format(
        claim_text=claim.claim_text,
        requires=", ".join(claim.requires or []) or "(not specified)",
        outline=_outline(roots),
        max_sections=max_sections,
    )
    choice: NavigationChoice = await llm_call_structured(prompt, NavigationChoice, tier="mini")
    by_outline_id = {node.outline_id: node for root in roots for node in root.flatten()}
    chosen: list[_Node] = []
    for section_id in choice.section_ids:
        node = by_outline_id.get(section_id.strip().strip("[]"))
        # Picking a parent already reads its subsections — drop a pick that's inside one already chosen.
        if node is not None and not any(node is n for picked in chosen for n in picked.flatten()):
            chosen.append(node)
    return chosen[:max_sections], prompt, choice.model_dump_json()


async def _extract_quotes(claim: Claim, section_path: str, text: str, config: dict) -> tuple[list[ExtractedQuote], str, str]:
    prompt = _QUOTER_PROMPT.format(
        claim_text=claim.claim_text,
        claim_source=claim.source_span or claim.claim_text,
        section_path=section_path,
        section_text=text,
        max_quotes=config["max_quotes_per_section"],
    )
    extraction: QuoteExtraction = await llm_call_structured(prompt, QuoteExtraction, tier="standard")
    return extraction.quotes[: config["max_quotes_per_section"]], prompt, extraction.model_dump_json()


def _trace(claim: Claim, agent_name: str, prompt: str, response: str, config_hash: str) -> AgentTrace:
    return AgentTrace(
        claim_id=claim.id, agent_name=agent_name, prompt_sent=prompt, raw_response=response, tool_calls=None, config_hash=config_hash
    )


async def find_section_evidence(session: AsyncSession, claim: Claim, config: dict | None = None) -> list[Evidence]:
    """Returns cited, source-verified quotes bearing on `claim` (both supporting and
    contradicting — the verifier weighs them), or [] when nothing verifiable was found. The
    navigator/quoter prompts and responses are added to `session` as AgentTrace rows, committed
    alongside the claim's verdict."""
    config = config or load_config()
    if not config.get("enabled", True):
        return []

    max_chars = config["max_section_chars"]
    config_hash = compute_config_hash()
    sections = (
        await session.execute(
            select(DocumentSection).where(DocumentSection.document_id == claim.document_id).order_by(DocumentSection.order_index)
        )
    ).scalars().all()

    # (nodes the quote may come from, citation label for the whole read, text read)
    reads: list[tuple[list[_Node], str, str]] = []
    try:
        if sections and any(s.content for s in sections):
            roots = _build_tree(list(sections))
            chosen, nav_prompt, nav_response = await _navigate(claim, roots, config["max_sections"])
            session.add(_trace(claim, "vectorless_navigator", nav_prompt, nav_response, config_hash))
            reads = [(node.flatten(), " > ".join(node.path), node.full_text()[:max_chars]) for node in chosen]
        else:
            document = await session.get(Document, claim.document_id)
            if document is not None and document.markdown:
                reads = [([], document.filename, document.markdown[:max_chars])]

        reads = [read for read in reads if read[2].strip()]
        results = await asyncio.gather(*(_extract_quotes(claim, label, text, config) for _, label, text in reads))
    except MissingCredentialsError:
        raise
    except Exception:
        # Retrieval is one rung of an escalating ladder — a malformed model response here should
        # fall through to the next rung, not fail the claim's whole verification.
        logger.exception("Vectorless retrieval failed for claim %s", claim.id)
        return []

    doc_chunks = (
        await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == claim.document_id))
    ).scalars().all()

    evidence: list[Evidence] = []
    seen: set[str] = set()
    for (nodes, label, text), (quotes, quote_prompt, quote_response) in zip(reads, results):
        session.add(_trace(claim, "vectorless_quoter", quote_prompt, quote_response, config_hash))
        for item in quotes:
            match = locate_quote(text, item.quote)
            if match.score < config["quote_match_threshold"]:
                logger.info("Dropping unverified quote for claim %s (score %.2f): %r", claim.id, match.score, item.quote)
                continue
            if _is_self_citation(item.quote, claim) or normalize(item.quote) in seen:
                continue
            seen.add(normalize(item.quote))

            # Cite the most specific section the quote actually sits in, not just the one navigated to.
            best = max(nodes, key=lambda n: locate_quote(n.section.content or "", item.quote).score, default=None)
            if best is not None:
                section_ids = {best.section.id}
                ref = f"document_section:{best.section.id}"
                path = " > ".join(best.path)
                fallback_page = best.section.page_start
            else:
                section_ids, ref, path, fallback_page = None, f"document:{claim.document_id}", label, None
            candidate_chunks = [c for c in doc_chunks if section_ids is None or c.section_id in section_ids]
            page = _page_for_quote(item.quote, candidate_chunks, fallback_page)

            evidence.append(
                Evidence(
                    claim_id=claim.id,
                    source_type="internal_vectorless",
                    source_ref=citation(ref, page, path),
                    content_snippet=f'{item.stance.upper()}: "{item.quote}" — {item.explanation}',
                    authority_score=match.score,
                )
            )
    return evidence
