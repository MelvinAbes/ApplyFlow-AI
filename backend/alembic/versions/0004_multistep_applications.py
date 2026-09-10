"""Track and safely advance multi-step application forms."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0004_multistep_applications"
down_revision = "0003_cache_ai_answers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    application_columns = {column["name"] for column in inspector.get_columns("application")}
    field_columns = {column["name"] for column in inspector.get_columns("applicationfield")}
    with op.batch_alter_table("application") as batch:
        if "current_step" not in application_columns:
            batch.add_column(
                sa.Column("current_step", sa.Integer(), nullable=False, server_default="1")
            )
        if "form_action" not in application_columns:
            batch.add_column(sa.Column("form_action", sa.String(), nullable=True))
        if "waiting_reason" not in application_columns:
            batch.add_column(sa.Column("waiting_reason", sa.String(), nullable=True))
    with op.batch_alter_table("applicationfield") as batch:
        if "step_index" not in field_columns:
            batch.add_column(
                sa.Column("step_index", sa.Integer(), nullable=False, server_default="1")
            )
        if "active" not in field_columns:
            batch.add_column(
                sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
            )

    indexes = {index["name"] for index in inspect(bind).get_indexes("applicationfield")}
    if "ix_applicationfield_active" not in indexes:
        op.create_index("ix_applicationfield_active", "applicationfield", ["active"])


def downgrade() -> None:
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("applicationfield")}
    if "ix_applicationfield_active" in indexes:
        op.drop_index("ix_applicationfield_active", table_name="applicationfield")
    with op.batch_alter_table("applicationfield") as batch:
        batch.drop_column("active")
        batch.drop_column("step_index")
    with op.batch_alter_table("application") as batch:
        batch.drop_column("waiting_reason")
        batch.drop_column("form_action")
        batch.drop_column("current_step")
