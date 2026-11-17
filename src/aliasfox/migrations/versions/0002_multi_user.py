"""Multi-user: auth_subject and wrapped_key on users; drop email.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("auth_subject", sa.String(), nullable=True))
        batch.add_column(sa.Column("wrapped_key", sa.LargeBinary(), nullable=True))
    # Pre-existing rows are the single-mode owner; assumes at most one such row
    # (the unique constraint below would fail otherwise).
    op.execute("UPDATE users SET auth_subject = 'single' WHERE auth_subject IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("auth_subject", existing_type=sa.String(), nullable=False)
        batch.create_unique_constraint("uq_users_auth_subject", ["auth_subject"])
        batch.drop_column("email")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email", sa.String(), nullable=True))
        batch.drop_constraint("uq_users_auth_subject", type_="unique")
        batch.drop_column("wrapped_key")
        batch.drop_column("auth_subject")
