"""Cache grounded application-question answers."""

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0003_cache_ai_answers"
down_revision = "0002_harden_application_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "aianswercache" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "aianswercache",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cache_key", sa.String(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key", name="uq_ai_answer_cache_key"),
    )
    op.create_index("ix_aianswercache_cache_key", "aianswercache", ["cache_key"])
    op.create_index("ix_aianswercache_created_at", "aianswercache", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_aianswercache_created_at", table_name="aianswercache")
    op.drop_index("ix_aianswercache_cache_key", table_name="aianswercache")
    op.drop_table("aianswercache")
