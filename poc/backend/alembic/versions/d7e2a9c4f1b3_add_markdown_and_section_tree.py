"""add markdown + section tree: documents.markdown, document_sections.level/parent_id/content

Revision ID: d7e2a9c4f1b3
Revises: c4d8e1f6a2b7
Create Date: 2026-09-28

"""

from alembic import op

revision = "d7e2a9c4f1b3"
down_revision = "c4d8e1f6a2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE documents
        ADD COLUMN markdown TEXT;
        """
    )
    op.execute(
        """
        ALTER TABLE document_sections
        ADD COLUMN level INTEGER,
        ADD COLUMN parent_id UUID REFERENCES document_sections(id) ON DELETE CASCADE,
        ADD COLUMN content TEXT;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_sections
        DROP COLUMN content,
        DROP COLUMN parent_id,
        DROP COLUMN level;
        """
    )
    op.execute(
        """
        ALTER TABLE documents
        DROP COLUMN markdown;
        """
    )
