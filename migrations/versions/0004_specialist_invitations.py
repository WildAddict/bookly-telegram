"""Add expiring, single-use specialist invitations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_specialist_invitations"
down_revision = "0003_reminders_one_hour_before"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "specialist_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False, server_default=""),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Minsk"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_specialist_invitations_code_hash", "specialist_invitations", ["code_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_specialist_invitations_code_hash", table_name="specialist_invitations")
    op.drop_table("specialist_invitations")
