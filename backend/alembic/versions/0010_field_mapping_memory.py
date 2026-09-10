"""Keep user-confirmed field meanings separate from reusable answers."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0010_field_mapping_memory"
down_revision = "0009_structured_candidate_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if "fieldmappingentry" in inspector.get_table_names():
        return
    op.create_table(
        "fieldmappingentry",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("normalized_label", sa.String(), nullable=False),
        sa.Column("source_label", sa.String(), nullable=False),
        sa.Column("canonical_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_label", name="uq_field_mapping_normalized_label"
        ),
    )
    op.create_index(
        "ix_fieldmappingentry_normalized_label",
        "fieldmappingentry",
        ["normalized_label"],
        unique=False,
    )
    op.create_index(
        "ix_fieldmappingentry_canonical_key",
        "fieldmappingentry",
        ["canonical_key"],
        unique=False,
    )


def downgrade() -> None:
    if "fieldmappingentry" in inspect(op.get_bind()).get_table_names():
        op.drop_table("fieldmappingentry")
