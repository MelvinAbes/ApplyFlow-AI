from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from app.ai.question_answerer import QuestionAnswerer
from app.automation.field_detector import DetectedField
from app.automation.field_mapper import map_field
from app.config import Settings
from app.models import Job, Resume
from app.models.enums import AnswerSource
from app.services.answer_bank import AnswerBankService
from app.services.field_mapping import FieldMappingService
from app.services.profile_service import ProfileService


@dataclass(slots=True)
class ResolvedField:
    field: DetectedField
    field_key: str | None
    answer: str | None
    source: AnswerSource
    confidence: float
    upload_path: Path | None = None
    upload_name: str | None = None
    resolution_message: str | None = None
    ai_retryable: bool = False


class FieldResolver:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    async def resolve(self, field: DetectedField, job: Job, resume: Resume | None) -> ResolvedField:
        mapping_memory = FieldMappingService(self.session)
        learned = mapping_memory.match(field.question)
        if learned is None and field.translated_question:
            learned = mapping_memory.match(field.translated_question)
        mapped = (
            (learned.canonical_key, 1.0)
            if learned
            else
            (
                field.canonical_key,
                min(0.98, field.translation_confidence)
                if field.translation_confidence > 0
                else 0.9,
            )
            if field.canonical_key
            else map_field(
                field.translated_question or field.question,
                html_name=field.html_name,
                html_id=field.html_id,
                autocomplete=field.autocomplete,
                placeholder=field.placeholder,
                section=field.section,
            )
            or map_field(
                field.question,
                html_name=field.html_name,
                html_id=field.html_id,
                autocomplete=field.autocomplete,
                placeholder=field.placeholder,
                section=field.section,
            )
        )
        field_key = mapped[0] if mapped else None
        confidence = mapped[1] if mapped else 0.0
        if field_key == "resume":
            if resume:
                return ResolvedField(
                    field=field,
                    field_key=field_key,
                    answer=resume.label,
                    source=AnswerSource.CANDIDATE_PROFILE,
                    confidence=1.0,
                    upload_path=Path(resume.path),
                    upload_name=resume.filename,
                )
            return self._unknown(field, field_key)

        facts = ProfileService(self.session).flat_facts()
        if field_key and facts.get(field_key):
            value = facts[field_key]
            assert isinstance(value, str)
            value = self._transform_value(field_key, value, field)
            return ResolvedField(
                field,
                field_key,
                self._fit_option(value, field),
                AnswerSource.CANDIDATE_PROFILE,
                confidence,
            )

        bank = AnswerBankService(self.session)
        bank_match = bank.match(
            field.translated_question or field.question,
            canonical_key=field_key,
        )
        if bank_match is None and field.translated_question:
            bank_match = bank.match(field.question, canonical_key=field_key)
        if bank_match:
            entry, bank_confidence = bank_match
            return ResolvedField(
                field,
                field_key or entry.canonical_key,
                self._fit_option(entry.answer, field),
                AnswerSource.ANSWER_BANK,
                bank_confidence,
            )

        if field_key == "preferred_start_date" and (
            facts.get("availability") or facts.get("notice_period")
        ):
            return ResolvedField(
                field,
                field_key,
                facts.get("availability") or facts["notice_period"],
                AnswerSource.TRANSFORMATION,
                0.85,
            )

        attempt = await QuestionAnswerer(self.session, self.settings).attempt(
            field.translated_question or field.question,
            job,
            resume,
            field.character_limit,
        )
        ai_answer = attempt.answer
        if ai_answer.status == "ANSWERED" and ai_answer.answer:
            return ResolvedField(
                field,
                field_key,
                ai_answer.answer,
                AnswerSource.LLM_GENERATED,
                ai_answer.confidence,
            )
        return self._unknown(
            field,
            field_key,
            resolution_message=attempt.resolution_message,
            ai_retryable=attempt.retryable,
        )

    @staticmethod
    def _fit_option(value: str, field: DetectedField) -> str:
        if not field.options:
            return value
        exact = next(
            (option for option in field.options if option.casefold() == value.casefold()), None
        )
        if exact:
            return exact
        lowered = value.casefold()
        normalized_value = " ".join(value.casefold().split())
        translated_exact = next(
            (
                original
                for original, translated in zip(
                    field.options, field.translated_options, strict=False
                )
                if " ".join(translated.casefold().split()) == normalized_value
            ),
            None,
        )
        if translated_exact:
            return translated_exact
        contained = next(
            (
                option
                for option in field.options
                if normalized_value and normalized_value in " ".join(option.casefold().split())
            ),
            None,
        )
        if contained:
            return contained
        if lowered in {"yes", "true", "authorized", "authorised"}:
            direct = next(
                (option for option in field.options if option.casefold() in {"yes", "ja"}), value
            )
            if direct != value:
                return direct
            for original, translated in zip(field.options, field.translated_options, strict=False):
                if "other open position" in translated.casefold():
                    return original
            return value
        if lowered in {"no", "false"}:
            direct = next(
                (option for option in field.options if option.casefold() in {"no", "nein"}), value
            )
            if direct != value:
                return direct
            for original, translated in zip(field.options, field.translated_options, strict=False):
                if "only" in translated.casefold() and "applied" in translated.casefold():
                    return original
            return value
        return value

    @staticmethod
    def _transform_value(field_key: str, value: str, field: DetectedField) -> str:
        return value

    @staticmethod
    def _unknown(
        field: DetectedField,
        field_key: str | None,
        *,
        resolution_message: str | None = None,
        ai_retryable: bool = False,
    ) -> ResolvedField:
        return ResolvedField(
            field,
            field_key,
            None,
            AnswerSource.UNKNOWN,
            0.0,
            resolution_message=resolution_message,
            ai_retryable=ai_retryable,
        )
