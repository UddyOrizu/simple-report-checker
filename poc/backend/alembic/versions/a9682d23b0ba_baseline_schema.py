"""baseline schema

Revision ID: a9682d23b0ba
Revises:
Create Date: 2026-08-17

"""

from alembic import op

revision = "a9682d23b0ba"
down_revision = None
branch_labels = None
depends_on = None


DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename              TEXT NOT NULL,
  file_type             TEXT NOT NULL CHECK (file_type IN ('docx', 'pdf')),
  storage_path          TEXT NOT NULL,
  file_size_bytes        BIGINT,
  status                TEXT NOT NULL DEFAULT 'queued',
  page_count            INT,
  has_structural_index  BOOLEAN NOT NULL DEFAULT false,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE document_sections (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  title             TEXT,
  is_pseudo_section BOOLEAN NOT NULL DEFAULT false,
  summary           TEXT,
  domain_hint       TEXT,
  order_index       INT NOT NULL,
  page_start        INT,
  page_end          INT
);

CREATE TABLE document_chunks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id      UUID REFERENCES document_sections(id),
  chunk_type      TEXT NOT NULL,
  chunk_text      TEXT NOT NULL,
  embedding       VECTOR(1536) NOT NULL,
  context_capsule TEXT,
  page_number     INT,
  char_start      INT,
  char_end        INT,
  ocr_confidence  REAL
);

CREATE TABLE extracted_tables (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_id    UUID REFERENCES document_sections(id),
  page_number   INT,
  table_data    JSONB NOT NULL,
  parse_status  TEXT NOT NULL DEFAULT 'ok'
);

CREATE TABLE claims (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id        UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id           UUID NOT NULL REFERENCES document_chunks(id),
  claim_text         TEXT NOT NULL,
  source_span        TEXT NOT NULL,
  claim_type         TEXT NOT NULL,
  scope              TEXT NOT NULL,
  requires           JSONB,
  domain             TEXT,
  domain_confidence  REAL,
  domain_source      TEXT,
  embedding          VECTOR(1536),
  status             TEXT NOT NULL DEFAULT 'pending',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evidence (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id         UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  source_type      TEXT NOT NULL,
  source_ref       TEXT NOT NULL,
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
  final_verdict         TEXT NOT NULL,
  final_confidence      REAL NOT NULL,
  severity              TEXT NOT NULL DEFAULT 'info',
  resolved_by           TEXT NOT NULL,
  resolved_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pipeline_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  stage        TEXT NOT NULL,
  config_hash  TEXT NOT NULL,
  input_ref    TEXT,
  raw_output   JSONB NOT NULL,
  duration_ms  INT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_traces (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  claim_id      UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  agent_name    TEXT NOT NULL,
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
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);

CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- reserved for the future claim-cache dedup use of claims.embedding (see schema comment)
CREATE INDEX idx_claims_embedding ON claims USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"""

DROP_DDL = """
DROP TABLE IF EXISTS agent_traces;
DROP TABLE IF EXISTS pipeline_runs;
DROP TABLE IF EXISTS verdicts;
DROP TABLE IF EXISTS evidence;
DROP TABLE IF EXISTS claims;
DROP TABLE IF EXISTS extracted_tables;
DROP TABLE IF EXISTS document_chunks;
DROP TABLE IF EXISTS document_sections;
DROP TABLE IF EXISTS documents;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DROP_DDL)
