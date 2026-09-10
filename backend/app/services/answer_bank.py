from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

from sqlmodel import Session, select

from app.models import AnswerBankEntry
from app.models.entities import utc_now
from app.schemas.profile import AnswerBankInput

CANONICAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "full_name": ("full name", "legal name", "your name"),
    "phone": ("phone number", "telephone number", "mobile number"),
    "street_name": ("street name", "road name"),
    "house_number": ("house number", "building number", "street number"),
    "address": ("street address", "address line", "street and house number"),
    "work_authorization": (
        "authorized to work",
        "authorised to work",
        "permission to work",
        "legally work in germany",
        "work permit",
    ),
    "available_start_date": ("when can you start", "available start date", "start date"),
    "weekly_hours": ("hours per week", "weekly hours", "how many hours"),
    "german_level": ("german level", "german proficiency", "level of german"),
    "english_level": ("english level", "english proficiency", "level of english"),
    "salary_expectation": ("salary expectation", "expected salary", "salary requirements"),
    "current_student": ("currently enrolled", "current student", "enrolled as a student"),
    "graduation_date": ("when do you graduate", "graduation date", "expected graduation"),
    "relocation_willingness": ("willing to relocate", "open to relocation", "relocate"),
}

CANONICAL_KEY_EQUIVALENTS = {
    "available_start_date": "preferred_start_date",
}


def normalize_question(value: str) -> str:
    value = value.replace("ß", "ss").replace("ẞ", "SS")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    stop_words = {"are", "you", "do", "the", "a", "an", "currently", "have", "to"}
    return " ".join(word for word in value.split() if word not in stop_words)


def infer_canonical_key(question: str) -> str | None:
    normalized = normalize_question(question)
    if _unsafe_work_authorization_question(normalized):
        return None
    for key, patterns in CANONICAL_PATTERNS.items():
        if any(normalize_question(pattern) in normalized for pattern in patterns):
            return key
    return None


class AnswerBankService:
    def __init__(self, session: Session):
        self.session = session

    def list(self) -> list[AnswerBankEntry]:
        return list(self.session.exec(select(AnswerBankEntry).order_by(AnswerBankEntry.question)))

    def create(self, payload: AnswerBankInput) -> AnswerBankEntry:
        entry = AnswerBankEntry(
            **payload.model_dump(), normalized_question=normalize_question(payload.question)
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def update(self, entry: AnswerBankEntry, payload: AnswerBankInput) -> AnswerBankEntry:
        for field, value in payload.model_dump().items():
            setattr(entry, field, value)
        entry.normalized_question = normalize_question(payload.question)
        entry.updated_at = utc_now()
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def match(
        self,
        question: str,
        *,
        canonical_key: str | None = None,
        threshold: float = 0.9,
    ) -> tuple[AnswerBankEntry, float] | None:
        normalized = normalize_question(question)
        if not normalized or _unsafe_work_authorization_question(normalized):
            return None
        inferred = canonical_key or infer_canonical_key(question)
        expected_key = _canonical_family(inferred) if inferred else None
        best: tuple[AnswerBankEntry, float] | None = None
        for entry in self.list():
            entry_key = _canonical_family(entry.canonical_key)
            if expected_key and entry_key != expected_key:
                continue
            variants = [entry.normalized_question, *(normalize_question(a) for a in entry.aliases)]
            if normalized in variants:
                return entry, 1.0
            if expected_key and entry_key == expected_key:
                return entry, 0.98
            # Without a known semantic category, only custom remembered questions may use
            # fuzzy reuse. This prevents lexical accidents such as House Number -> Phone
            # number while retaining reuse for near-identical open-ended questions.
            if expected_key is None and not entry.canonical_key.startswith("custom_"):
                continue
            score = max(SequenceMatcher(None, normalized, variant).ratio() for variant in variants)
            if best is None or score > best[1]:
                best = (entry, score)
        return best if best and best[1] >= threshold else None

    def remember(
        self,
        *,
        question: str,
        answer: str,
        canonical_key: str | None,
        aliases: list[str],
    ) -> AnswerBankEntry:
        normalized = normalize_question(question)
        key = canonical_key or infer_canonical_key(question)
        if not key:
            key = f"custom_{hashlib.sha256(normalized.encode()).hexdigest()[:12]}"
        existing = self.session.exec(
            select(AnswerBankEntry).where(
                AnswerBankEntry.canonical_key == key,
                AnswerBankEntry.normalized_question == normalized,
            )
        ).first()
        if existing:
            existing.answer = answer
            existing.aliases = sorted(set(existing.aliases) | set(aliases))
            existing.updated_at = utc_now()
            self.session.add(existing)
            self.session.commit()
            self.session.refresh(existing)
            return existing
        entry = AnswerBankEntry(
            canonical_key=key,
            question=question,
            normalized_question=normalized,
            answer=answer,
            aliases=aliases,
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry


def _unsafe_work_authorization_question(normalized_question: str) -> bool:
    padded = f" {normalized_question} "
    if (
        " not " in padded
        and any(term in padded for term in (" authorized ", " authorised ", " permission "))
    ) or any(
        phrase in normalized_question
        for phrase in (
            "not authorized",
            "not authorised",
            "without authorization",
            "without authorisation",
            "do not permission",
        )
    ):
        return True
    return any(
        jurisdiction in padded
        for jurisdiction in (
            " united states ",
            " usa ",
            " us ",
            " canada ",
            " united kingdom ",
            " uk ",
            " switzerland ",
            " austria ",
            " france ",
            " netherlands ",
            " ireland ",
            " australia ",
            " european union ",
            " eu ",
        )
    )


def _canonical_family(key: str) -> str:
    return CANONICAL_KEY_EQUIVALENTS.get(key, key)
