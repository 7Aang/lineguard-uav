"""Create LineGuard business and audit tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_lineguard_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lineguard_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("calibration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lineguard_assets_asset_type", "lineguard_assets", ["asset_type"])
    op.create_table(
        "lineguard_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("asset_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("current_stage", sa.String(80), nullable=True),
        sa.Column("task_spec", sa.JSON(), nullable=True),
        sa.Column("mission_plan", sa.JSON(), nullable=True),
        sa.Column("video_analysis", sa.JSON(), nullable=True),
        sa.Column("risk_assessment", sa.JSON(), nullable=True),
        sa.Column("report_id", sa.String(36), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lineguard_tasks_status", "lineguard_tasks", ["status"])
    op.create_table(
        "lineguard_tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lineguard_tool_calls_task_id",
        "lineguard_tool_calls",
        ["task_id"],
    )
    op.create_table(
        "lineguard_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reviewer", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lineguard_reviews_task_id", "lineguard_reviews", ["task_id"])
    op.create_table(
        "lineguard_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(30), nullable=False),
        sa.Column("json_path", sa.Text(), nullable=False),
        sa.Column("markdown_path", sa.Text(), nullable=False),
        sa.Column("html_path", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_lineguard_reports_task_id", "lineguard_reports", ["task_id"])
    op.create_table(
        "lineguard_trace_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lineguard_trace_events_task_id",
        "lineguard_trace_events",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_lineguard_trace_events_task_id", table_name="lineguard_trace_events")
    op.drop_table("lineguard_trace_events")
    op.drop_index("ix_lineguard_reports_task_id", table_name="lineguard_reports")
    op.drop_table("lineguard_reports")
    op.drop_index("ix_lineguard_reviews_task_id", table_name="lineguard_reviews")
    op.drop_table("lineguard_reviews")
    op.drop_index("ix_lineguard_tool_calls_task_id", table_name="lineguard_tool_calls")
    op.drop_table("lineguard_tool_calls")
    op.drop_index("ix_lineguard_tasks_status", table_name="lineguard_tasks")
    op.drop_table("lineguard_tasks")
    op.drop_index("ix_lineguard_assets_asset_type", table_name="lineguard_assets")
    op.drop_table("lineguard_assets")
