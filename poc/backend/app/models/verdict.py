import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Verdict(Base):
    __tablename__ = "verdicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    verifier_verdict: Mapped[str | None] = mapped_column(String)
    verifier_confidence: Mapped[float | None] = mapped_column(Float)
    verifier_reasoning: Mapped[str | None] = mapped_column(String)
    challenger_verdict: Mapped[str | None] = mapped_column(String)
    challenger_confidence: Mapped[float | None] = mapped_column(Float)
    challenger_reasoning: Mapped[str | None] = mapped_column(String)
    agreement: Mapped[bool | None] = mapped_column(Boolean)
    final_verdict: Mapped[str] = mapped_column(String, nullable=False)
    final_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, server_default="info")
    resolved_by: Mapped[str] = mapped_column(String, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Per-voter breakdown for resolved_by == "ensemble_vote": [{"voter", "verdict", "confidence",
    # "reasoning"}, ...] — the majority result itself lives in verifier_verdict/confidence/
    # reasoning (there's no single "challenger" in a vote panel), this is the audit trail showing
    # how each model voted.
    voter_breakdown: Mapped[list[dict] | None] = mapped_column(JSONB)
