from sqlalchemy import Index

from app.models.agent_trace import AgentTrace
from app.models.base import Base
from app.models.claim import Claim
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.document_section import DocumentSection
from app.models.evidence import Evidence
from app.models.extracted_table import ExtractedTable
from app.models.pipeline_run import PipelineRun
from app.models.verdict import Verdict

__all__ = [
    "Base",
    "Document",
    "DocumentSection",
    "DocumentChunk",
    "ExtractedTable",
    "Claim",
    "Evidence",
    "Verdict",
    "PipelineRun",
    "AgentTrace",
]

Index("idx_chunks_document", DocumentChunk.document_id)
Index("idx_claims_document", Claim.document_id)
Index("idx_evidence_claim", Evidence.claim_id)
Index("idx_verdicts_claim", Verdict.claim_id)
Index("idx_pipeline_runs_document", PipelineRun.document_id, PipelineRun.stage)
Index("idx_agent_traces_claim", AgentTrace.claim_id)
Index("idx_documents_created_at", Document.created_at.desc())
