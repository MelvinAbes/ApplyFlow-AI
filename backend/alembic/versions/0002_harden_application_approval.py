"""Bind approvals to an exact application form and payload."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0002_harden_application_approval"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    application_columns = {column["name"] for column in inspector.get_columns("application")}
    field_columns = {column["name"] for column in inspector.get_columns("applicationfield")}

    with op.batch_alter_table("application") as batch:
        if "ats_adapter" not in application_columns:
            batch.add_column(sa.Column("ats_adapter", sa.String(), nullable=True))
        if "form_fingerprint" not in application_columns:
            batch.add_column(sa.Column("form_fingerprint", sa.String(), nullable=True))
        if "approved_payload_hash" not in application_columns:
            batch.add_column(sa.Column("approved_payload_hash", sa.String(), nullable=True))
        if "approval_consumed_at" not in application_columns:
            batch.add_column(sa.Column("approval_consumed_at", sa.DateTime(), nullable=True))

    if "options" not in field_columns:
        with op.batch_alter_table("applicationfield") as batch:
            batch.add_column(
                sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
            )

    unique_names = {
        constraint["name"] for constraint in inspect(bind).get_unique_constraints("application")
    }
    if "uq_application_job" not in unique_names:
        with op.batch_alter_table("application") as batch:
            batch.create_unique_constraint("uq_application_job", ["job_id"])


def downgrade() -> None:
    bind = op.get_bind()
    unique_names = {
        constraint["name"] for constraint in inspect(bind).get_unique_constraints("application")
    }
    with op.batch_alter_table("application") as batch:
        if "uq_application_job" in unique_names:
            batch.drop_constraint("uq_application_job", type_="unique")
        batch.drop_column("approval_consumed_at")
        batch.drop_column("approved_payload_hash")
        batch.drop_column("form_fingerprint")
        batch.drop_column("ats_adapter")
    with op.batch_alter_table("applicationfield") as batch:
        batch.drop_column("options")
