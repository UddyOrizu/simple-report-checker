# Claim Checker POC

A pipeline that ingests business reports (`.docx`/`.pdf`), extracts factual claims, classifies
each claim's domain and scope (internal / external / both), retrieves evidence, and verifies each
claim via either deterministic arithmetic recomputation or a two-agent (verifier + challenger)
LLM review — with full tracing and a review UI.

## Prerequisites

- Docker + Docker Compose
- Python 3.11 (only needed if you want to run the backend outside Docker)
- Node 18+ and npm (for the frontend)
- An [Anthropic API key](https://console.anthropic.com/) or an [OpenAI API key](https://platform.openai.com/api-keys)
  — optional, and only one of the two is needed (pick via `LLM_PROVIDER`). Without one,
  ingestion, chunking, structural indexing, and arithmetic (deterministic) verification all work
  fully; everything that needs an LLM call (claim decomposition, section summaries, agent-based
  verification) is skipped gracefully rather than failing the run.

## Quick start

```bash
cd poc
cp .env.example .env        # fill in ANTHROPIC_API_KEY (or set LLM_PROVIDER=openai + OPENAI_API_KEY)
docker compose up -d        # starts Postgres + the API (builds the API image on first run)
```

Wait for both services to report healthy, then run migrations once:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m spacy download en_core_web_sm en_core_web_trf
alembic upgrade head
```

In a second terminal, start the frontend:

```bash
cd poc/frontend
npm install
npm run dev
```

Open **http://localhost:5173**. The dev server proxies `/api/*` to the backend at
`localhost:8000` (see `vite.config.ts`).

`GET http://localhost:8000/health` → `{"status": "ok"}` confirms the API is up.
`GET http://localhost:8000/docs` renders interactive API docs for all 9 endpoints.

## Environment variables

Set in `poc/.env` (used by `docker compose`) or exported directly if running the backend outside
Docker:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://poc:poc@localhost:5433/claim_checker` | Postgres connection (async driver) |
| `LLM_PROVIDER` | `anthropic` | Which backend every Agno agent uses: `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` | *(empty)* | Required for any LLM-backed stage when `LLM_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | *(empty)* | Required for any LLM-backed stage when `LLM_PROVIDER=openai` |
| `OPENAI_BASE_URL` | `https://eu.api.openai.com/v1` | Only used when `LLM_PROVIDER=openai`; point at a proxy/gateway if needed |
| `ANTHROPIC_MODEL_ID` / `ANTHROPIC_MINI_MODEL_ID` | `claude-sonnet-4-5-20250929` / `claude-haiku-4-5-20251001` | Override the standard/mini model tier for Anthropic |
| `OPENAI_MODEL_ID` / `OPENAI_MINI_MODEL_ID` | `gpt-4o` / `gpt-4o-mini` | Override the standard/mini model tier for OpenAI |
| `INTERNAL_VERIFICATION_VOTERS` | `standard,mini,fino1` | Which models sit on the internal-claim "highest vote" panel (see below) |
| `HF_TOKEN` | *(empty)* | Hugging Face token for the `fino1` voter (TheFinAI/Fin-o1-8B). Missing token just drops that voter from the vote |
| `FINO1_MODEL_ID` | `TheFinAI/Fin-o1-8B` | Override the specialized finance model used by the `fino1` voter |
| `HF_INFERENCE_BASE_URL` | *(empty — HF's shared routing)* | Set only if Fin-o1-8B is deployed as a dedicated HF Inference Endpoint |
| `STORAGE_DIR` | `./storage` | Where uploaded documents are stored on disk |

### Internal-claim verification

Claims routed `scope="internal"` are verified by a multi-model "highest vote" panel
(`app/agents/internal_vote_panel.py`) instead of the verifier/challenger adversarial pair used for
`external`/`both` claims: every configured voter reads the same evidence independently and votes
`supported`/`contradicted`/`insufficient`; the majority wins, and a tie (including a 3-way split)
resolves to `disputed` at zero confidence rather than picking one arbitrarily. Evidence is gathered
through a ladder — vectorless section retrieval (verified, cited quotes; see below) first, then
exact keyword lookup, embedding search over the claim's `requires` phrases, and the cross-reference
resolver — and only when all four find nothing does it
fall back to handing the whole document to the panel directly (the claim's own source chunk has its
wording redacted first, so a voter can't "verify" the claim by reading it back to itself). Financial
claims that resolve deterministically (exact arithmetic recomputation) skip this panel entirely —
that path is already exact and free.

### Markdown conversion

Uploads are converted to Markdown before parsing, so the structural index gets a real heading
hierarchy (`#`/`##`/`###` levels) instead of guessing headings from font sizes:

- **PDF** — [marker-pdf 0.3.2](https://pypi.org/project/marker-pdf/0.3.2/), an optional extra
  because it pulls in torch plus surya's layout/OCR/table models (several GB, downloaded on first
  use) and pins older Pillow/regex. Install with `pip install -e ".[marker]"` (or
  `docker compose build --build-arg INSTALL_EXTRAS=marker`). Without it, PDFs fall back to the
  pdfplumber/Tesseract parser. marker is GPL-3.0 licensed — check that suits your distribution.
- **Word (.docx)** — mammoth + markdownify, always available.

Both are toggled in `config/ingestion.yaml` under `markdown_conversion`. The rendered Markdown is
stored on `documents.markdown`, and each section's own body on `document_sections.content`.

Every document takes the same ingestion path regardless of size (`app/ingestion/pipeline.py`).
Only conversion runs incrementally (`app/ingestion/conversion.py`): marker converts
`marker_pages_per_batch` pages per call, the native parser one page at a time, and a `.docx` in one
go, with `ingest_progress` events in between. Generated headings, chunking, the structural index
and summaries then run once over the whole document, and chunks are embedded and written
`persist_chunk_batch` at a time so memory stays bounded. marker drops a blank page's page break, so
when a batch comes back with fewer page breaks than pages, its page numbers are re-derived from the
PDF's own text layer.

### Generated section headings

Documents longer than `short_document_page_threshold` with fewer than two real headings (no
headings at all, or only a title) get LLM-generated ones (`app/ingestion/heading_generator.py`):
the model reads the paragraphs in order, in windows for long documents, and decides where the
topic changes and what to call each part. The headings are inserted before chunking, so they
feed chunk context, the structural index, section summaries and vectorless retrieval just like
real headings. Their sections are marked `is_pseudo_section = true`, and the model calls are
traced in the ingest `pipeline_runs.raw_output.heading_generation_trace`. Without an LLM key (or
on a bad response) ingestion falls back to topic-shift pseudo-sections. Configure under
`generated_headings` in `config/ingestion.yaml`.

### Vectorless retrieval (section evidence with citations)

`app/retrieval/vectorless.py` finds in-document evidence without embeddings, in the style of
[simple-vectorless-rag](https://github.com/UddyOrizu/simple-vectorless-rag): the model reads the
section tree's titles + summaries like a table of contents, picks the sections most likely to hold
evidence, reads them, and extracts **verbatim** quotes that support or contradict the claim. Every
quote is checked against the source text (fuzzy on wording, exact on numbers) and dropped if it
doesn't appear there or is just the claim's own sentence quoted back. Survivors become
`internal_vectorless` evidence citing the section path and page, e.g.
`document_section:<id> page 5 section 'Financials > Revenue'`, with the navigator/quoter calls
traced in `agent_traces`. It runs first in the internal-evidence ladder; exact lookup, embedding
search and the cross-reference resolver are only tried when it finds nothing verifiable. Tune or
disable it in `config/retrieval.yaml`.

Inside `docker-compose.yml`, the `api` service talks to Postgres over the Docker network
(`postgres:5432`); from your host machine (e.g. running `alembic` or `pytest` locally), Postgres
is reachable at `localhost:5433` — that's why the two `DATABASE_URL` values above differ only in
host/port.

## Database
https://pdf2image.readthedocs.io/en/latest/installation.html


Postgres runs via Docker Compose (`pgvector/pgvector:pg16`, port `5433` on the host). Schema
migrations use Alembic:

```bash
cd poc/backend
alembic upgrade head          # apply all migrations
alembic downgrade base        # drop everything (destructive)
```

Inspect the database directly:

```bash
docker exec -it poc-postgres-1 psql -U poc -d claim_checker
```

## Running the backend outside Docker

Useful for faster iteration (no image rebuilds) or running the test suite:

```bash
cd poc/backend
source .venv/bin/activate                      # after the one-time setup above
export DATABASE_URL=postgresql+asyncpg://poc:poc@localhost:5433/claim_checker
export ANTHROPIC_API_KEY=...                    # optional
uvicorn app.main:app --reload --port 8000
```

Make sure the `postgres` container is running (`docker compose up -d postgres`) — you don't need
the `api` container running at the same time; stop it first if you want to avoid port conflicts
on 8000.

## Testing end to end

### Automated backend tests

```bash
cd poc/backend
pytest                       # runs against the postgres container on localhost:5433
```

Tests that need a real `ANTHROPIC_API_KEY` (claim decomposition, agent-based verification, full
reference-set validation) are marked to skip automatically when the key isn't set — you'll see
`X passed, Y skipped` rather than failures. Set `ANTHROPIC_API_KEY` before running `pytest` to
exercise those too.

### Frontend build check

```bash
cd poc/frontend
npm run build                # tsc type-check + vite build
```

### Manual end-to-end walkthrough (recommended)

With Postgres, the API, and the frontend dev server all running:

1. Open `http://localhost:5173` — lands on the (empty, on first run) document history page.
2. Click **Upload new document** and drop in one of the fixtures under
   `backend/tests/fixtures/` (e.g. `sample_report.docx` for a quick pass, or `large_report.pdf`
   to watch real percentage progress on an 80-page document).
3. You're redirected to the document's page, which shows live progress (stage name + real
   `pages_done`/`pages_total` percentage on PDFs) via the SSE endpoint, then automatically
   switches to the review view once processing completes.
4. Click any highlighted claim to open its review panel — scope, domain (and which tier resolved
   it), the deterministic recomputed figures or the verifier/challenger reasoning side by side,
   evidence, and tool calls.
5. Go back to the history page — the document now appears with an accurate claim-verdict summary
   and can be reopened at any time.

Without an API key, ingestion completes fully and claims are still extracted for any simple,
single-fact sentence (no LLM needed); claims needing decomposition or agent verification stay
`pending` and any reverify attempt returns a clear `503 BLOCKED-CREDENTIALS` error rather than a
generic failure.

### Exercising individual pipeline stages via CLI

Each stage of the pipeline is also runnable standalone from `poc/backend`, useful for isolating a
specific piece without going through the API:

```bash
python scripts/run_ingest.py tests/fixtures/sample_report.docx
python scripts/run_extract.py "Revenue grew 12% YoY, driven by APAC expansion." --context "Financial Highlights"
python scripts/run_classify.py "our approach complies with GDPR" --section "Executive Summary"
python scripts/run_verify.py --adhoc "Revenue grew 12%" --evidence "Current: \$112M, Prior: \$100M"
python scripts/compare_runs.py --document <uuid> --snapshot   # then edit a config value and --snapshot again, then diff the two hashes
```

## Troubleshooting

- **Port already in use (5433, 8000, or 5173)** — something else is bound to that port; stop it
  or adjust the port mapping in `docker-compose.yml` / `vite.config.ts`.
- **`alembic upgrade head` can't connect** — confirm `docker compose ps` shows `postgres` as
  `healthy`, and that you're using the `localhost:5433` connection string (not the in-Docker
  `postgres:5432` one) when running Alembic from your host machine.
- **API container fails to build / build is slow** — the first build downloads `en_core_web_trf`
  (a transformer-based spaCy model) and a CPU-only PyTorch wheel; expect a few minutes and a
  ~3GB image. Subsequent builds are cached.
- **Uploads stuck at `queued`/`processing`/`ingested`** — check `docker logs poc-api-1` (or your
  local uvicorn output); background processing runs in-process and logs exceptions rather than
  failing silently, but a stuck status with no error usually means it's still working through a
  large document's claim extraction (CPU-bound, thread-offloaded — the API stays responsive to
  other requests while this runs, it just takes real wall-clock time for documents with many
  sentences).
