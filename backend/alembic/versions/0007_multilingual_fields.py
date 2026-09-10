"""Add multilingual field interpretation and explicit optional-field decisions."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0007_multilingual_fields"
down_revision = "0006_screening_answer_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    profile_columns = {
        column["name"] for column in inspector.get_columns("candidateprofile")
    }
    profile_additions = {
        "date_of_birth": sa.Date(),
        "gender": sa.String(),
        "honorific_title": sa.String(),
        "name_suffix": sa.String(),
        "travel_willingness": sa.String(),
        "consider_other_positions": sa.String(),
    }
    with op.batch_alter_table("candidateprofile") as batch:
        for name, column_type in profile_additions.items():
            if name not in profile_columns:
                batch.add_column(sa.Column(name, column_type, nullable=True))

    field_columns = {
        column["name"] for column in inspect(bind).get_columns("applicationfield")
    }
    with op.batch_alter_table("applicationfield") as batch:
        if "translated_question" not in field_columns:
            batch.add_column(sa.Column("translated_question", sa.String(), nullable=True))
        if "source_language" not in field_columns:
            batch.add_column(sa.Column("source_language", sa.String(), nullable=True))
        if "translated_options" not in field_columns:
            batch.add_column(
                sa.Column(
                    "translated_options",
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                )
            )
        if "translation_confidence" not in field_columns:
            batch.add_column(
                sa.Column(
                    "translation_confidence",
                    sa.Float(),
                    nullable=False,
                    server_default="0",
                )
            )
        if "skipped" not in field_columns:
            batch.add_column(
                sa.Column(
                    "skipped", sa.Boolean(), nullable=False, server_default=sa.false()
                )
            )

    if "aifieldinterpretationcache" not in inspect(bind).get_table_names():
        op.create_table(
            "aifieldinterpretationcache",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("cache_key", sa.String(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "cache_key", name="uq_ai_field_interpretation_cache_key"
            ),
        )
        op.create_index(
            op.f("ix_aifieldinterpretationcache_cache_key"),
            "aifieldinterpretationcache",
            ["cache_key"],
            unique=False,
        )
        op.create_index(
            op.f("ix_aifieldinterpretationcache_created_at"),
            "aifieldinterpretationcache",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_aifieldinterpretationcache_created_at"),
        table_name="aifieldinterpretationcache",
    )
    op.drop_index(
        op.f("ix_aifieldinterpretationcache_cache_key"),
        table_name="aifieldinterpretationcache",
    )
    op.drop_table("aifieldinterpretationcache")

    with op.batch_alter_table("applicationfield") as batch:
        batch.drop_column("skipped")
        batch.drop_column("translation_confidence")
        batch.drop_column("translated_options")
        batch.drop_column("source_language")
        batch.drop_column("translated_question")

    with op.batch_alter_table("candidateprofile") as batch:
        batch.drop_column("consider_other_positions")
        batch.drop_column("travel_willingness")
        batch.drop_column("name_suffix")
        batch.drop_column("honorific_title")
        batch.drop_column("gender")
        batch.drop_column("date_of_birth")
