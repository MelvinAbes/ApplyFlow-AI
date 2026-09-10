"""Store street name and house number as separate candidate facts."""

import re

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0009_structured_candidate_address"
down_revision = "0008_archived_application_restarts"
branch_labels = None
depends_on = None


def _split(address: str) -> tuple[str, str] | None:
    cleaned = " ".join(address.split())
    if not cleaned or any(separator in cleaned for separator in (",", ";")):
        return None
    match = re.fullmatch(
        r"(?P<street>.*[A-Za-zÀ-ÖØ-öø-ÿß])\s+"
        r"(?P<number>\d+\s*[A-Za-z]?(?:\s*[/-]\s*\d+\s*[A-Za-z]?)?)",
        cleaned,
    )
    if not match or len(match.group("street").strip()) < 2:
        return None
    number = re.sub(r"\s+", "", match.group("number").strip())
    return match.group("street").strip(), number


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("candidateprofile")}
    with op.batch_alter_table("candidateprofile") as batch:
        if "street_name" not in columns:
            batch.add_column(sa.Column("street_name", sa.String(), nullable=True))
        if "house_number" not in columns:
            batch.add_column(sa.Column("house_number", sa.String(), nullable=True))

    profile = sa.table(
        "candidateprofile",
        sa.column("id", sa.String()),
        sa.column("address", sa.String()),
        sa.column("street_name", sa.String()),
        sa.column("house_number", sa.String()),
    )
    rows = bind.execute(
        sa.select(profile.c.id, profile.c.address).where(profile.c.address.is_not(None))
    )
    for row in rows:
        parsed = _split(row.address)
        if parsed:
            bind.execute(
                profile.update()
                .where(profile.c.id == row.id)
                .values(street_name=parsed[0], house_number=parsed[1])
            )


def downgrade() -> None:
    columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("candidateprofile")
    }
    with op.batch_alter_table("candidateprofile") as batch:
        if "house_number" in columns:
            batch.drop_column("house_number")
        if "street_name" in columns:
            batch.drop_column("street_name")
