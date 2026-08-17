# Claim Validation POC — Implementation Plan (for Claude Code, Self-Contained)

This document assumes Claude Code has access to **only this file**. Nothing here references an external design document — schema, repo structure, ingestion logic, extraction logic, the internal/external claim classification, agent/tool design, and every phase's tasks are all inlined below in full.

## Purpose

Build a proof of concept that: uploads a Word or PDF document, processes it through an AI pipeline that extracts factual claims and checks each one against the document itself and, where needed, external sources, and shows the results in a review UI — live, as claims resolve. No authentication, no multi-tenancy, no deployment infrastructure. The system is deliberately built so every stage — ingestion, extraction, classification, retrieval, verification — is config-driven, independently runnable via a standalone script, and fully traceable, so it can be rapidly tuned.

## Rules for working through this plan

1. Work phase by phase, in order. Don't start a task whose dependency isn't checked off.
2. A task isn't done until its **Verify** step passes — run the command, don't eyeball the code.
3. Write tests/verification alongside implementation in the same task, not deferred.
4. Run the full phase verification gate before starting the next phase, then commit: `poc-phase-N: <phase name> complete`.
5. Check installed library APIs (especially Agno, which changes quickly) against their actual current docs before trusting any code sample here verbatim — treat every snippet below as showing the intended shape and logic, not guaranteed-current syntax.
6. **This POC should generate close to zero credential blockers.** Auth and real external data providers are out of scope or mocked. If a task unexpectedly needs a real credential anyway, mark it `[BLOCKED-CREDENTIALS]` in this file and continue with everything else.
7. **Config and prompt files must contain real, working content — never placeholders.** An empty `config/thresholds.yaml` or a stub `prompts/verifier.md` defeats the purpose of this build. This is checked explicitly at multiple phase gates.

---

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI | async |
| Agent orchestration | Agno | agents, tools, teams |
| NLP | spaCy (`en_core_web_trf`) | sentence segmentation, NER, custom `EntityRuler` |
| Database | PostgreSQL + `pgvector` extension | |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | |
| Background work | FastAPI `BackgroundTasks` / asyncio | **no job queue, no Redis, no ARQ, no Celery** — unnecessary for a single-user POC |
| Live updates | In-process `asyncio.Queue` pub/sub → SSE | swap for Redis pub/sub only if this ever needs multi-instance scaling |
| Word parsing | `python-docx` | |
| PDF parsing | PyMuPDF (`fitz`) + `pdfplumber` | |
| OCR | `pytesseract` + `pdf2image` (needs Tesseract + poppler installed) | |
| Config format | YAML (`pyyaml`) | |
| Frontend | React + TypeScript + Vite | `@tanstack/react-query`, Tailwind |
| Auth | none | out of scope |

---

## Repository structure

```
poc/
├── backend/
│   ├── app/
│   │   ├── api/                      # FastAPI routers
│   │   │   ├── documents.py
│   │   │   ├── claims.py
│   │   │   └── events.py
│   │   ├── ingestion/
│   │   │   ├── parsers/
│   │   │   │   ├── docx_parser.py
│   │   │   │   ├── pdf_parser.py
│   │   │   │   └── ocr.py
│   │   │   ├── chunker.py
│   │   │   └── structural_index.py
│   │   ├── nlp/
│   │   │   ├── spacy_pipeline.py
│   │   │   └── domain_router.py
│   │   ├── agents/
│   │   │   ├── decomposer.py
│   │   │   ├── deterministic_verifier.py
│   │   │   ├── verifier.py
│   │   │   ├── challenger.py
│   │   │   ├── reconcile.py
│   │   │   └── tools/
│   │   │       ├── citation_check.py
│   │   │       └── internal_lookup.py
│   │   ├── retrieval/
│   │   │   ├── internal_index.py
│   │   │   └── connectors/
│   │   │       ├── base.py
│   │   │       └── mock.py
│   │   ├── events/
│   │   │   └── broadcaster.py
│   │   ├── models/                   # SQLAlchemy models, one file per table
│   │   ├── schemas/                  # Pydantic schemas
│   │   ├── pipeline.py                # orchestrates ingest → extract → classify → retrieve → verify
│   │   ├── config_hash.py
│   │   └── main.py
│   ├── config/
│   │   ├── ingestion.yaml
│   │   ├── domain_registry.yaml
│   │   ├── thresholds.yaml
│   │   └── gazetteers/
│   │       ├── financial_terms.jsonl
│   │       └── legal_terms.jsonl
│   ├── prompts/
│   │   ├── decomposer.md
│   │   ├── verifier.md
│   │   ├── challenger.md
│   │   └── section_summarizer.md
│   ├── scripts/
│   │   ├── run_ingest.py
│   │   ├── run_extract.py
│   │   ├── run_classify.py
│   │   ├── run_verify.py
│   │   └── compare_runs.py
│   ├── tests/
│   │   ├── fixtures/
│   │   └── reference_set/
│   └── alembic/
├── frontend/
│   └── src/{components,hooks,api,pages}
└── docker-compose.yml
```

---

## Database schema (full, run once in Phase 1)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename              TEXT NOT NULL,
  file_type             TEXT NOT NULL CHECK (file_type IN ('docx', 'pdf')),
  storage_path          TEXT NOT NULL,
  file_size_bytes        BIGINT,
  status                TEXT NOT NULL DEFAULT 'queued',  -- queued|ingesting|extracting|classifying|retrieving|verifying|complete|failed
  page_count            INT,
  has_structural_index  BOOLEAN NOT NULL DEFAULT false,  -- false for short docs that skip index-building (see Phase 2)
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE document_sections (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  title             TEXT,                 -- real heading text, or a generated label if pseudo-section
  is_pseudo_section BOOLEAN NOT NULL DEFAULT false,  -- true when created via topic-shift detection, not a real heading
  summary           TEXT,                 -- one-line human-readable note on what this section covers
  domain_hint       TEXT,                 -- cheap structural-tier domain guess, consumed in Phase 4
  order_index       INT NOT NULL,
  page_start        INT,
  page_end          INT
);

CREATE TABLE document_chunks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id      UUID REFERENCES document_sections(id),
  chunk_type      TEXT NOT NULL,       -- paragraph|table|figure_caption
  chunk_text      TEXT NOT NULL,
  context_capsule TEXT,                -- section title + document title, prepended when read in isolation
  page_number     INT,
  char_start      INT,
  char_end        INT,
  ocr_confidence  REAL                  -- null for native text; set 0.0-1.0 when OCR-derived
);

CREATE TABLE extracted_tables (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id    UUID REFERENCES document_sections(id),
  page_number   INT,
  table_data    JSONB NOT NULL,        -- rows of key/value pairs; never flattened prose
  parse_status  TEXT NOT NULL DEFAULT 'ok'  -- ok|needs_review
);

CREATE TABLE claims (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id        UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id           UUID NOT NULL REFERENCES document_chunks(id),
  claim_text         TEXT NOT NULL,
  source_span        TEXT NOT NULL,
  claim_type         TEXT NOT NULL,     -- statistical|causal|comparative|definitional|forward_looking|hedged|opinion
  scope              TEXT NOT NULL,     -- internal|external|both — see Phase 3's classification logic
  requires           JSONB,             -- what inputs are needed to verify this claim
  domain             TEXT,              -- financial|legal|scientific|general
  domain_confidence  REAL,
  domain_source      TEXT,              -- structural|terminology|semantic
  embedding          VECTOR(1536),      -- reserved for future claim-cache dedup; not required for the POC
  status             TEXT NOT NULL DEFAULT 'pending',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id         UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  source_type      TEXT NOT NULL,      -- internal|external
  source_ref       TEXT NOT NULL,      -- table id / page / URL
  content_snippet  TEXT,
  authority_score  REAL,
  retrieved_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE verdicts (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id              UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  verifier_verdict      TEXT,
  verifier_confidence   REAL,
  verifier_reasoning    TEXT,
  challenger_verdict    TEXT,
  challenger_confidence REAL,
  challenger_reasoning  TEXT,
  agreement             BOOLEAN,
  final_verdict         TEXT NOT NULL,   -- supported|contradicted|insufficient|disputed
  final_confidence      REAL NOT NULL,
  severity              TEXT NOT NULL DEFAULT 'info',  -- critical|major|minor|info
  resolved_by           TEXT NOT NULL,   -- deterministic|agent|human
  resolved_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pipeline_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL,   -- ingest|extract|classify|retrieve|verify
  config_hash  TEXT NOT NULL,
  input_ref    TEXT,
  raw_output   JSONB NOT NULL,
  duration_ms  INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_traces (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id      UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  agent_name    TEXT NOT NULL,   -- verifier|challenger
  prompt_sent   TEXT NOT NULL,
  raw_response  TEXT NOT NULL,
  tool_calls    JSONB,
  config_hash   TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chunks_document ON document_chunks(document_id);
CREATE INDEX idx_claims_document ON claims(document_id);
CREATE INDEX idx_evidence_claim ON evidence(claim_id);
CREATE INDEX idx_verdicts_claim ON verdicts(claim_id);
CREATE INDEX idx_pipeline_runs_document ON pipeline_runs(document_id, stage);
CREATE INDEX idx_agent_traces_claim ON agent_traces(claim_id);
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);  -- backs the history listing, Phase 9
```

---

## Canonical worked examples (used across every phase's tests — memorize these)

**Example A — internal, arithmetic-checkable.** Fixture table `extracted_tables` (Financial Highlights section) contains `{"Revenue (current period)": "$112M", "Revenue (prior period)": "$100M"}`. Fixture sentence: *"Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost discipline, positioning us ahead of our closest competitor."*

This decomposes (Phase 3) into four claims:

| Claim text | `claim_type` | `scope` | `requires` |
|---|---|---|---|
| Revenue grew 12% year-over-year | `statistical` | `internal` | `["current period revenue", "prior period revenue"]` |
| APAC expansion was a primary driver of revenue growth | `causal` | `internal` | `["regional revenue breakdown"]` |
| Cost discipline contributed to revenue growth | `causal` | `internal` | `["cost breakdown"]` |
| The company is ahead of its closest competitor | `comparative` | `both` | `["our revenue figure", "competitor's revenue figure"]` |

Claim 1 resolves via Phase 6's deterministic path: `(112 - 100) / 100 = 0.12` → matches the stated 12% → `supported`, `resolved_by='deterministic'`, no LLM call.

**Example B — the `both`-scope case.** Claim 4 above needs the internal figure (from the same table) and a competitor figure from outside the document — this is what exercises Phase 5's external stub and Phase 6's comparison logic. The mock external connector (Phase 5.2) should return a plausible competitor revenue figure so this claim can be fully resolved end to end in tests.

Every fixture document built in Phase 2 should contain Example A's sentence and table verbatim, so every later phase can test against the exact same known input.

---

## Claim classification: internal vs. external vs. both (the core design point of this POC)

This is decided once, during claim decomposition (Phase 3), and it's the single most important classification in the whole pipeline — it's the routing key that determines where Phase 5 looks for evidence and which verification path Phase 6 uses.

**Definitions:**

- **`internal`** — everything needed to verify this claim is expected to exist somewhere in the *same document*: a number reported elsewhere, a term defined earlier, a fact stated in another section. Verification is a lookup against the document's own tables/text — no outside source needed, no LLM required if it's a pure arithmetic claim.
- **`external`** — verifying this claim requires facts that cannot be found in the document at all: competitor data, regulatory thresholds, market benchmarks, general world facts. The document has no reason to contain the answer.
- **`both`** — the claim is a comparison or combination that needs one piece of evidence from inside the document and one piece from outside it, and can't be resolved with either alone.

**How the decomposition agent decides (this belongs in `prompts/decomposer.md`, written out in full below):** for each extracted claim, ask whether the entity/number/fact the claim is actually about is something *this document itself* would define or report (→ `internal`), something that inherently lives outside this document's own scope (→ `external`), or an explicit comparison between something inside the document and something outside it (→ `both`). A claim referencing "our revenue," "this year's results," or a metric with a definition elsewhere in the document is internal. A claim referencing "the industry average," "regulatory requirements," or a competitor's own figures is external. A claim like "we outperform X" or "we're ahead of the industry" is `both` — it can't be checked without pulling a number from each side.

**Why this matters for the rest of the pipeline:** the `scope` field on `claims` is read directly by Phase 5 to decide which retrieval path(s) to run (`internal` → query `extracted_tables`/chunks only; `external` → query the connector only; `both` → run both independently, then Phase 6 checks they're on the same basis/time-period before combining them). Get this tag wrong and every downstream stage looks in the wrong place — this is worth testing explicitly and precisely, not just "does something come back."

---

## Phase 0 — Scaffolding

- [ ] **0.1 — Repo structure** — create the full tree above.
  - Verify: `find . -type d | sort` matches the tree.

- [ ] **0.2 — Backend dependency manifest**
  - File: `backend/pyproject.toml` — `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2.0`, `alembic`, `asyncpg`, `pgvector`, `agno`, `spacy`, `python-docx`, `pymupdf`, `pdfplumber`, `pytesseract`, `pdf2image`, `pyyaml`, `sse-starlette`, `pytest`, `pytest-asyncio`, `httpx`. No `arq`, no `redis`.
  - Verify: `pip install -e .` completes cleanly.

- [ ] **0.3 — spaCy model** — `python -m spacy download en_core_web_trf`.
  - Verify: `python -c "import spacy; spacy.load('en_core_web_trf')"` succeeds.

- [ ] **0.4 — Frontend scaffold** — `npm create vite@latest frontend -- --template react-ts`; install `@tanstack/react-query`, `tailwindcss`.
  - Verify: `npm run dev` serves a blank page.

- [ ] **0.5 — `docker-compose.yml`** — services `postgres` (image `pgvector/pgvector:pg16`) and `api` only.
  - Verify: `docker-compose up` brings up both healthy.

- [ ] **0.6 — `.env.example`** — `DATABASE_URL`, `ANTHROPIC_API_KEY`.
  - Verify: every env var used anywhere in the codebase has an entry here.

- [ ] **0.7 — Seed config and prompts with real content**

  `backend/config/ingestion.yaml`:
  ```yaml
  ocr_confidence_threshold: 0.6        # below this, chunk flagged low_confidence_ocr
  native_text_char_threshold: 20       # PDF page text length above which it's treated as native, not scanned
  short_document_page_threshold: 5     # documents at or below this page count skip structural-index building
  topic_shift_sensitivity: 0.35        # 0-1; lower = more sensitive to topic changes when detecting pseudo-sections
  max_upload_size_mb: 200              # uploads above this are rejected with 413 before processing starts
  progress_event_every_n_pages: 5      # how often ingest_progress events fire on long documents
  section_summary_max_words: 150       # hard cap on every generated section summary, at any recursion level
  section_summary_direct_word_limit: 3000  # sections at or below this word count get summarized in a single LLM call;
                                            # above this, summarized hierarchically — see Phase 2.5.1
  ```

  `backend/config/thresholds.yaml`:
  ```yaml
  arithmetic_tolerance_pct: 0.5        # percentage-point tolerance for deterministic recompute vs stated figure
  ```

  `backend/config/domain_registry.yaml`:
  ```yaml
  - domain: financial
    claim_type: statistical
    evidence_source: internal_tables
    verification_method: deterministic
  - domain: financial
    claim_type: causal
    evidence_source: internal_tables
    verification_method: agent
  - domain: legal
    claim_type: definitional
    evidence_source: external
    verification_method: agent
  - domain: general
    claim_type: "*"
    evidence_source: external
    verification_method: agent
    confidence_ceiling: 0.6            # general fallback never reports higher confidence than this
  ```

  `backend/config/gazetteers/financial_terms.jsonl` — one JSON object per line, e.g. `{"label": "FINANCIAL_TERM", "pattern": "EBITDA"}`, `{"label": "FINANCIAL_TERM", "pattern": "constant currency"}`, and at least 8 more real terms (revenue, gross margin, YoY, quarter-over-quarter, cost of goods sold, operating income, free cash flow, working capital).

  `backend/config/gazetteers/legal_terms.jsonl` — similarly real: GDPR, material weakness, indemnification, force majeure, and at least 6 more.

  `backend/prompts/decomposer.md`:
  ```markdown
  You are decomposing a sentence from a business report into atomic, independently verifiable claims.

  For each claim, output:
  - text: the claim, self-contained (resolve pronouns/ellipsis using the provided context)
  - claim_type: one of statistical, causal, comparative, definitional, forward_looking, hedged, opinion
  - scope: internal (verifiable only from this document), external (needs sources outside this document),
    or both (needs one fact from inside the document and one from outside)
  - source_span: the exact text in the original sentence this claim came from
  - requires: a short list of what's needed to verify it

  A claim is internal if everything needed to check it would reasonably appear in this same document
  (a number, a defined term, a fact stated elsewhere). It's external if it needs something this document
  has no reason to contain (competitor data, industry benchmarks, regulatory text, general world facts).
  It's both if it's a comparison between something inside the document and something outside it.

  Do not invent claims the sentence does not make. Do not skip a claim bundled into a longer sentence.

  Context: {context_capsule}
  Sentence: {sentence}
  ```

  `backend/prompts/verifier.md` and `backend/prompts/challenger.md`: write real instructions per Phase 6 below (not placeholders) — see the code in Phase 6.2 for what each needs to accomplish; the actual prompt text should be written out in full there, this task just needs the files to exist with real content by the end of Phase 0 so nothing downstream is blocked, and it's fine to revise the wording once Phase 6 is reached.

  `backend/prompts/section_summarizer.md`:
  ```markdown
  Summarize the following section of a business report in at most {max_words} words.

  The summary's purpose is navigation, not general description: a reader — or another AI agent
  deciding where to look for a specific fact — should be able to read this summary and quickly
  judge whether a given figure, topic, or claim is likely to be found in this section, without
  reading the section itself.

  Prioritize: what metrics or figures are discussed, what topics are covered, what claims or
  conclusions are stated. Do not pad with generic phrasing like "This section discusses...".

  Section title: {section_title}
  Content:
  {text}
  ```
  This same prompt is reused for both summarizing a section's raw text directly and, for large sections, summarizing a collection of already-generated sub-summaries — see Phase 2.5.1 for why one prompt covers both cases.

  - Verify: none of the files above are empty; the domain registry parses as valid YAML with all four entries; both gazetteers have 10+ real entries each.

- [ ] **0.8 — Health endpoint** — `GET /health` → `200 {"status": "ok"}`.

### Phase 0 verification gate
- [ ] `docker-compose up` → both services healthy; `curl localhost:8000/health` → 200; `npm run dev` serves without error.
- [ ] Every config/prompt file has real, non-placeholder content, confirmed by reading each one, not just checking file size > 0.
- [ ] Commit: `poc-phase-0: scaffolding complete`.

---

## Phase 1 — Data model

- [ ] **1.1 — SQLAlchemy models** — one file per table in `backend/app/models/`, matching the schema above exactly, including `claims.scope` and `claims.requires`.
  - Verify: `python -c "from app.models import *"` imports cleanly.

- [ ] **1.2 — Alembic baseline migration** — includes `CREATE EXTENSION vector`, the `ivfflat` index, all indexes listed in the schema section.
  - Verify: `alembic upgrade head` runs clean against a fresh database.

- [ ] **1.3 — Test fixtures** — `backend/tests/conftest.py`, transactional-rollback test DB fixture.
  - Verify: a test inserting one row per table (all nine) round-trips correctly.

### Phase 1 verification gate
- [ ] `alembic upgrade head` clean from scratch.
- [ ] `\d claims` shows `scope`, `requires`, `domain`, `domain_confidence`, `domain_source` all present with correct types.
- [ ] Commit: `poc-phase-1: schema complete`.

---

## Phase 2 — Ingestion

- [ ] **2.1 — Word parser**
  - File: `backend/app/ingestion/parsers/docx_parser.py`
  ```python
  from docx import Document

  def parse_docx(path: str) -> list[dict]:
      doc = Document(path)
      elements = []
      for block in iter_block_items(doc):  # helper walking paragraphs + tables in document order
          if is_heading(block):
              elements.append({"type": "heading", "level": heading_level(block), "text": block.text})
          elif is_table(block):
              elements.append({"type": "table", "data": table_to_rows(block)})
          else:
              elements.append({"type": "paragraph", "text": block.text})
      return elements
  ```
  - Fixture: `backend/tests/fixtures/sample_report.docx` — must contain Example A's sentence and table verbatim (see the canonical examples section above), under a heading like "Financial Highlights."
  - Verify: `pytest backend/tests/test_docx_parser.py` parses the fixture into correct heading/paragraph/table elements in order.

- [ ] **2.2 — PDF text-layer parser**
  - File: `backend/app/ingestion/parsers/pdf_parser.py`
  - Per page: attempt extraction with PyMuPDF; if extracted character count exceeds `config/ingestion.yaml`'s `native_text_char_threshold`, treat as native and use `pdfplumber` for layout/table detection.
  - Fixture: `backend/tests/fixtures/native_report.pdf` (non-scanned).
  - Verify: `pytest backend/tests/test_pdf_parser.py` passes on the native fixture.

- [ ] **2.3 — OCR fallback**
  - File: `backend/app/ingestion/parsers/ocr.py`
  ```python
  import pytesseract
  from pdf2image import convert_from_path

  def ocr_page(pdf_path: str, page_number: int) -> tuple[str, float]:
      image = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)[0]
      data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
      text = " ".join(w for w in data["text"] if w.strip())
      confidences = [c for c in data["conf"] if c != -1]
      avg_confidence = sum(confidences) / len(confidences) if confidences else 0
      return text, avg_confidence / 100
  ```
  - Store `ocr_confidence` per chunk; below `config/ingestion.yaml`'s `ocr_confidence_threshold`, flag the chunk `low_confidence_ocr`. Process every page independently — a document can mix native and scanned pages.
  - Fixture: `backend/tests/fixtures/scanned_report.pdf` (image-only) and `backend/tests/fixtures/mixed_report.pdf` (some native pages, some scanned).
  - Verify: `pytest backend/tests/test_ocr.py` — scanned fixture produces text with confidence scores; mixed fixture routes each page independently and correctly.

- [ ] **2.4 — Chunker**
  - File: `backend/app/ingestion/chunker.py`
  - Chunk at structural boundaries (paragraph/table edges), never fixed token windows. Attach `context_capsule` (section title + document title) to every chunk.
  - Verify: chunk boundaries align with paragraph/section breaks in the fixture, not mid-sentence splits.

- [ ] **2.5 — Structural index, with size-tiering and no-heading fallback**
  - File: `backend/app/ingestion/structural_index.py`
  ```python
  def build_structural_index(document, elements, config) -> list[DocumentSection] | None:
      if document.page_count <= config["short_document_page_threshold"]:
          return None  # too short to bother — claims from this doc get full-document context instead

      headings = [e for e in elements if e["type"] == "heading"]
      if headings:
          return sections_from_headings(headings, elements)  # real section list

      # no headings at all: fall back to topic-shift pseudo-sectioning
      return pseudo_sections_from_topic_shift(elements, sensitivity=config["topic_shift_sensitivity"])
  ```
  - `pseudo_sections_from_topic_shift`: group consecutive paragraphs, detect a topic shift via embedding-similarity drop (or a simpler keyword-overlap heuristic — either is acceptable for the POC) between adjacent paragraph windows, and cut a new pseudo-section at each detected shift. Section summaries are generated separately — see Phase 2.5.1 — this task only needs to establish section boundaries.
  - Set `documents.has_structural_index = false` when the short-circuit fires.
  - Fixtures: `backend/tests/fixtures/short_memo.docx` (2 pages, should skip index-building) and `backend/tests/fixtures/unstructured_essay.pdf` (long, zero headings, should hit the pseudo-sectioning fallback).
  - Verify: the short fixture produces zero `document_sections` rows and `has_structural_index=false`; the headed fixture (2.1's `sample_report.docx`) produces a real section list including "Financial Highlights"; the unstructured fixture produces multiple pseudo-sections with `is_pseudo_section=true`, not one section covering the whole document.

- [ ] **2.5.1 — Section summary generation (LLM-based, hierarchical for large sections)**
  - File: `backend/app/ingestion/section_summarizer.py`
  - Every section (real or pseudo) from 2.5 gets its `summary` field populated by an actual LLM call using `prompts/section_summarizer.md`, capped at `section_summary_max_words` (150). This is a real generation step, not a heuristic — it costs a model call per section, which is a deliberate tradeoff: a 150-word navigational summary is worth far more for the cross-reference fallback in Phase 5.1.1 than a cheap one-liner would be.
  - **Large sections can't just be dumped into one prompt.** A section spanning many pages might exceed what's sensible to summarize in a single call, and stuffing more raw text into the prompt doesn't reliably produce a better summary anyway. The rule: if a section's total word count is at or below `section_summary_direct_word_limit` (3000), summarize it directly in one call. If it's larger, split it into batches under that limit, summarize each batch independently, then summarize the *collection of batch summaries* into the final ≤150-word result — a summary of summaries. If even the combined batch summaries exceed the direct limit (only plausible for an extremely large section with many batches), reduce again, recursively, the same way. In practice this terminates in one or two levels, because each summarization step compresses substantially.
  ```python
  import asyncio

  async def summarize_text(text: str, section_title: str, max_words: int) -> str:
      prompt = load_prompt("prompts/section_summarizer.md").format(
          max_words=max_words, section_title=section_title, text=text)
      return await llm_call(prompt)  # single LLM call, returns the summary string

  async def generate_section_summary(section_chunks: list[str], section_title: str, config: dict) -> str:
      full_text = "\n".join(section_chunks)
      max_words = config["section_summary_max_words"]
      direct_limit = config["section_summary_direct_word_limit"]

      if len(full_text.split()) <= direct_limit:
          return await summarize_text(full_text, section_title, max_words)

      # too large for one call: batch, summarize each batch concurrently, then reduce
      batches = split_into_batches(section_chunks, max_words_per_batch=direct_limit)
      batch_summaries = await asyncio.gather(
          *[summarize_text("\n".join(b), section_title, max_words) for b in batches]
      )
      combined = "\n".join(batch_summaries)
      if len(combined.split()) <= direct_limit:
          return await summarize_text(combined, section_title, max_words)  # final reduce
      # combined summaries still too large — recurse (summary of summaries of summaries)
      return await generate_section_summary(batch_summaries, section_title, config)
  ```
  - Run summary generation for all of a document's sections **concurrently** (`asyncio.gather` across sections, with a sane concurrency cap — e.g. 5–10 at a time), not sequentially — a document with dozens of sections summarizing one at a time would meaningfully slow down ingestion on exactly the large documents from Phase 2.7 where this matters most.
  - Traceability: store the prompt sent and raw response for each summarization call (base-case and reduce-case alike) inside the `pipeline_runs` row's `raw_output` JSON for the `ingest` stage, keyed by section id — no new table needed, this reuses the tracing mechanism already established for the tuning harness rather than adding a parallel one.
  - Verify: a normal-sized section (well under 3000 words) produces a single LLM call and a summary at or under 150 words. To test the hierarchical path without needing an enormous fixture, temporarily set `section_summary_direct_word_limit` very low (e.g. `50`) in a test-scoped config override and confirm an ordinary fixture section correctly triggers the batch-then-reduce path and still produces a single coherent summary at or under 150 words, not a truncated or malformed one.

- [ ] **2.6 — `scripts/run_ingest.py`**
  - `python scripts/run_ingest.py backend/tests/fixtures/sample_report.docx` runs 2.1–2.5.1 standalone, prints resulting chunks/tables/sections as JSON — including each section's generated `summary`, its word count, and whether it was produced directly or via the batch-and-reduce path — and writes a `pipeline_runs` row (`stage='ingest'`) with the prompt/response trace per section per 2.5.1.
  - Verify: running against each fixture produces correct JSON; editing `ocr_confidence_threshold` in `config/ingestion.yaml` and re-running changes which chunks get flagged, with zero code changes; editing `prompts/section_summarizer.md` and re-running visibly changes summary style/content with zero Python changes.

- [ ] **2.7 — Large-file handling: thread pool + incremental persistence + fine-grained progress**
  - Files: `backend/app/ingestion/large_file.py`, edits to 2.3's OCR loop and 2.4's chunker
  - **Thread pool, not naive async.** OCR (`pytesseract`) and PDF/table parsing are synchronous, CPU-bound calls — run each page's parsing/OCR via `asyncio.to_thread(ocr_page, ...)` (or a `ThreadPoolExecutor`), never called directly inside an `async def` background task. Called naively, a large scanned document would block the event loop for the entire processing duration, freezing every other request the API is handling — including SSE streams for unrelated documents.
  ```python
  import asyncio

  async def process_page(pdf_path: str, page_number: int, config: dict) -> dict:
      # ocr_page and native-text extraction are sync/CPU-bound — offload them
      text, confidence = await asyncio.to_thread(ocr_page, pdf_path, page_number)
      return {"page_number": page_number, "text": text, "confidence": confidence}
  ```
  - **Incremental persistence.** Write each page's resulting chunks/tables to Postgres as they're produced, not accumulated in a Python list and written once at the end — keeps peak memory bounded regardless of document length.
  - **Fine-grained progress.** Every `progress_event_every_n_pages` pages (from `config/ingestion.yaml`), publish an `ingest_progress` event (`{"pages_done": N, "pages_total": M}`) via the Phase 7 broadcaster, so a multi-minute OCR run on a long document shows real percentage progress in the UI instead of sitting on "ingesting..." with no feedback.
  - Fixture: `backend/tests/fixtures/large_report.pdf` — at least 80 pages, mixed native and scanned, large enough to meaningfully exercise this path (doesn't need to hit the full 200-page production target, just big enough that a naive synchronous implementation would visibly stall). Also include, deliberately, a sentence near the front of the document making a claim whose supporting figure/table sits many pages later in a different section — Phase 5.1.1 depends on this fixture containing that far-apart pair, so build it in now rather than bolting it on when Phase 5 is reached.
  - Verify: while `large_report.pdf` is processing, issue a concurrent request to `GET /health` (or any other endpoint) and confirm it responds immediately rather than waiting for ingestion to finish — this is the actual proof the thread-pool offload works, not just that ingestion eventually completes. Separately confirm `ingest_progress` events arrive at roughly the configured interval, and that peak memory during the run doesn't scale linearly with page count (spot-check via a memory profiler or simple `RSS` sampling — exact tooling is flexible, the requirement is bounded memory, not a specific tool).

- [ ] **2.8 — Upload size limit enforcement** — see Phase 9.1, which implements the streaming upload endpoint this task's config value (`max_upload_size_mb`) constrains; listed here as a dependency note so Phase 2's fixture work and Phase 9's endpoint work stay consistent.

### Phase 2 verification gate
- [ ] All six fixtures from 2.1–2.5 (docx, native PDF, scanned PDF, mixed PDF, short memo, unstructured essay) ingest correctly via tests and the CLI script.
- [ ] Short-document skip and no-heading fallback both independently verified, not just implemented.
- [ ] Every generated section summary is ≤150 words, including ones produced via the batch-and-reduce path; the reduce path is confirmed to actually trigger under the low-threshold test, not just implemented and assumed correct.
- [ ] `large_report.pdf` processes without blocking concurrent requests, with bounded memory and periodic progress events.
- [ ] Commit: `poc-phase-2: ingestion complete`.

---

## Phase 3 — Claim extraction & scope classification

- [ ] **3.1 — spaCy pipeline + gazetteer**
  - File: `backend/app/nlp/spacy_pipeline.py`
  ```python
  import spacy
  nlp = spacy.load("en_core_web_trf")
  ruler = nlp.add_pipe("entity_ruler", before="ner")
  ruler.from_disk("config/gazetteers/financial_terms.jsonl")
  ruler.add_patterns(load_jsonl("config/gazetteers/legal_terms.jsonl"))
  ```
  - Verify: a smoke-test sentence containing "EBITDA" produces a gazetteer match.

- [ ] **3.2 — Dependency-parse pre-filter**
  - Flag sentences with multiple clauses/coordinating conjunctions as decomposition candidates; simple single-fact sentences skip straight to a direct claim record without an LLM call.
  - Verify: Example A's sentence flags `needs_decomposition=True`; a plain single-fact sentence flags `False`.

- [ ] **3.3 — Claim schema**
  - File: `backend/app/schemas/claim.py`
  ```python
  from pydantic import BaseModel

  class ExtractedClaim(BaseModel):
      text: str
      claim_type: str   # statistical|causal|comparative|definitional|forward_looking|hedged|opinion
      scope: str         # internal|external|both — see the classification section above
      source_span: str
      requires: list[str]

  class ClaimList(BaseModel):
      claims: list[ExtractedClaim]
  ```

- [ ] **3.4 — Decomposition agent**
  - File: `backend/app/agents/decomposer.py`
  ```python
  from agno.agent import Agent
  from agno.models.anthropic import Claude

  def build_decomposer() -> Agent:
      instructions = open("prompts/decomposer.md").read()
      return Agent(model=Claude(id="claude-sonnet-4-6"), instructions=[instructions], response_model=ClaimList)
  ```
  - Feed the sentence plus its `context_capsule` from Phase 2.4 into the prompt template.
  - Verify: run against Example A's sentence — must produce exactly the four claims documented in the canonical examples section, with exact `claim_type`/`scope` values asserted in the test (not just "four claims came back").

- [ ] **3.5 — `scripts/run_extract.py`**
  - `python scripts/run_extract.py "Revenue grew 12% YoY, driven by APAC expansion and cost discipline, beating our closest competitor."` — runs decomposition standalone, prints claim cards with their `scope` tag clearly labeled.
  - Verify: editing `prompts/decomposer.md` and re-running against the same sentence shows a visibly different result with zero Python changes.

### Phase 3 verification gate
- [ ] Example A decomposes to the exact documented shape, via both pytest and the standalone script.
- [ ] Every claim's `scope` value is correct per the classification definitions above — spot-check by hand against the definitions, not just against what the agent happened to output.
- [ ] Commit: `poc-phase-3: claim extraction and scope classification complete`.

---

## Phase 4 — Domain classification & routing

- [ ] **4.1 — Cascade**
  - File: `backend/app/nlp/domain_router.py`
  ```python
  def classify_domain(claim, section, config) -> dict:
      if section and section.domain_hint:
          return {"domain": section.domain_hint, "confidence": 0.95, "source": "structural"}
      if hits := match_gazetteer(claim.claim_text):
          return {"domain": hits.domain, "confidence": 0.85, "source": "terminology"}
      result = semantic_domain_classifier(claim.claim_text)  # embedding similarity vs a small seeded exemplar set
      return {"domain": result.domain, "confidence": result.score, "source": "semantic"}
  ```
  - Seed a small exemplar set (a handful of representative sentences per domain) for the semantic fallback — don't leave this as a TODO, it's needed for the cascade to be complete.
  - Verify: `pytest backend/tests/test_domain_routing.py` covers all three tiers.

- [ ] **4.2 — `scripts/run_classify.py`**
  - `python scripts/run_classify.py "our approach complies with GDPR" --section "Executive Summary"` prints which tier resolved the claim and why.
  - Verify: this example resolves via the terminology tier (GDPR is in the legal gazetteer) despite a generic section header; editing `config/domain_registry.yaml` changes routing on the next run with zero code changes.

### Phase 4 verification gate
- [ ] All three cascade tiers independently verified; registry-driven, not hardcoded.
- [ ] Commit: `poc-phase-4: domain routing complete`.

---

## Phase 5 — Evidence retrieval (routed by the internal/external/both scope tag)

- [ ] **5.1 — Internal lookup**
  - File: `backend/app/retrieval/internal_index.py`
  - Direct queries against `extracted_tables`/`document_chunks` for the document — no embedding search. Only runs for claims where `scope` is `internal` or `both`. This handles the common case where a claim's evidence is co-located in its own chunk/section, already linked at ingestion.
  - Verify: querying for Example A's revenue figures returns the correct table cell with exact page/section citation.

- [ ] **5.1.1 — Cross-reference fallback: actually using the map from Phase 2.5**
  - File: `backend/app/retrieval/cross_reference.py`
  - This is the retrieval-time payoff of the structural index built in Phase 2.5 — the reason that map exists at all is so a claim on page 3 needing evidence from page 180 doesn't require reading everything in between (the same principle PageIndex, https://github.com/VectifyAI/PageIndex, is built around). For the POC, implement this using the `document_sections` table's `summary` field already produced in Phase 2.5, rather than adding PageIndex as a new dependency — a small agent call reasons over the list of section titles + one-line summaries and picks the most likely section to check, the same way a human would use a table of contents.
  ```python
  async def resolve_cross_reference(claim, sections: list[DocumentSection]) -> Evidence | None:
      # only called when 5.1's direct lookup misses
      candidates = [(s.id, s.title, s.summary) for s in sections]
      chosen_section_id = await navigator_agent.pick_section(claim.requires, candidates)
      if chosen_section_id is None:
          return None  # genuinely not found — resolves to insufficient upstream, not a guess
      return lookup_in_section(chosen_section_id, claim)
  ```
  - **Only runs when 5.1 genuinely misses** — never the default path. This is an LLM call (agentic navigation), not a free lookup, so calling it unconditionally would reintroduce real cost for no benefit on the common case where evidence is already co-located.
  - Skip entirely for documents with `has_structural_index=false` (short documents already get full-document context — there's no map to navigate).
  - Verify: extend `large_report.pdf` (Phase 2.7) with a claim near the front of the document whose supporting figure sits many pages later, in a different section — confirm this claim resolves correctly via this fallback with an accurate page/section citation. Separately confirm a claim whose evidence *is* co-located never invokes this path at all — same "assert it wasn't called" discipline as 5.2's external-connector check, not just "the right answer came back."

- [ ] **5.2 — External connector, stubbed**
  - Files: `backend/app/retrieval/connectors/{base.py, mock.py}`
  ```python
  class EvidenceConnector(Protocol):
      async def fetch(self, claim, config) -> list[Evidence]: ...

  class MockConnector(EvidenceConnector):
      async def fetch(self, claim, config):
          # returns realistic fixture data standing in for a real external source
          return [Evidence(source_type="external", source_ref="mock://competitor-filing",
                            content_snippet="Competitor reported 8% revenue growth", authority_score=0.6)]
  ```
  - Only runs for claims where `scope` is `external` or `both`. Log clearly that this is a mock, not a real source.
  - Verify: a claim tagged `scope='external'` or `scope='both'` retrieves mock evidence through this interface; a claim tagged `scope='internal'` never calls this connector at all (assert it wasn't invoked, not just that the final answer was correct).

- [ ] **5.3 — Retrieval dispatcher**
  - File: `backend/app/retrieval/dispatch.py` — reads `claims.scope` and calls 5.1 (and 5.1.1 as a fallback if 5.1 misses), 5.2, or both accordingly. This is the piece that makes the scope tag from Phase 3 actually consequential, not just metadata.
  - Verify: for each of the three scope values, confirm exactly the expected retrieval path(s) fire — `internal` calls 5.1 (falling back to 5.1.1 only on a miss), `external` calls only 5.2, `both` calls both sides.

### Phase 5 verification gate
- [ ] All three scope-routing paths independently tested and confirmed to call only what they should.
- [ ] Internal retrieval returns exact citations; the cross-reference fallback correctly resolves far-away evidence on the large-document fixture and is confirmed not to fire when evidence is already co-located; external stub is clearly marked as a mock.
- [ ] Commit: `poc-phase-5: evidence retrieval complete`.

---

## Phase 6 — Multi-agent verification

- [ ] **6.1 — Deterministic arithmetic path**
  - File: `backend/app/agents/deterministic_verifier.py`
  - For claims where `domain_registry.yaml` names `verification_method: deterministic` (i.e., `(financial, statistical)`): locate figures via 5.1, recompute `(current - prior) / prior`, compare to the stated percentage within `config/thresholds.yaml`'s `arithmetic_tolerance_pct`. Never calls an LLM.
  - Verify: run against Example A's revenue claim — `(112-100)/100 = 12%`, matches stated 12% → `final_verdict='supported'`, `resolved_by='deterministic'`. Also test a deliberately mismatched pair of figures to confirm `contradicted` fires correctly.

- [ ] **6.2 — Verifier and challenger agents**
  - Files: `backend/app/agents/{verifier.py, challenger.py}`, both loading instructions from `prompts/verifier.md`/`prompts/challenger.md` at startup — never inlined.

  `backend/prompts/verifier.md`:
  ```markdown
  Given a claim and its evidence bundle, build the strongest supported case.
  Return: verdict (supported/contradicted/insufficient), confidence (0-1), reasoning, and citations
  to the specific evidence items that support your verdict.
  ```

  `backend/prompts/challenger.md`:
  ```markdown
  Given a claim, its evidence, and the verifier's verdict, find the strongest rebuttal. Check:
  1. Citation fidelity — does the cited evidence actually say what the verifier claims it says?
  2. Basis/definition mismatches — is the evidence answering the exact question the claim asks,
     on the same basis (e.g. reported vs constant-currency) and the same time period?
  3. Completeness — does the evidence support the full claim, or only part of it?
  4. For external evidence specifically — is the source current and authoritative, and is there
     more than one independent source, or just one?
  Return your own verdict, confidence, and reasoning, explicitly stating whether you accept or
  reject the verifier's conclusion and why.
  ```

  - Verify: instantiate each agent and confirm its loaded instructions came from these files, not hardcoded text.

- [ ] **6.3 — Tools as independent modules**
  - Files: `backend/app/agents/tools/{citation_check.py, internal_lookup.py}`
  ```python
  # citation_check.py — deterministic pre-check, no LLM call
  def citation_fidelity(cited_span: str, claimed_content: str) -> bool:
      return claimed_content.lower() in cited_span.lower()  # simple containment check is enough for the POC
  ```
  - `internal_lookup.py` wraps Phase 5.1 as a callable tool the agents can invoke mid-reasoning if they need to pull a specific figure themselves.
  - Each tool is independently unit-testable without an agent or LLM call.
  - Verify: `citation_fidelity` correctly returns `False` on a deliberately mismatched span/claim pair, `True` on a matching one — pure function test, no agent involved.

- [ ] **6.4 — Reconciliation + severity**
  - File: `backend/app/agents/reconcile.py`
  ```python
  def reconcile(verifier_result, challenger_result) -> dict:
      if verifier_result.verdict == challenger_result.accepted_verdict:
          confidence = min(verifier_result.confidence, challenger_result.confidence)
          final = verifier_result.verdict
          resolved_by = "agent"
      else:
          final, confidence, resolved_by = "disputed", 0.0, "agent"
      severity = derive_severity(final, claim_domain="financial")  # see table below
      return {"final_verdict": final, "final_confidence": confidence,
              "agreement": verifier_result.verdict == challenger_result.accepted_verdict,
              "severity": severity, "resolved_by": resolved_by}
  ```
  - Severity rule table (implement exactly):

  | `final_verdict` | claim domain | `severity` |
  |---|---|---|
  | `contradicted` | financial/statistical | `critical` |
  | `contradicted` | any other | `major` |
  | `disputed` | any | `major` |
  | `insufficient` | any | `minor` |
  | `supported` | any | `info` |

  - Verify: unit tests covering all five rows of the severity table, and confirming disagreement never silently resolves to one agent's answer.

- [ ] **6.5 — Full tracing**
  - Every verifier/challenger call writes an `agent_traces` row: the exact prompt sent after template substitution, the raw response, `tool_calls` (which tools fired, with what arguments), tagged with the current config hash (Phase 8.1).
  - Verify: verifying one claim produces exactly two `agent_traces` rows, both with non-empty `prompt_sent`/`raw_response`.

- [ ] **6.6 — `scripts/run_verify.py`**
  - `python scripts/run_verify.py --adhoc "Revenue grew 12%" --evidence "Current: $112M, Prior: $100M"`, or `--claim-id <id>` against a stored claim — prints both agents' full reasoning and tool calls.
  - Verify: disabling the citation-fidelity tool (6.3) and re-running produces a visibly different, reproducible result via this script alone.

### Phase 6 verification gate
- [ ] Both canonical examples (A and B) resolve correctly end to end — A via the deterministic path, B via the `both`-scope agent path with the external mock.
- [ ] All five severity rows correctly derived; disagreement never auto-resolves.
- [ ] Tool modularity proven by disabling one and observing the change.
- [ ] Commit: `poc-phase-6: multi-agent verification complete`.

---

## Phase 7 — Real-time updates (SSE)

- [ ] **7.1 — In-process broadcaster**
  - File: `backend/app/events/broadcaster.py` — one `asyncio.Queue` per document, no external dependency.

- [ ] **7.2 — SSE endpoint**
  - `GET /documents/{id}/events`, subscribed to 7.1.
  - Verify: `curl -N localhost:8000/documents/{id}/events` shows live events during processing.

- [ ] **7.3 — Frontend hook**
  - `frontend/src/hooks/useDocumentEvents.ts` — subscribes via `EventSource`, updates claim state incrementally as `claim_verified` events arrive.
  - Verify: a test page shows claims appearing one at a time, not all at once.

### Phase 7 verification gate
- [ ] SSE delivers every event type (`ingest_complete`, `claim_extracted`, `claim_verified`, `verification_complete`) in order.
- [ ] Commit: `poc-phase-7: real-time updates complete`.

---

## Phase 8 — Tuning & comparison harness

- [ ] **8.1 — Config hashing**
  - File: `backend/app/config_hash.py` — hashes the combined contents of `config/` + `prompts/` into one identifier, used to tag `pipeline_runs`/`agent_traces` rows (already wired in earlier phases) and to name comparison snapshots.
  - Verify: the hash changes when any file under `config/` or `prompts/` changes, stable otherwise.

- [ ] **8.2 — Reference set**
  - Directory: `backend/tests/reference_set/` — 3–5 documents (reuse Phase 2's fixtures), each with hand-checked expected claims/verdicts, covering both scope values and at least three claim types.
  - Verify: running the full pipeline against this set matches the hand-checked expectations on a clean checkout.

- [ ] **8.3 — `scripts/compare_runs.py`**
  - `python scripts/compare_runs.py --document <id> --config-a <hash> --config-b <hash>` — re-runs the same document under two config snapshots, prints a claim-by-claim diff (verdict, confidence, domain, scope changes).
  - Verify: change `arithmetic_tolerance_pct` in `config/thresholds.yaml`, re-run the reference set under old and new config, and confirm the script correctly isolates exactly which claims' verdicts changed as a result.

### Phase 8 verification gate
- [ ] Config hashing is stable and correctly change-sensitive.
- [ ] `compare_runs.py` correctly isolates the effect of a deliberate, known config change against the reference set.
- [ ] Commit: `poc-phase-8: tuning harness complete`.

---

## Phase 9 — API endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/documents` | Upload `.docx`/`.pdf`, start processing |
| `GET` | `/documents` | **History list** — past documents, paginated, newest first, with a claim-verdict summary per document |
| `GET` | `/documents/{id}` | Document metadata + status |
| `GET` | `/documents/{id}/events` | SSE stream of processing progress |
| `GET` | `/documents/{id}/claims` | All claims + verdicts for a document, including `scope` and `domain` |
| `GET` | `/claims/{id}` | Single claim: verdict, evidence, full agent traces |
| `GET` | `/claims/{id}/traces` | Raw `agent_traces` rows for a claim |
| `GET` | `/documents/{id}/runs` | `pipeline_runs` history, filterable by `stage` |
| `POST` | `/claims/{id}/reverify` | Re-run verification on one claim against current config, without reprocessing the document |

- [ ] **9.1 — `POST /documents`: streaming upload with size enforcement**
  - File: `backend/app/api/documents.py`
  - Stream the incoming file to `storage_path` in chunks (e.g. `1MB` reads via `UploadFile.read(chunk_size)` in a loop) rather than `await file.read()` in one call — bounds memory during the upload itself regardless of file size.
  - Enforce `config/ingestion.yaml`'s `max_upload_size_mb` **during** the streaming write (track bytes written so far and abort as soon as the limit is crossed), not only by trusting the `Content-Length` header, which a client can omit or misreport. Return `413 Payload Too Large` with a clear message on rejection, and clean up the partial file on disk.
  - Store `file_size_bytes` on the `documents` row. Return `202` immediately with the document ID and `status='queued'`; all processing happens after the response is sent.
  - Verify: an upload under the limit succeeds and streams correctly; an upload over the limit is rejected with `413` before processing starts, and leaves no partial file behind; `file_size_bytes` is recorded accurately.

- [ ] **9.2 — `GET /documents`: history listing**
  - File: `backend/app/api/documents.py`
  - Query params: `limit` (default 20), `offset` (default 0), optional `status` filter. Sort by `created_at DESC` (uses the index from Phase 1).
  - Response includes, per document: `id`, `filename`, `status`, `created_at`, `page_count`, `file_size_bytes`, and a `claim_summary` object — counts of claims grouped by `final_verdict` (`supported`/`contradicted`/`insufficient`/`disputed`), computed via a join against `claims`/`verdicts` at query time (no need for a cached/materialized summary at this scale).
  - Verify: uploading three fixture documents and calling this endpoint returns all three, newest first, each with an accurate `claim_summary`; the `status` filter correctly narrows results; pagination (`limit`/`offset`) behaves correctly across more documents than one page.

- [ ] **9.3 — Remaining endpoints** — implement the rest of the table above per the existing schema (unchanged from earlier phases). No auth middleware, no scoping — single-user dataset.
  - Verify: `/docs` renders all nine endpoints; each returns correctly shaped data against fixtures; `reverify` produces exactly one new pair of `agent_traces` rows and zero changes to any other claim.

### Phase 9 verification gate
- [ ] All nine endpoints verified against the fixture documents.
- [ ] Large-file upload correctly streams and enforces the size limit; history listing correctly reflects multiple past documents with accurate summaries.
- [ ] Commit: `poc-phase-9: API complete`.

---

## Phase 10 — Frontend UI

- [ ] **10.0 — `DocumentHistory` — the app's landing page**
  - File: `frontend/src/pages/DocumentHistory.tsx`
  - Fetches `GET /documents`, renders a table: filename, upload date, status badge, page count, and the `claim_summary` counts as small colored badges (matching the severity colors used in the review panel — red-leaning for `contradicted`/`disputed`, neutral for `supported`/`insufficient`). Each row is clickable, navigating to that document's review view (10.3/10.4). An "Upload new document" control at the top opens 10.1. Supports the same pagination and `status` filter as the API.
  - Verify: after processing three fixture documents, this page lists all three, newest first, with correct badges; clicking a row that finished processing opens straight to its claims; clicking a row still in progress opens the live progress view (10.2) instead.

- [ ] **10.1 — `DocumentUpload`** — drag/drop, calls `POST /documents`, redirects to the new document's progress view on success. Surfaces the `413` response from Phase 9.1 as a clear "file too large" message, not a generic error.
- [ ] **10.2 — `ProgressBar`** — driven by `useDocumentEvents`, shows named stages **and** real percentage progress on long documents via the `ingest_progress` events from Phase 2.7 (`pages_done`/`pages_total`), not just a stage label with no indication of how much is left.
- [ ] **10.3 — `DocumentViewer`** — source text with sentence-level verdict highlighting.
- [ ] **10.4 — `ClaimReviewPanel`** — shows, per claim: raw chunk text, `scope` (internal/external/both) and `domain` with which tier resolved it, full verifier and challenger reasoning side by side (not collapsed), tool calls made, and for deterministic claims the actual recomputed figures. This panel is intentionally more detailed than a production UI would be — the goal is to see *why*, not just get a clean verdict.

**Verify:** manual walkthrough — land on the history page (empty on first run), upload the fixture document, watch live progress with real percentage on the large-document fixture specifically, open both the deterministic claim and the `both`-scope claim and confirm every piece of reasoning is visible, then navigate back to the history page and confirm the just-processed document now appears with an accurate claim summary and can be reopened.

### Phase 10 verification gate
- [ ] Full upload-to-review walkthrough works end to end for both canonical examples.
- [ ] History page correctly lists and allows reopening past documents, including one still mid-processing.
- [ ] Progress bar shows real percentage on the large-document fixture, not just a static stage label.
- [ ] Commit: `poc-phase-10: frontend complete`.

---

## Definition of done

- Every checkbox in every phase checked, every gate passed and committed separately (11 commits, in order).
- Both canonical worked examples resolve correctly end to end, through the API and visibly in the UI.
- The tuning loop is proven, not just built: a deliberate config or prompt change, run through `compare_runs.py` against the reference set, visibly and correctly changes only the claims it should.
- The large-document fixture processes without blocking the API and with bounded memory, and its progress is visibly granular in the UI, not just "processing..." for minutes.
- Past documents are browsable from the history page and can be reopened for review at any time.
- Zero unresolved `[BLOCKED-CREDENTIALS]` markers.
