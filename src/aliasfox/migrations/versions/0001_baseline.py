"""Baseline: the single-user schema as shipped before multi-user.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "apple_sessions",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("apple_sessions")
    op.drop_table("users")
