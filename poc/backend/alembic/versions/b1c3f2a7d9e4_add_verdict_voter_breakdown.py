"""add verdict voter_breakdown column

Revision ID: b1c3f2a7d9e4
Revises: a9682d23b0ba
Create Date: 2026-08-28

"""

from alembic import op

revision = "b1c3f2a7d9e4"
down_revision = "a9682d23b0ba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE verdicts
        ADD COLUMN voter_breakdown JSONB;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE verdicts
        DROP COLUMN IF EXISTS voter_breakdown;
        """
    )
