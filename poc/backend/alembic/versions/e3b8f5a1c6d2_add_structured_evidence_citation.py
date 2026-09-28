"""add structured evidence citation: evidence.section_id/section_path/page_number/quote/stance

Revision ID: e3b8f5a1c6d2
Revises: d7e2a9c4f1b3
Create Date: 2026-09-28

"""

from alembic import op

revision = "e3b8f5a1c6d2"
down_revision = "d7e2a9c4f1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence
        ADD COLUMN section_id UUID REFERENCES document_sections(id) ON DELETE SET NULL,
        ADD COLUMN section_path VARCHAR,
        ADD COLUMN page_number INTEGER,
        ADD COLUMN quote TEXT,
        ADD COLUMN stance VARCHAR;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence
        DROP COLUMN stance,
        DROP COLUMN quote,
        DROP COLUMN page_number,
        DROP COLUMN section_path,
        DROP COLUMN section_id;
        """
    )
