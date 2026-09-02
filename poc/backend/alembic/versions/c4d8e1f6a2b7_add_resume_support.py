"""add resume support: documents.failed_stage, document_chunks.claims_extracted

Revision ID: c4d8e1f6a2b7
Revises: b1c3f2a7d9e4
Create Date: 2026-09-02

"""

from alembic import op

revision = "c4d8e1f6a2b7"
down_revision = "b1c3f2a7d9e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
        ADD COLUMN failed_stage VARCHAR;
        """
    )
    op.execute(
        """
        ALTER TABLE document_chunks
        ADD COLUMN claims_extracted BOOLEAN NOT NULL DEFAULT false;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks
        DROP COLUMN IF EXISTS claims_extracted;
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        DROP COLUMN IF EXISTS failed_stage;
        """
    )
