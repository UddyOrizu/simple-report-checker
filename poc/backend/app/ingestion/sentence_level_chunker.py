import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
import asyncpg
import spacy
import tiktoken
from openai import AsyncOpenAI

from app.nlp.spacy_pipeline import get_sentencizer_nlp


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

EMBEDDING_MODEL = "text-embedding-3-small"   # 1536-dim; use "text-embedding-3-large" (3072-dim) for higher recall
EMBEDDING_DIM = 1536
EMBEDDING_MAX_TOKENS = 8191                  # per-input hard limit for OpenAI embeddings
EMBEDDING_BATCH_MAX_INPUTS = 100             # conservative batch size, well under the API's own cap
EMBEDDING_BATCH_MAX_TOKENS = 250_000         # conservative total-tokens-per-request budget

CHUNK_SENTENCES = 10          # sentences per embedding chunk
CHUNK_OVERLAP_SENTENCES = 2  # overlap (in sentences) between consecutive embedding chunks

EXTRACTION_WINDOW_TOKENS = 3000       # token budget per Extractor Agent call
EXTRACTION_WINDOW_OVERLAP_CHUNKS = 1  # embedding-chunks of overlap between extraction windows

CLAIM_DEDUP_SIMILARITY_THRESHOLD = 0.92  # cosine similarity above which two claims are treated as duplicates

_tokenizer = tiktoken.get_encoding("cl100k_base")
_nlp = get_sentencizer_nlp()  # sentence boundaries only — no need for the full trf pipeline here


def count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text))

# ----------------------------------------------------------------------------
# Sentence-based chunking with overlap
# ----------------------------------------------------------------------------

@dataclass
class Sentence:
    index: int
    text: str
    char_start: int
    char_end: int
    page: Optional[int] = None


@dataclass
class Chunk:
    chunk_index: int
    text: str
    start_sentence_index: int
    end_sentence_index: int
    page: Optional[int] = None
    embedding: Optional[List[float]] = None


def split_sentences(document_text: str) -> List[Sentence]:
    """
    Split document text into sentences using spaCy, preserving character offsets
    so chunks can be mapped back to page numbers.

    `page_map` (optional): list of (char_offset, page_number) marking where each
    page begins, if you've preserved that during PDF/docx text extraction. If
    omitted, `page` on each Sentence is left None.
    """
    doc = _nlp(document_text)
    sentences: List[Sentence] = []
    for i, sent in enumerate(doc.sents):
        text = sent.text.strip()
        if not text:
            continue
        
        sentences.append(Sentence(index=i, text=text, char_start=sent.start_char, char_end=sent.end_char, page=1))
    return sentences




def chunk_sentences(
    sentences: List[Sentence],
    chunk_size: int = CHUNK_SENTENCES,
    overlap: int = CHUNK_OVERLAP_SENTENCES,
) -> List[Chunk]:
    """
    Group sentences into overlapping windows for embedding.

    Overlap is measured in SENTENCES, not characters or tokens — this keeps
    chunk boundaries semantically clean (a chunk never splits mid-sentence)
    while still giving the retriever context spanning a boundary, so evidence
    that straddles two sentences near a cut point isn't missed by either chunk.

    A single very long sentence (e.g. a dense table row rendered as flat text)
    is additionally guarded against the embedding model's token limit via
    `_guard_token_limit`.
    """
    if not sentences:
        return []
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: List[Chunk] = []
    step = chunk_size - overlap
    i = 0
    chunk_index = 0
    n = len(sentences)

    while i < n:
        window = sentences[i : i + chunk_size]
        if not window:
            break
        text = _guard_token_limit(" ".join(s.text for s in window))
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                text=text,
                start_sentence_index=window[0].index,
                end_sentence_index=window[-1].index,
                page=window[0].page,
            )
        )
        chunk_index += 1
        if i + chunk_size >= n:
            break
        i += step

    return chunks


def _guard_token_limit(text: str, max_tokens: int = EMBEDDING_MAX_TOKENS - 100) -> str:
    """Defensive truncation if a chunk somehow exceeds the embedding token limit."""
    tokens = _tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _tokenizer.decode(tokens[:max_tokens])


# ----------------------------------------------------------------------------
# OpenAI embeddings — batched, large-document safe
# ----------------------------------------------------------------------------

class EmbeddingService:
    """
    Wraps OpenAI embeddings with token-aware batching, bounded concurrency, and
    retry-with-backoff, so large documents (hundreds of chunks) don't blow
    per-request limits or fail outright on a transient rate-limit error.
    """

    def __init__(self, client: Optional[AsyncOpenAI] = None, model: str = EMBEDDING_MODEL):
        self.client = client or AsyncOpenAI()
        self.model = model

    async def embed_chunks(self, chunks: List[Chunk], max_concurrency: int = 4) -> List[Chunk]:
        batches = self._build_batches(chunks)
        semaphore = asyncio.Semaphore(max_concurrency)

        async def run_batch(batch: List[Chunk]):
            async with semaphore:
                await self._embed_batch(batch)

        await asyncio.gather(*(run_batch(b) for b in batches))
        return chunks

    def _build_batches(self, chunks: List[Chunk]) -> List[List[Chunk]]:
        """Pack chunks into batches respecting both input-count and total-token limits."""
        batches: List[List[Chunk]] = []
        current: List[Chunk] = []
        current_tokens = 0

        for chunk in chunks:
            t = count_tokens(chunk.text)
            would_exceed = (
                len(current) >= EMBEDDING_BATCH_MAX_INPUTS
                or current_tokens + t > EMBEDDING_BATCH_MAX_TOKENS
            )
            if would_exceed and current:
                batches.append(current)
                current, current_tokens = [], 0
            current.append(chunk)
            current_tokens += t

        if current:
            batches.append(current)
        return batches

    async def _embed_batch(self, batch: List[Chunk], max_retries: int = 5) -> None:
        texts = [c.text for c in batch]
        delay = 1.0
        for attempt in range(max_retries):
            try:
                response = await self.client.embeddings.create(model=self.model, input=texts)
                for chunk, item in zip(batch, response.data):
                    chunk.embedding = item.embedding
                return
            except Exception:  # narrow to openai.RateLimitError / APIError in production
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(delay)
                delay *= 2  # exponential backoff

    async def embed_text(self, text: str) -> List[float]:
        """Single-text embedding — used to embed one claim at routing/query time, where a
        standalone lookup embedding is genuinely needed on its own. For embedding many texts at
        once (a document's chunks, a batch of extracted claims), use embed_texts instead — one
        HTTP round trip per batch instead of one per text."""
        text = _guard_token_limit(text)
        response = await self.client.embeddings.create(model=self.model, input=[text])
        return response.data[0].embedding

    async def embed_texts(self, texts: List[str], max_concurrency: int = 4) -> List[List[float]]:
        """Batched embedding for a list of plain strings, in original order. Thin wrapper around
        embed_chunks (which already implements token-aware batching, bounded concurrency, and
        retry-with-backoff) for callers that just have raw text, not Chunk dataclasses — document
        ingestion's chunk list and a document's extracted claims are both this shape."""
        if not texts:
            return []
        wrapped = [Chunk(chunk_index=i, text=t, start_sentence_index=0, end_sentence_index=0) for i, t in enumerate(texts)]
        await self.embed_chunks(wrapped, max_concurrency=max_concurrency)
        return [c.embedding for c in wrapped]

