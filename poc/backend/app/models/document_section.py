import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocumentSection(Base):
    __tablename__ = "document_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String)
    is_pseudo_section: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    summary: Mapped[str | None] = mapped_column(String)
    domain_hint: Mapped[str | None] = mapped_column(String)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # Heading hierarchy (1 = top-level). parent_id links a subsection to the nearest preceding
    # section of a shallower level, so vectorless retrieval can navigate the index as a tree.
    level: Mapped[int | None] = mapped_column(Integer)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_sections.id", ondelete="CASCADE")
    )
    # The section's own body (up to its first subsection) rendered as Markdown — what vectorless
    # retrieval reads, and verifies extracted quotes against.
    content: Mapped[str | None] = mapped_column(Text)
