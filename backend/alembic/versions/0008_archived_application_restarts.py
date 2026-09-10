"""Preserve archived attempts while allowing one fresh active application per job."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0008_archived_application_restarts"
down_revision = "0007_multilingual_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("application")}
    unique_names = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("application")
    }
    index_names = {index["name"] for index in inspector.get_indexes("application")}

    with op.batch_alter_table("application") as batch:
        if "archived_at" not in columns:
            batch.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        if "uq_application_job" in unique_names:
            batch.drop_constraint("uq_application_job", type_="unique")

    if "ix_application_archived_at" not in index_names:
        op.create_index(
            "ix_application_archived_at",
            "application",
            ["archived_at"],
            unique=False,
        )
    if "uq_active_application_job" not in index_names:
        op.create_index(
            "uq_active_application_job",
            "application",
            ["job_id"],
            unique=True,
            sqlite_where=sa.text("archived_at IS NULL"),
            postgresql_where=sa.text("archived_at IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    index_names = {index["name"] for index in inspector.get_indexes("application")}
    if "uq_active_application_job" in index_names:
        op.drop_index("uq_active_application_job", table_name="application")
    if "ix_application_archived_at" in index_names:
        op.drop_index("ix_application_archived_at", table_name="application")

    with op.batch_alter_table("application") as batch:
        batch.drop_column("archived_at")
        batch.create_unique_constraint("uq_application_job", ["job_id"])
