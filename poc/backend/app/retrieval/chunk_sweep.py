from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim, DocumentChunk
from app.retrieval.matching import words

# Last-resort fallback only (see verify_claim.py) — reached when direct lookup, semantic search,
# and the cross-reference navigator have all found nothing. Bundling every chunk into one prompt
# per voter, rather than one LLM call per chunk, keeps this to O(voters) calls instead of
# O(chunks x voters); the cap below keeps a single bundle from blowing a voter model's context
# window on a very large document, at the cost of not literally covering 100% of a huge document
# in that case.
MAX_CHUNK_SWEEP_CHARS = 60_000

# A chunk sentence sharing this much of its content-word vocabulary with the claim itself is
# almost certainly a restatement of the claim (or the claim's own source sentence) rather than
# independent evidence — dropped from the claim's own origin chunk so a voter can't "verify" the
# claim by simply reading the claim back to itself. Deliberately biased toward over-redacting: a
# reworded figure ("£4.2M" vs "4.2 million pounds", "FY24" vs "fiscal year 2024") shares fewer
# literal tokens than a paraphrase-blind threshold would like, and redacting one extra unrelated
# sentence from this one chunk is a far cheaper mistake than leaking the claim's own wording back
# to a voter.
REDACTION_OVERLAP_THRESHOLD = 0.5


def _redact_claim_sentence(chunk_text: str, claim: Claim) -> str:
    """Removes the claim's own wording from its origin chunk before that chunk goes into the
    sweep bundle. Tries an exact (case-insensitive) substring removal first — the common case for
    claims that skipped decomposition and kept the literal source sentence — then falls back to
    dropping whole sentences that are largely the same bag of words as the claim, since a
    decomposed/reworded claim often won't appear verbatim in its source chunk at all."""
    anchor = (claim.source_span or claim.claim_text or "").strip()
    if anchor and anchor.lower() in chunk_text.lower():
        start = chunk_text.lower().index(anchor.lower())
        return chunk_text[:start] + "[claim sentence redacted]" + chunk_text[start + len(anchor):]

    claim_words = words(claim.claim_text)
    if not claim_words:
        return chunk_text

    # Split on sentence-ending punctuation only — good enough for redaction purposes without
    # pulling in the spaCy sentencizer (extract_claims.py's split_sentences) just for this.
    pieces = chunk_text.replace("\n", " \n").split(". ")
    kept = []
    for piece in pieces:
        piece_words = words(piece)
        overlap = len(piece_words & claim_words) / len(claim_words) if piece_words else 0.0
        kept.append("[claim sentence redacted]" if overlap >= REDACTION_OVERLAP_THRESHOLD else piece)
    return ". ".join(kept)


async def build_chunk_sweep_bundle(session: AsyncSession, claim: Claim) -> str:
    """Renders every chunk of the claim's document into one text bundle for the vote panel — the
    claim's own origin chunk has its wording redacted (see _redact_claim_sentence) so a voter
    verifies the claim against the rest of the document, not against the claim restated. Ordering
    is best-effort (page_number, then char_start) since document_chunks has no explicit sequence
    column; a wrong order doesn't affect correctness, only how naturally the bundle reads."""
    chunks = (
        await session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == claim.document_id)
            .order_by(DocumentChunk.page_number.asc().nulls_last(), DocumentChunk.char_start.asc().nulls_last())
        )
    ).scalars().all()

    lines = []
    total_chars = 0
    truncated = False
    for chunk in chunks:
        text = chunk.chunk_text
        if chunk.id == claim.chunk_id:
            text = _redact_claim_sentence(text, claim)

        entry = f"[chunk {chunk.id} | page {chunk.page_number}]\n{text}\n"
        if total_chars + len(entry) > MAX_CHUNK_SWEEP_CHARS:
            truncated = True
            break
        lines.append(entry)
        total_chars += len(entry)

    bundle = "\n".join(lines)
    if truncated:
        bundle += "\n\n... [remaining chunks omitted — document exceeds the sweep size budget]"
    return bundle
