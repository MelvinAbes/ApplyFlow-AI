from sqlmodel import Session, select

from app.automation.field_mapper import normalize_label
from app.models import FieldMappingEntry
from app.models.entities import utc_now


class FieldMappingService:
    """Exact, user-confirmed label semantics kept separate from reusable answers."""

    def __init__(self, session: Session):
        self.session = session

    def match(self, label: str) -> FieldMappingEntry | None:
        normalized = normalize_label(label)
        if not normalized:
            return None
        return self.session.exec(
            select(FieldMappingEntry).where(
                FieldMappingEntry.normalized_label == normalized
            )
        ).first()

    def remember(self, *, label: str, canonical_key: str) -> FieldMappingEntry:
        normalized = normalize_label(label)
        if not normalized:
            raise ValueError("A field label is required")
        existing = self.match(label)
        if existing:
            existing.source_label = label
            existing.canonical_key = canonical_key
            existing.updated_at = utc_now()
            self.session.add(existing)
            self.session.flush()
            return existing
        entry = FieldMappingEntry(
            normalized_label=normalized,
            source_label=label,
            canonical_key=canonical_key,
        )
        self.session.add(entry)
        self.session.flush()
        return entry
