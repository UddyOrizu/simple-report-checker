import asyncio
import json
import re
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.decomposer import decompose_sentence
from app.agents.router import route_claim
from app.events.broadcaster import broadcaster
from app.ingestion.sentence_level_chunker import EmbeddingService
from app.llm.client import MissingCredentialsError
from app.models import Claim, DocumentChunk, DocumentSection
from app.nlp.clause_filter import needs_decomposition
from app.nlp.domain_router import classify_domain
from app.nlp.spacy_pipeline import extract_entities, get_sentencizer_nlp
from app.retrieval.internal_index import query_similar
from app.schemas.claim import RoutingDecision

# Bounded fan-out for decompose/route LLM calls within a document — large enough to overlap
# network latency across many claims, small enough not to hammer the provider on a big document.
EXTRACTION_LLM_CONCURRENCY = 8

# Claims from the tail/head of two adjacent DocumentChunks can restate the same fact: the
# ingestion-time embedding chunker overlaps consecutive sentence windows on purpose (see
# CHUNK_OVERLAP_SENTENCES in sentence_level_chunker.py) so retrieval never misses evidence at a
# boundary, but that means the same sentence(s) can appear in two chunks' text and get decomposed
# into claims twice. Treat claims this similar, from consecutive chunks, as duplicates.
DUPLICATE_CLAIM_SIMILARITY_THRESHOLD = 0.92

_BOILERPLATE_SECTION_MARKERS = (
    "disclaimer",
    "table of contents",
    "glossary",
    "definitions",
    "legal notice",
)

# A bare digit isn't enough to call a sentence "statistical" (a year, an ID number) — require a
# metric/trend cue alongside it.
_STATISTICAL_CUES = re.compile(
    r"(%|percent|[$£€]|\bpts\b|basis points|\b(grew|grow|increased|decreased|declined|rose|fell|dropped|up|down)\b)",
    re.IGNORECASE,
)

_sentencizer = get_sentencizer_nlp()


def split_sentences(text: str) -> list[str]:
    doc = _sentencizer(text)
    return [sent.text.strip() for sent in doc.sents if sent.text.strip()]


def direct_claim(sentence: str) -> dict:
    """A simple single-fact sentence skips the decomposition LLM call — there's nothing to split
    out of a single clause. `_direct` marks it downstream so it also skips the routing call and
    defaults scope to "internal", UNLESS it's tagged with a MONEY/PERCENT/DATE/LAW entity (see
    _has_groundable_entity in _finalize below): a hard figure, rate, or date is exactly the kind
    of claim router.md's own rule 2 says must always be externally checked regardless of how well
    the document agrees with itself, so those still get a real routing call despite being
    syntactically simple."""
    has_number = bool(re.search(r"\d", sentence))
    claim_type = "statistical" if has_number and _STATISTICAL_CUES.search(sentence) else "definitional"
    return {
        "text": sentence,
        "claim_type": claim_type,
        "scope": "internal",
        "source_span": sentence,
        "requires": [],
        "_direct": True,
    }


def _is_boilerplate_section(section: DocumentSection | None) -> bool:
    if section is None or not section.title:
        return False
    title = section.title.lower()
    return any(marker in title for marker in _BOILERPLATE_SECTION_MARKERS)


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    a_arr, b_arr = np.array(a), np.array(b)
    denom = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    return float(np.dot(a_arr, b_arr) / denom) if denom else 0.0


def _merge_entities(llm_entities: list[dict], spacy_entities: list[dict]) -> list[dict]:
    """Union the decomposer's self-reported entities with deterministic spaCy NER + gazetteer
    entities, deduplicating by (text, label). The LLM is asked to reproduce spaCy-style entity
    labels from memory, which is less consistent than actually running the gazetteer-augmented
    NER pipeline already loaded for domain classification — but the LLM can still surface things
    the generic NER model has no gazetteer coverage for, so keep both rather than picking one."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for ent in [*llm_entities, *spacy_entities]:
        text = (ent.get("text") or "").strip()
        label = (ent.get("label") or "").strip()
        if not text:
            continue
        key = (text.lower(), label.upper())
        if key in seen:
            continue
        seen.add(key)
        merged.append({"text": text, "label": label})
    return merged


# Entity types router.md's rule 2 says must always be routed externally "regardless of in-document
# matches" — a filed figure, a statutory rate, a regulator's position, a market fact. ORG is
# deliberately excluded even though rule 2 lists it: a bare company-name mention is far weaker
# signal than a number/date/legal reference, and including it would make the fast path fire on
# most sentences in a business document, defeating its purpose.
_GROUNDABLE_ENTITY_LABELS = {"MONEY", "PERCENT", "DATE", "LAW"}


def _has_groundable_entity(entities: list[dict]) -> bool:
    """True if any tagged entity is a type router.md's rule 2 says needs external grounding no
    matter how well the document agrees with itself. Used to keep the direct_claim fast path
    (see below) from silently defaulting exactly these claims to "internal" — a hard number, rate,
    or date is precisely what internal self-consistency cannot establish as true."""
    return any((e.get("label") or "").strip().upper() in _GROUNDABLE_ENTITY_LABELS for e in entities)


def _format_retrieval_context(hits: list[dict]) -> str:
    """Renders query_similar's hits as the "top in-document retrieval hit with its similarity
    score" the router prompt expects — previously never computed before routing at all."""
    if not hits:
        return "In-document retrieval: no similar passages found elsewhere in this document."
    lines = ["In-document retrieval (candidate passages elsewhere in this document):"]
    for hit in hits:
        lines.append(f"- similarity {hit['similarity']:.2f} (page {hit.get('page_number')}): {hit['text']}")
    return "\n".join(lines)


def _table_rows(chunk_text: str) -> tuple[str | None, list[str]]:
    """chunker.py joins table rows with "\\n" and cells with " | ". Returns (header_row_or_None,
    data_rows) — the header (if present) is used as context, never turned into a claim itself."""
    rows = [r.strip() for r in chunk_text.split("\n") if r.strip()]
    if len(rows) <= 1:
        return None, rows
    return rows[0], rows[1:]


async def extract_claims_for_document(session: AsyncSession, document_id: uuid.UUID, registry: list[dict]) -> list[Claim]:
    """Walks every chunk of a document (prose and tables alike), splits prose into sentences,
    decomposes each sentence/table-row into claims, classifies each claim's domain, retrieves
    in-document evidence and routes each claim's verification scope, and persists the results.
    Publishes a claim_extracted event per claim, and commits incrementally per chunk so a run
    killed partway through a large document doesn't lose everything extracted so far.

    A simple single-fact sentence skips both the decomposition and routing LLM calls (the
    direct_claim heuristic); a sentence flagged is_opinion_or_unverifiable by the decomposer also
    skips routing. Everything else runs through decompose_sentence and route_claim, gathered with
    bounded concurrency rather than one call at a time. Degrades gracefully — not a crash — when
    ANTHROPIC_API_KEY/OPENAI_API_KEY is missing: LLM-dependent claims for that sentence are
    skipped rather than retried.
    """
    chunks = (await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == document_id))).scalars().all()

    persisted: list[Claim] = []
    embedding_service = EmbeddingService()
    sem = asyncio.Semaphore(EXTRACTION_LLM_CONCURRENCY)

    # Best-effort continuity across chunks — document_chunks has no explicit sequence column, so
    # this only helps when the DB happens to return chunks in insertion (document) order, as it
    # does in practice for a fresh, uncontended scan. Worst case it's a no-op, not a correctness bug:
    # a wrong "preceding sentence" hint just gives the decomposer slightly less useful context, and
    # comparing dedup embeddings against the wrong chunk just means an occasional missed duplicate,
    # never a false drop (0.92 cosine similarity between genuinely unrelated claims is vanishingly rare).
    preceding_sentence: str | None = None
    prev_chunk_claim_embeddings: list[list[float]] = []

    for chunk in chunks:
        section = await session.get(DocumentSection, chunk.section_id) if chunk.section_id else None
        if _is_boilerplate_section(section):
            preceding_sentence = None
            prev_chunk_claim_embeddings = []
            continue

        is_table = chunk.chunk_type == "table"
        if is_table:
            header, rows = _table_rows(chunk.chunk_text)
            items = [(row, True) for row in rows]
        else:
            sentences = await asyncio.to_thread(split_sentences, chunk.chunk_text)
            items = [(s, False) for s in sentences]
            header = None

        async def _decompose_item(index: int, text: str, is_row: bool) -> list[dict] | None:
            if is_row:
                local_context = f"{chunk.chunk_text}\nTable header: {header}" if header else chunk.chunk_text
                needs_llm = True
            else:
                prior = items[index - 1][0] if index > 0 else preceding_sentence
                local_context = chunk.chunk_text 
                if prior:
                    local_context += (
                        f'\nPreceding sentence (context only, for resolving pronouns/references — '
                        f'do not extract claims from it): "{prior}"'
                    )
                needs_llm = await asyncio.to_thread(needs_decomposition, text)

            if not needs_llm:
                return [direct_claim(text)]

            async with sem:
                try:
                    claim_list = await decompose_sentence(text, local_context)
                except MissingCredentialsError:
                    return None
            return [c.model_dump() for c in claim_list.claims]

        decomposed = await asyncio.gather(*(_decompose_item(i, text, is_row) for i, (text, is_row) in enumerate(items)))

        if items and not is_table:
            preceding_sentence = items[-1][0]

        claim_data_list = [c for group in decomposed if group is not None for c in group]
        if not claim_data_list:
            prev_chunk_claim_embeddings = []
            continue

        claim_texts = [c["text"] for c in claim_data_list]
        claim_embeddings = await embedding_service.embed_texts(claim_texts)

        survivors: list[tuple[dict, list[float]]] = []
        for claim_data, embedding in zip(claim_data_list, claim_embeddings):
            is_duplicate = any(
                _cosine(embedding, prev_embedding) >= DUPLICATE_CLAIM_SIMILARITY_THRESHOLD
                for prev_embedding in prev_chunk_claim_embeddings
            )
            if not is_duplicate:
                survivors.append((claim_data, embedding))
        prev_chunk_claim_embeddings = [embedding for _, embedding in survivors]

        async def _finalize(claim_data: dict, embedding: list[float]):
            domain_result = await asyncio.to_thread(classify_domain, claim_data["text"], section, registry)
            spacy_entities = await asyncio.to_thread(extract_entities, claim_data["text"])
            merged_entities = _merge_entities(claim_data.get("entities", []) or [], spacy_entities)

            if claim_data.get("_direct") and not _has_groundable_entity(merged_entities):
                route = RoutingDecision(
                    claim_id="",
                    route=claim_data.get("scope", "internal"),
                    confidence=1.0,
                    reasoning="Single-fact sentence resolved via the no-LLM heuristic path — routing call skipped.",
                    suggested_search_queries=[],
                )
            elif claim_data.get("is_opinion_or_unverifiable"):
                route = RoutingDecision(
                    claim_id="",
                    route="unverifiable",
                    confidence=1.0,
                    reasoning="Flagged is_opinion_or_unverifiable at extraction — routing call skipped.",
                    suggested_search_queries=[],
                )
            else:
                hits = await query_similar(session, document_id, embedding, top_k=3, exclude_chunk_id=chunk.id)
                context = (
                    f"{json.dumps({k: v for k, v in claim_data.items() if not k.startswith('_')}, indent=2)}\n"
                    f"domain: {domain_result['domain']} domain confidence: {domain_result['confidence']} "
                    f"domain source: {domain_result['source']}\n{_format_retrieval_context(hits)}"
                )
                async with sem:
                    try:
                        route = await route_claim(claim_data["text"], context)
                    except MissingCredentialsError:
                        return None

            return claim_data, embedding, domain_result, merged_entities, route

        finalized = await asyncio.gather(*(_finalize(cd, emb) for cd, emb in survivors))

        for result in finalized:
            if result is None:
                continue
            claim_data, embedding, domain_result, merged_entities, route = result
            claim = Claim(
                document_id=document_id,
                chunk_id=chunk.id,
                claim_text=claim_data["text"],
                source_span=claim_data["source_span"],
                claim_type=claim_data["claim_type"],
                scope=route.route,
                requires=claim_data["requires"],
                domain=domain_result["domain"],
                domain_confidence=domain_result["confidence"],
                domain_source=domain_result["source"],
                cites_external_source=claim_data.get("cites_external_source", False),
                is_opinion_or_unverifiable=claim_data.get("is_opinion_or_unverifiable", False),
                routing_decision=route.reasoning,
                suggested_search_queries=route.suggested_search_queries,
                entities=merged_entities,
                embedding=embedding,
            )
            session.add(claim)
            await session.flush()
            persisted.append(claim)

            await broadcaster.publish(
                document_id,
                {
                    "event": "claim_extracted",
                    "claim_id": str(claim.id),
                    "claim_text": claim.claim_text,
                    "claim_type": claim.claim_type,
                    "scope": claim.scope,
                },
            )

        await session.commit()

    return persisted
