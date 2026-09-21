"""Add UAV mission execution and telemetry payload."""

import sqlalchemy as sa
from alembic import op

revision: str = "0002_mission_execution"
down_revision: str | None = "0001_lineguard_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lineguard_tasks",
        sa.Column("mission_execution", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lineguard_tasks", "mission_execution")
