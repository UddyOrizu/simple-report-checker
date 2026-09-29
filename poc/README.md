# Claim Checker POC

A pipeline that ingests business reports (`.docx`/`.pdf`), converts them to Markdown, extracts
factual claims, classifies each claim's domain and scope (internal / external / both), finds
evidence, and verifies each claim, with full tracing and a review UI.

In-document evidence comes from **vectorless retrieval**: the model reads the document's section
tree like a table of contents, picks the right sections, and extracts verbatim quotes that support
or contradict the claim. Every quote is checked against the source text before it's accepted, and
cited to its section and page. Verification is either deterministic arithmetic recomputation or
LLM review (a verifier/challenger pair, or a multi-model vote panel for internal claims).

## How a document flows through the pipeline

```
upload (.pdf / .docx)
  → convert to Markdown, in page batches       marker-pdf, or pdfplumber/Tesseract; mammoth for Word
  → generate headings (if the document has none) LLM
  → chunk + build the section tree + summarize  heading levels, preamble, long sections split into parts
  → store sections, chunks, embeddings
  → extract claims → route each (internal / external / both)
  → verify each claim
       evidence: vectorless retrieval → keyword lookup → embedding search → cross-reference
       verdict:  deterministic arithmetic, vote panel (internal), or verifier + challenger
  → review UI: verdicts, reasoning, cited quotes you can open in context
```

Every document takes this one path whatever its size. Only conversion runs incrementally, with
live progress events between page batches.

## Prerequisites

- Docker + Docker Compose
- Python 3.11 (only needed to run the backend outside Docker)
- Node 18+ and npm (for the frontend)
- Tesseract and Poppler, for OCR of scanned PDFs when running outside Docker; see the
  [pdf2image install notes](https://pdf2image.readthedocs.io/en/latest/installation.html). The
  Docker image already includes both.
- An `EXA_API_KEY`. **The API won't start without one**: the verification agents create an Exa
  search client when the app loads. Any non-empty value lets it start, but real web search needs a
  real key.
- Optional LLM and embedding keys (see [Running without API keys](#running-without-api-keys)):
  - an [Anthropic](https://console.anthropic.com/), [OpenAI](https://platform.openai.com/api-keys)
    or Azure OpenAI key for the LLM stages (pick via `LLM_PROVIDER`)
  - an OpenAI key for embeddings, used whatever `LLM_PROVIDER` is set to
  - a `SERPER_API_KEY` for the external-evidence search agent

## Quick start

```bash
cd poc
cp .env.example .env        # fill in your keys (see Environment variables)
docker compose up -d        # starts Postgres + the API (builds the API image on first run)
```

Wait for both services to report healthy, then set up the backend and run migrations once:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .                    # add ".[marker]" for marker-pdf PDF conversion (see below)
python -m spacy download en_core_web_sm
python -m spacy download en_core_web_trf
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
`GET http://localhost:8000/docs` renders interactive docs for every endpoint.

## Environment variables

Set in `poc/.env` (used by `docker compose`) or exported directly if running the backend outside
Docker:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://poc:poc@localhost:5433/claim_checker` | Postgres connection (async driver) |
| `LLM_PROVIDER` | `anthropic` | Which backend every Agno agent uses: `anthropic`, `openai` or `azure` |
| `ANTHROPIC_API_KEY` | *(empty)* | Required for any LLM-backed stage when `LLM_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | *(empty)* | Required for any LLM-backed stage when `LLM_PROVIDER=openai`. Also enables embeddings (always OpenAI's `text-embedding-3-small`, whatever `LLM_PROVIDER` is). Without it, chunks are stored without embeddings and embedding search / claim dedup are skipped |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Only used when `LLM_PROVIDER=openai`; point at a proxy/gateway if needed |
| `AZURE_OPENAI_KEY` / `AZURE_OPENAI_BASE_URL` | *(empty)* | Credentials and endpoint when `LLM_PROVIDER=azure` |
| `AZURE_OPENAI_MODEL_ID` / `AZURE_OPENAI_MINI_MODEL_ID` | falls back to the OpenAI model IDs | Azure **deployment names** for the standard/mini tiers |
| `ANTHROPIC_MODEL_ID` / `ANTHROPIC_MINI_MODEL_ID` | `claude-sonnet-4-5-20250929` / `claude-haiku-4-5-20251001` | Override the standard/mini model tier for Anthropic |
| `OPENAI_MODEL_ID` / `OPENAI_MINI_MODEL_ID` | `gpt-4o` / `o3` | Override the standard/mini model tier for OpenAI |
| `EXA_API_KEY` | *(none)* | **Required for the API to start** (see Prerequisites). Web search for the verifier, challenger and vote panel |
| `SERPER_API_KEY` | *(empty)* | Web search for the external-evidence search agent |
| `INTERNAL_VERIFICATION_VOTERS` | `standard,mini,fino1` | Which models sit on the internal-claim vote panel (see below) |
| `HF_TOKEN` | *(empty)* | Hugging Face token for the `fino1` voter (TheFinAI/Fin-o1-8B). A missing token just drops that voter from the vote |
| `FINO1_MODEL_ID` | `TheFinAI/Fin-o1-8B` | Override the specialized finance model used by the `fino1` voter |
| `HF_INFERENCE_BASE_URL` | *(empty; uses HF's shared routing)* | Set only if Fin-o1-8B is deployed as a dedicated HF Inference Endpoint |
| `TORCH_DEVICE` | auto (`cuda` → `mps` → `cpu`) | Device for marker-pdf's models, if installed |
| `STORAGE_DIR` | `./storage` | Where uploaded documents are stored on disk |

## Configuration

Behaviour is tuned in `backend/config/` (and prompts in `backend/prompts/`). Every file there is
hashed into `config_hash` on pipeline runs and agent traces, so results record exactly which
settings produced them.

| File | Controls |
|---|---|
| `ingestion.yaml` | Markdown conversion (`markdown_conversion`), generated headings (`generated_headings`), section sizes (`max_section_chars`), OCR thresholds, progress interval, summary lengths, write batch size |
| `retrieval.yaml` | Vectorless retrieval: sections per claim, text per read, reads per claim, quote-match threshold, on/off |
| `domain_registry.yaml` | Per (domain, claim type): where evidence comes from and whether verification is `deterministic` or `agent` |
| `thresholds.yaml` | Arithmetic tolerance for deterministic verification |

## Ingestion

### Markdown conversion

Uploads are converted to Markdown before parsing, so the section tree gets a real heading hierarchy
(`#`/`##`/`###` levels) instead of guessing headings from font sizes:

- **PDF**: [marker-pdf 0.3.2](https://pypi.org/project/marker-pdf/0.3.2/), an optional extra
  because it pulls in torch plus surya's layout/OCR/table models (several GB, downloaded on first
  use) and pins older Pillow/regex. Install with `pip install -e ".[marker]"` (or
  `docker compose build --build-arg INSTALL_EXTRAS=marker`). Without it, PDFs fall back to the
  pdfplumber/Tesseract parser. marker is GPL-3.0 licensed, so check that suits your distribution.
- **Word (.docx)**: mammoth + markdownify, always available. Legacy `.doc` files aren't
  supported; save them as `.docx` first.

Both are toggled under `markdown_conversion` in `ingestion.yaml`. The rendered Markdown is stored on
`documents.markdown`, and each section's own body on `document_sections.content`.

Only conversion runs incrementally (`app/ingestion/conversion.py`). marker converts
`marker_pages_per_batch` pages per call, the native parser one page at a time, and a `.docx` in one
go, with `ingest_progress` events in between. Everything after that runs once over the whole
document, and chunks are embedded and written `persist_chunk_batch` at a time so memory stays
bounded. marker drops a blank page's page break, so when a batch comes back with fewer page breaks
than pages, its page numbers are re-derived from the PDF's own text layer. Concurrent uploads share
marker's models and take turns batch by batch.

### Section tree

Headings become a tree of sections, each with a `level` and `parent_id`. Nothing is left outside it:

- Text before the first heading gets its own **Preamble** section.
- A section whose body is longer than `max_section_chars` is split into "(part k of n)" sections.
- A section with no text of its own (a heading followed straight by a subheading) gets the summary
  "Contains: <subsection titles>" instead of an LLM summary, which would only invent content.
- Documents at or under `short_document_page_threshold` pages get no tree; vectorless retrieval
  reads them whole.

### Generated section headings

Documents longer than `short_document_page_threshold` with fewer than two real headings (none at
all, or only a title) get LLM-generated ones (`app/ingestion/heading_generator.py`). The model reads
the paragraphs in order, in windows for long documents, and decides where the topic changes and
what to call each part. The headings are inserted before chunking, so they feed chunk context, the
section tree, summaries and vectorless retrieval just like real headings. Their sections are marked
`is_pseudo_section = true`, and the model calls are traced in the ingest run's
`pipeline_runs.raw_output.heading_generation_trace`. Without an LLM key, or on a bad response,
ingestion falls back to topic-shift pseudo-sections. Configure under `generated_headings` in
`ingestion.yaml`.

## Evidence and verification

### Vectorless retrieval (section evidence with citations)

`app/retrieval/vectorless.py` finds in-document evidence without embeddings, in the style of
[simple-vectorless-rag](https://github.com/UddyOrizu/simple-vectorless-rag):

1. **Navigate**: the model reads the section tree's titles and summaries and picks up to
   `max_sections` sections likely to hold the evidence.
2. **Read**: it reads each chosen section with its subsections, in parts of `max_section_chars`
   when long. Reads are capped at `max_reads_per_claim` per claim; if the cap is hit, the unread
   remainder is logged, never silently dropped.
3. **Quote**: it extracts verbatim passages that support, contradict, or give context for the claim.
4. **Verify**: each quote is checked against the source text, allowing small wording differences
   but requiring every number to match exactly. Quotes that don't appear in the source, or that
   just repeat the claim's own sentence, are dropped.
5. **Cite**: survivors become `internal_vectorless` evidence citing the most specific section and
   page, e.g. `document_section:<id> page 5 section 'Financials > Revenue'`.

Each citation is also stored as structured fields on `evidence` (`section_id`, `section_path`,
`page_number`, `quote`, `stance`). In the claim review panel, a cited item shows its stance and
quote, and its "§ section · page" link opens the section
(`GET /documents/{id}/sections/{section_id}`) with the quote highlighted. The navigator and quoter
calls are traced in `agent_traces`. Tune or disable retrieval in `retrieval.yaml`.

### Evidence ladder

Internal evidence is gathered by trying each step in order and stopping at the first that finds
something:

1. vectorless retrieval (above)
2. exact keyword lookup of the claim's `requires` phrases
3. embedding search over those phrases (needs `OPENAI_API_KEY`)
4. the cross-reference resolver, which also follows explicit pointers like "see Table 2"

For internal claims, if all four find nothing, the whole document goes to the vote panel with the
claim's own sentence redacted, so a voter can't "verify" the claim by reading it back to itself.

### Verification

- **Deterministic**: when `domain_registry.yaml` sets `verification_method: deterministic` for a
  (domain, claim type) pair, the claim's percentage is recomputed from the document's tables,
  within `thresholds.yaml`'s tolerance. It's exact and needs no LLM. The shipped registry currently
  routes every pair to `agent`.
- **Internal claims** (`scope="internal"`): a multi-model vote panel
  (`app/agents/internal_vote_panel.py`). Every configured voter reads the same evidence
  independently and votes `supported`/`contradicted`/`insufficient`. The majority wins, and a tie
  (including a 3-way split) resolves to `disputed` at zero confidence.
- **External / both**: internal and external evidence are gathered concurrently (external search:
  search → scrape → source credibility scoring), then a verifier and challenger review it and their
  verdicts are reconciled.

## Running without API keys

- **No LLM key**: ingestion, conversion, chunking and the section tree all complete, without
  section summaries or generated headings. Simple single-fact sentences are still extracted as
  claims. Sentences needing decomposition are skipped. A simple claim that mentions a date, number
  or similar entity needs the LLM router, so it's kept as `pending` with a provisional scope of
  `both`. Verification needing an LLM leaves claims `pending`, and reverify returns a clear
  `503 BLOCKED-CREDENTIALS`.
- **No `OPENAI_API_KEY`**: chunks are stored without embeddings (a warning is logged once), and
  embedding search and duplicate-claim checks are skipped. Everything else is unaffected.

Pending claims keep their provisional scope: reverifying a claim doesn't route it again. Once you add
a key, reprocess those documents (below) to route them properly.

## Reprocessing existing documents

`scripts/reprocess_documents.py` re-runs the full pipeline for documents already in the database.
Use it for documents ingested before Markdown conversion existed (they have no Markdown or section
content, so vectorless retrieval can't read them), or to route claims left pending without a key.

```bash
cd poc/backend
python scripts/reprocess_documents.py                                   # list documents with no Markdown
python scripts/reprocess_documents.py --document <uuid> --confirm       # reprocess one document
python scripts/reprocess_documents.py --all                             # list every document
```

This is **destructive**: a document's claims, verdicts, evidence and traces are deleted and
regenerated, with the LLM cost that implies. Without `--confirm` it only lists what it would do.

## Database

Postgres runs via Docker Compose (`pgvector/pgvector:pg16`, port `5433` on the host). Inside
`docker-compose.yml` the `api` service talks to Postgres over the Docker network (`postgres:5432`);
from your host machine (e.g. running `alembic` or `pytest` locally) it's at `localhost:5433`.
That's why the two `DATABASE_URL` values differ only in host/port.

Schema migrations use Alembic:

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
export EXA_API_KEY=...                          # required for the app to start
export ANTHROPIC_API_KEY=...                    # optional
uvicorn app.main:app --reload --port 8000
```

Make sure the `postgres` container is running (`docker compose up -d postgres`). You don't need the
`api` container at the same time; stop it first to avoid port conflicts on 8000.

## Testing

### Automated backend tests

```bash
cd poc/backend
EXA_API_KEY=dummy pytest     # runs against the postgres container on localhost:5433
```

The suite needs Postgres (with migrations applied) and the spaCy model `en_core_web_sm`, but no API
keys or network. It takes about 30 seconds. Tests that need a real LLM key (claim decomposition,
agent-based verification, full reference-set validation) skip automatically without one, so you'll
see `X passed, Y skipped`. Set your provider's key before running `pytest` to exercise those too.

### Frontend build check

```bash
cd poc/frontend
npm run build                # tsc type-check + vite build
npm run lint
```

### Manual end-to-end walkthrough (recommended)

With Postgres, the API, and the frontend dev server all running:

1. Open `http://localhost:5173`. It lands on the document history page (empty on first run).
2. Click **Upload new document** and drop in one of the fixtures under
   `backend/tests/fixtures/` (e.g. `sample_report.docx` for a quick pass, or `large_report.pdf`
   to watch real percentage progress on an 80-page document).
3. You're redirected to the document's page, which shows live progress (stage name, and real
   `pages_done`/`pages_total` percentage on PDFs) via the SSE endpoint, then switches to the review
   view once processing completes.
4. Click any highlighted claim to open its review panel: scope, domain (and which tier resolved
   it), the deterministic figures or the verifier/challenger reasoning side by side, evidence, and
   tool calls. For evidence from vectorless retrieval, click the "§ section · page" link to see the
   quote highlighted in its section.
5. Go back to the history page. The document now appears with its claim-verdict summary and can
   be reopened at any time.

### Exercising individual pipeline stages via CLI

Each stage is also runnable standalone from `poc/backend`, useful for isolating a specific piece
without going through the API:

```bash
python scripts/run_ingest.py tests/fixtures/sample_report.docx
python scripts/run_extract.py "Revenue grew 12% YoY, driven by APAC expansion." --context "Financial Highlights"
python scripts/run_classify.py "our approach complies with GDPR" --section "Executive Summary"
python scripts/run_verify.py --adhoc "Revenue grew 12%" --evidence "Current: \$112M, Prior: \$100M"
python scripts/compare_runs.py --document <uuid> --snapshot   # then edit a config value and --snapshot again, then diff the two hashes
python scripts/reprocess_documents.py                          # see Reprocessing existing documents
```

## Troubleshooting

- **API won't start: `API key must be provided ... EXA_API_KEY`**: set `EXA_API_KEY` (see
  Prerequisites).
- **`OSError: [E050] Can't find model 'en_core_web_sm'`**: run
  `python -m spacy download en_core_web_sm` in the backend's virtualenv.
- **Port already in use (5433, 8000, or 5173)**: something else is bound to that port; stop it or
  adjust the port mapping in `docker-compose.yml` / `vite.config.ts`.
- **`alembic upgrade head` can't connect**: confirm `docker compose ps` shows `postgres` as
  `healthy`, and that you're using the `localhost:5433` connection string (not the in-Docker
  `postgres:5432` one) when running Alembic from your host machine.
- **API container fails to build / build is slow**: the first build downloads `en_core_web_trf`
  (a transformer-based spaCy model) and a CPU-only PyTorch wheel; expect a few minutes and a ~3GB
  image. Subsequent builds are cached.
- **PDF conversion is very slow, or re-downloads models**: marker runs on the CPU in the default
  image, and its models download into the container, so they're fetched again whenever the container
  is recreated. Use a GPU host (`TORCH_DEVICE=cuda`) and mount a volume for the Hugging Face cache,
  or leave marker uninstalled to use the faster native parser.
- **"markdown_conversion.pdf is enabled but marker-pdf isn't installed"**: expected when marker
  isn't installed; PDFs use the native parser. Install the `marker` extra, or set
  `markdown_conversion.pdf: false` to silence it.
- **Evidence has no page number, or vectorless retrieval finds nothing for an older document**: the
  document was ingested before Markdown conversion existed. Reprocess it (see above).
- **Uploads stuck at `queued`/`processing`/`ingested`**: check `docker logs poc-api-1` (or your
  local uvicorn output). Background processing runs in-process and logs exceptions rather than
  failing silently; a stuck status with no error usually means it's still working through a large
  document's conversion or claim extraction (thread-offloaded, so the API stays responsive
  meanwhile).
