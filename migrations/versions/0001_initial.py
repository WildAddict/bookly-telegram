"""Initial schema."""

from alembic import op

from app.database import models  # noqa: F401
from app.database.base import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    # Keep later migration-owned tables out of this historical initial revision.
    initial_tables = [
        table for table in Base.metadata.sorted_tables if table.name != "specialist_invitations"
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=initial_tables)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
