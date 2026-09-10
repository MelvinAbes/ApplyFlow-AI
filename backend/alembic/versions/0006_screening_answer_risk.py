"""Separate sensitive screening verification from answer-based disqualification risk."""

import re

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "0006_screening_answer_risk"
down_revision = "0005_application_recovery"
branch_labels = None
depends_on = None

SENSITIVE_MARKERS = (
    "authorized",
    "authorised",
    "right to work",
    "eligible to work",
    "work permit",
    "visa",
    "sponsorship",
    "language level",
    "degree required",
)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("applicationfield")}
    if "requires_verification" not in columns:
        with op.batch_alter_table("applicationfield") as batch:
            batch.add_column(
                sa.Column(
                    "requires_verification",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    fields = sa.table(
        "applicationfield",
        sa.column("id", sa.String()),
        sa.column("question", sa.String()),
        sa.column("answer", sa.Text()),
        sa.column("disqualifying", sa.Boolean()),
        sa.column("requires_verification", sa.Boolean()),
    )
    rows = bind.execute(
        sa.select(fields.c.id, fields.c.question, fields.c.answer, fields.c.disqualifying)
    ).mappings()
    for row in rows:
        question = row["question"] or ""
        bind.execute(
            fields.update()
            .where(fields.c.id == row["id"])
            .values(
                requires_verification=bool(row["disqualifying"])
                or _requires_verification(question),
                disqualifying=_is_potentially_disqualifying(question, row["answer"]),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("applicationfield") as batch:
        batch.drop_column("requires_verification")


def _requires_verification(question: str) -> bool:
    lowered = question.casefold()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _is_potentially_disqualifying(question: str, answer: str | None) -> bool:
    truth_value = _answer_truth_value(answer)
    if truth_value is None:
        return False
    lowered = re.sub(r"\s+", " ", question.casefold()).strip()
    if any(term in lowered for term in ("need", "require")) and any(
        term in lowered for term in ("visa", "sponsorship", "work permit")
    ):
        return truth_value
    if "sponsorship" in lowered:
        if any(term in lowered for term in ("have", "hold", "secured")):
            return not truth_value
        return False
    authorization_markers = (
        "authorized to work",
        "authorised to work",
        "right to work",
        "eligible to work",
        "work authorization",
        "work authorisation",
    )
    if any(marker in lowered for marker in authorization_markers):
        negated = any(
            marker in lowered
            for marker in (
                "not authorized",
                "not authorised",
                "not eligible to work",
                "no right to work",
                "without authorization",
                "without authorisation",
            )
        ) or bool(
            re.search(
                r"\bnot\b(?:\s+\w+){0,3}\s+(?:authori[sz]ed|eligible)\b",
                lowered,
            )
        )
        return truth_value if negated else not truth_value
    if "visa" in lowered and any(term in lowered for term in ("have", "hold", "valid")):
        return not truth_value
    if "degree" in lowered and any(term in lowered for term in ("have", "hold", "meet")):
        return not truth_value
    if "language level" in lowered and any(term in lowered for term in ("have", "meet", "satisfy")):
        return not truth_value
    return False


def _answer_truth_value(answer: str | None) -> bool | None:
    if not answer:
        return None
    normalized = re.sub(r"\s+", " ", answer.casefold()).strip(" .!?;:")
    if normalized in {"yes", "true", "ja", "authorized", "authorised"} or re.match(
        r"^(yes|true|ja)\b", normalized
    ):
        return True
    if normalized in {"no", "false", "nein", "not authorized", "not authorised"} or re.match(
        r"^(no|false|nein)\b", normalized
    ):
        return False
    return None
