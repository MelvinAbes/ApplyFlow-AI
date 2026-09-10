import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.ai.providers import StructuredAIProvider, resolve_ai_provider
from app.automation.field_detector import DetectedField
from app.automation.field_mapper import map_field, normalize_label
from app.config import Settings
from app.logging import log_event
from app.models import AIFieldInterpretationCache, AIInvocation

CANONICAL_KEYS = {
    "address",
    "city",
    "consider_other_positions",
    "country",
    "date_of_birth",
    "email",
    "employer_save_answers",
    "english_level",
    "first_name",
    "gender",
    "german_level",
    "honorific_title",
    "last_name",
    "name_suffix",
    "nationality",
    "phone",
    "phone_country_code",
    "postal_code",
    "street_name",
    "house_number",
    "full_name",
    "preferred_location_primary",
    "preferred_location_secondary",
    "preferred_start_date",
    "resume",
    "salary_expectation",
    "travel_willingness",
    "university",
}

GERMAN_FIELD_RULES: tuple[tuple[tuple[str, ...], str, str | None], ...] = (
    (("private e mail",), "Private email", "email"),
    (("vorname",), "First name", "first_name"),
    (("nachname",), "Last name", "last_name"),
    (("landercode",), "Country calling code", "phone_country_code"),
    (("telefon",), "Phone number", "phone"),
    (("strasse und hausnummer",), "Street and house number", "address"),
    (("postleitzahl",), "Postal code", "postal_code"),
    (("stadt",), "City", "city"),
    (("geschlecht",), "Gender", "gender"),
    (("titel",), "Title", "honorific_title"),
    (("namenszusatz",), "Name suffix", "name_suffix"),
    (("geburtsdatum",), "Date of birth", "date_of_birth"),
    (("nationalitat",), "Nationality", "nationality"),
    (("hochschule",), "University", "university"),
    (
        ("standortwahl prioritat 1",),
        "Preferred location — first choice",
        "preferred_location_primary",
    ),
    (
        ("weiterer standort", "standortwahl prioritat 2"),
        "Preferred location — second choice",
        "preferred_location_secondary",
    ),
    (("deutschkenntnisse",), "German proficiency (written and spoken)", "german_level"),
    (("englischkenntnisse",), "English proficiency (written and spoken)", "english_level"),
    (("reisebereitschaft",), "Willingness to travel", "travel_willingness"),
    (
        ("gewunschter starttermin", "kundigungsfrist"),
        "Preferred start date or notice period",
        "preferred_start_date",
    ),
    (
        ("erwartetes jahresgehalt",),
        "Expected annual salary, including potential bonus",
        "salary_expectation",
    ),
    (
        ("weitere offene stellen",),
        "May this employer consider you for other open positions?",
        "consider_other_positions",
    ),
    (
        ("schwerbehinderung", "schwerbehindertenvertretung"),
        "Would you like support from the disability representative during the application process?",
        None,
    ),
    (
        ("antworten fur zukunftige",),
        "Save my answers for future applications",
        "employer_save_answers",
    ),
    (("lebenslauf",), "Resume", "resume"),
    (
        ("zeugnisse",),
        "Certificates upload (required for apprenticeships and dual-study programs)",
        None,
    ),
    (("weitere angaben",), "Additional information", None),
)

GERMAN_OPTION_TRANSLATIONS = {
    "ja": "Yes",
    "nein": "No",
    "keine angabe": "Prefer not to say",
    "mannlich": "Male",
    "weiblich": "Female",
    "divers": "Diverse",
    "deutschland": "Germany",
    "ja weitere offene stellen": "Consider me for other open positions",
    "nein nur die beworbene stelle": "Consider me only for the position I applied for",
    "ich mochte auf weitere offene stellen gepruft werden": "Consider me for other open positions",
    "ich mochte nur auf die stelle n gepruft werden auf die ich mich beworben habe": "Consider me only for the position(s) I applied for",
}


class AIFieldInterpretation(BaseModel):
    input_index: int = Field(ge=0)
    source_language: str
    english_question: str
    canonical_key: str | None
    english_options: list[str]
    confidence: float = Field(ge=0, le=1)


class AIFieldInterpretationBatch(BaseModel):
    fields: list[AIFieldInterpretation]


@dataclass(slots=True)
class DeterministicInterpretation:
    language: str
    question: str | None
    canonical_key: str | None
    options: list[str]
    needs_ai: bool


class FieldInterpreter:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        provider: StructuredAIProvider | None = None,
    ):
        self.session = session
        self.settings = settings
        self.provider = provider

    async def interpret(self, fields: list[DetectedField]) -> list[DetectedField]:
        deterministic = [self._deterministic(field) for field in fields]
        pending = [index for index, item in enumerate(deterministic) if item.needs_ai]
        ai_results: dict[int, AIFieldInterpretation] = {}
        provider = self.provider
        if pending and provider is None:
            provider = await resolve_ai_provider(self.settings)
        if pending and provider is not None:
            uncached: list[int] = []
            duplicate_of: dict[int, int] = {}
            first_index_by_key: dict[str, int] = {}
            for index in pending:
                cached = self._cached(provider, fields[index])
                if cached is None:
                    cache_key = self._cache_key(provider, fields[index])
                    if cache_key in first_index_by_key:
                        duplicate_of[index] = first_index_by_key[cache_key]
                    else:
                        first_index_by_key[cache_key] = index
                        uncached.append(index)
                else:
                    ai_results[index] = cached
            if uncached:
                generated = await self._generate(provider, fields, uncached)
                ai_results.update(generated)
                for index, original_index in duplicate_of.items():
                    original = generated.get(original_index)
                    if original:
                        ai_results[index] = original.model_copy(update={"input_index": index})

        interpreted: list[DetectedField] = []
        for index, field in enumerate(fields):
            base = deterministic[index]
            ai = ai_results.get(index)
            translated_options = list(base.options)
            if ai and len(ai.english_options) == len(field.options):
                translated_options = [
                    current if current != original else translated
                    for current, original, translated in zip(
                        translated_options,
                        field.options,
                        ai.english_options,
                        strict=True,
                    )
                ]
            canonical_key = base.canonical_key
            if (
                canonical_key is None
                and ai
                and ai.confidence >= 0.9
                and ai.canonical_key in CANONICAL_KEYS
            ):
                canonical_key = ai.canonical_key
            translated_question = base.question or (ai.english_question if ai else None)
            interpreted.append(
                field.model_copy(
                    update={
                        "translated_question": translated_question,
                        "source_language": base.language
                        if base.language != "und"
                        else (ai.source_language if ai else "und"),
                        "canonical_key": canonical_key,
                        "translated_options": translated_options,
                        "translation_confidence": 1.0
                        if base.question
                        else (ai.confidence if ai else 0.0),
                    }
                )
            )
        return interpreted

    def _deterministic(self, field: DetectedField) -> DeterministicInterpretation:
        normalized_question = normalize_label(field.question)
        normalized_context = normalize_label(" ".join((field.question, *field.options)))
        language = "de" if self._looks_german(normalized_context) else "en"
        question: str | None = None
        mapped = map_field(
            field.question,
            html_name=field.html_name,
            html_id=field.html_id,
            autocomplete=field.autocomplete,
            placeholder=field.placeholder,
            section=field.section,
        )
        canonical_key: str | None = mapped[0] if mapped else None
        for markers, english, key in GERMAN_FIELD_RULES:
            # Choice text can mention unrelated concepts (for example a location
            # dropdown containing "Darmstadt University"). It may help identify the
            # language, but it must never determine what question the control asks.
            if all(marker in normalized_question for marker in markers):
                question = english
                canonical_key = key
                language = "de"
                break
        options = [self._translate_option(option) for option in field.options]
        untranslated_german_options = language != "en" and any(
            translated == original
            for original, translated in zip(field.options, options, strict=True)
        )
        needs_ai = question is None and language != "en"
        needs_ai = needs_ai or untranslated_german_options
        needs_ai = needs_ai or (
            canonical_key is None and self._should_classify_semantics(field)
        )
        return DeterministicInterpretation(
            language=language,
            question=question,
            canonical_key=canonical_key,
            options=options,
            needs_ai=needs_ai,
        )

    @staticmethod
    def _looks_german(normalized: str) -> bool:
        padded = f" {normalized} "
        markers = (
            " der ",
            " die ",
            " das ",
            " du ",
            " welche ",
            " welcher ",
            " welchen ",
            " dein ",
            " eine ",
            " fur ",
            " mit ",
            " bevorzug ",
            " weitere ",
            " gewunscht ",
            " bereitschaft ",
            " kenntnisse ",
            " geburt ",
            " gehalt ",
            " strasse ",
            " stadt ",
            " land ",
        )
        return any(marker in padded for marker in markers)

    @staticmethod
    def _translate_option(option: str) -> str:
        normalized = normalize_label(option)
        return GERMAN_OPTION_TRANSLATIONS.get(normalized, option)

    @staticmethod
    def _should_classify_semantics(field: DetectedField) -> bool:
        normalized = normalize_label(field.question)
        if not normalized or len(normalized) > 140:
            return False
        if field.field_type == "textarea" or any(
            marker in normalized
            for marker in (
                "why",
                "describe",
                "tell us",
                "motivation",
                "cover letter",
                "additional information",
                "weitere angaben",
            )
        ):
            return False
        return field.field_type in {
            "text",
            "email",
            "tel",
            "url",
            "number",
            "date",
            "select",
            "combobox",
            "radio",
            "checkbox",
            "file",
        }

    def _cache_key(self, provider: StructuredAIProvider, field: DetectedField) -> str:
        payload = json.dumps(
            {
                "question": field.question,
                "options": field.options,
                "html_name": field.html_name,
                "html_id": field.html_id,
                "autocomplete": field.autocomplete,
                "placeholder": field.placeholder,
                "section": field.section,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(f"{provider.cache_identity}:{payload}".encode()).hexdigest()

    def _cached(
        self, provider: StructuredAIProvider, field: DetectedField
    ) -> AIFieldInterpretation | None:
        cache_key = self._cache_key(provider, field)
        record = self.session.exec(
            select(AIFieldInterpretationCache).where(
                AIFieldInterpretationCache.cache_key == cache_key
            )
        ).first()
        return AIFieldInterpretation.model_validate(record.result) if record else None

    async def _generate(
        self,
        provider: StructuredAIProvider,
        fields: list[DetectedField],
        indexes: list[int],
    ) -> dict[int, AIFieldInterpretation]:
        prompt = json.dumps(
            {
                "allowed_canonical_keys": sorted(CANONICAL_KEYS),
                "fields": [
                    {
                        "input_index": index,
                        "question": fields[index].question,
                        "options": fields[index].options,
                        "html_name": fields[index].html_name,
                        "html_id": fields[index].html_id,
                        "autocomplete": fields[index].autocomplete,
                        "placeholder": fields[index].placeholder,
                        "section": fields[index].section,
                    }
                    for index in indexes
                ],
            },
            ensure_ascii=False,
        )
        try:
            generated = await provider.generate(
                instructions=(
                    "Translate job-application field labels and choice labels faithfully into English. "
                    "Classify field meaning only from the label, safe HTML metadata, section, and choices. "
                    "Do not answer any question and do not infer candidate facts. Preserve each input_index. "
                    "Return exactly one result per input. english_options must have the same number and order "
                    "as the supplied options. canonical_key must be one of allowed_canonical_keys or null."
                ),
                prompt=prompt,
                output_model=AIFieldInterpretationBatch,
            )
        except Exception as error:
            log_event("FIELD_TRANSLATION_FALLBACK", error_type=type(error).__name__)
            return {}

        valid: dict[int, AIFieldInterpretation] = {}
        for item in generated.value.fields:
            if item.input_index not in indexes or item.input_index in valid:
                continue
            source = fields[item.input_index]
            if len(item.english_options) != len(source.options):
                continue
            if item.canonical_key not in CANONICAL_KEYS:
                item.canonical_key = None
            valid[item.input_index] = item
            self.session.add(
                AIFieldInterpretationCache(
                    cache_key=self._cache_key(provider, source),
                    result=item.model_dump(mode="json"),
                )
            )
        if valid:
            self.session.add(
                AIInvocation(
                    purpose="field_translation",
                    cache_key=None,
                    model=f"{generated.provider}:{generated.model}",
                    input_characters=len(prompt),
                    output_characters=generated.output_characters,
                )
            )
            try:
                self.session.commit()
            except IntegrityError:
                self.session.rollback()
        return valid
