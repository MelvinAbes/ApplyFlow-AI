"""Add visible AI resolution diagnostics for application fields."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0005_application_recovery"
down_revision = "0004_multistep_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("applicationfield")}
    with op.batch_alter_table("applicationfield") as batch:
        if "resolution_message" not in columns:
            batch.add_column(sa.Column("resolution_message", sa.Text(), nullable=True))
        if "ai_retryable" not in columns:
            batch.add_column(
                sa.Column("ai_retryable", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    with op.batch_alter_table("applicationfield") as batch:
        batch.drop_column("ai_retryable")
        batch.drop_column("resolution_message")
