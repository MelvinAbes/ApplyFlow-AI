import hashlib
import json
import re
from dataclasses import dataclass

from sqlmodel import Session, select

from app.ai.context import TOKEN_RE, redact_contact_data, relevant_excerpt
from app.ai.providers import StructuredAIProvider, resolve_ai_provider
from app.config import Settings
from app.logging import log_event
from app.models import AIAnswerCache, AIInvocation, Job, Resume
from app.schemas.application import AIAnswer
from app.services.profile_service import ProfileService

OPEN_ENDED_PATTERNS = (
    "why are you interested",
    "why do you want",
    "why should we",
    "describe your relevant experience",
    "good candidate",
    "motivation",
    "cover letter",
)

FACTUAL_PATTERNS = (
    "authorized",
    "authorised",
    "visa",
    "salary",
    "start date",
    "available",
    "hours per week",
    "language level",
    "proficiency",
    "degree",
    "graduate",
    "certification",
    "years of experience",
    "driver",
    "relocate",
    "notice period",
)

GROUNDING_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "by",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "with",
    "your",
}


@dataclass(frozen=True, slots=True)
class AIAnswerAttempt:
    answer: AIAnswer
    resolution_message: str | None = None
    retryable: bool = False


def is_open_ended_question(question: str) -> bool:
    lowered = question.casefold()
    return any(pattern in lowered for pattern in OPEN_ENDED_PATTERNS)


def is_factual_question(question: str) -> bool:
    lowered = question.casefold()
    return any(pattern in lowered for pattern in FACTUAL_PATTERNS)


class QuestionAnswerer:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        provider: StructuredAIProvider | None = None,
    ):
        self.session = session
        self.settings = settings
        self.provider = provider

    async def answer(
        self,
        question: str,
        job: Job,
        resume: Resume | None,
        character_limit: int | None = None,
    ) -> AIAnswer:
        return (
            await self.attempt(
                question,
                job,
                resume,
                character_limit,
            )
        ).answer

    async def attempt(
        self,
        question: str,
        job: Job,
        resume: Resume | None,
        character_limit: int | None = None,
    ) -> AIAnswerAttempt:
        if is_factual_question(question) or not is_open_ended_question(question):
            return self._needs_user_input()
        provider = self.provider or await resolve_ai_provider(self.settings)
        if provider is None:
            return self._needs_user_input(
                "AI is not available. Connect ChatGPT/Codex in Settings, then try AI again.",
                retryable=True,
            )
        profile_service = ProfileService(self.session)
        context = profile_service.professional_context()
        resume_text = (
            relevant_excerpt(
                redact_contact_data(resume.extracted_text, profile_service.contact_values()),
                f"{question}\n{job.title}\n{job.description}",
                max_characters=5_000,
            )
            if resume
            else ""
        )
        role_context = {
            "company": job.company,
            "title": job.title,
            "description": relevant_excerpt(job.description, question, max_characters=6_000),
        }
        evidence = build_grounding_evidence(context, resume_text, role_context)
        prompt = json.dumps(
            {
                "question": question,
                "maximum_characters": character_limit or 900,
                "evidence": [
                    {"id": evidence_id, "text": text} for evidence_id, text in evidence.items()
                ],
            },
            ensure_ascii=False,
        )
        cache_key = hashlib.sha256(f"{provider.cache_identity}:{prompt}".encode()).hexdigest()
        cached = self.session.exec(
            select(AIAnswerCache).where(AIAnswerCache.cache_key == cache_key)
        ).first()
        if cached:
            return AIAnswerAttempt(answer=AIAnswer.model_validate(cached.result))
        try:
            generated = await provider.generate(
                instructions=(
                    "Write a concise natural job-application answer using only the supplied evidence. "
                    "Never invent experience, education, skills, status, availability, or enthusiasm. "
                    "If the evidence is insufficient, return NEEDS_USER_INPUT with no answer. In "
                    "grounded_facts, return only the IDs of the evidence entries used; never return quotes "
                    "or invent IDs. Every sentence in the answer must be directly supported by at least "
                    "one cited evidence entry. Respect the character limit."
                ),
                prompt=prompt,
                output_model=AIAnswer,
            )
        except Exception as error:
            log_event(
                "AI_ANSWER_FALLBACK",
                entity_id=job.id,
                error_type=type(error).__name__,
            )
            return self._needs_user_input(
                "AI could not generate a valid answer. Try again; if the problem continues, "
                "check the Codex connection in Settings.",
                retryable=True,
            )
        parsed = generated.value
        if parsed.status != "ANSWERED" or not parsed.answer:
            return self._needs_user_input(
                "AI could not produce a grounded answer from the current profile and résumé. "
                "Add verified facts or enter the answer yourself.",
                retryable=True,
            )
        if character_limit and len(parsed.answer) > character_limit:
            return self._needs_user_input(
                "The generated answer exceeded the employer's character limit. Try AI again or enter a shorter answer.",
                retryable=True,
            )
        if not answer_is_grounded(parsed, evidence):
            return self._needs_user_input(
                "The generated answer was rejected because it was not fully supported by verified source evidence.",
                retryable=True,
            )
        self.session.add(
            AIInvocation(
                purpose="application_question",
                cache_key=cache_key,
                model=f"{generated.provider}:{generated.model}",
                input_characters=len(prompt),
                output_characters=generated.output_characters,
            )
        )
        self.session.add(AIAnswerCache(cache_key=cache_key, result=parsed.model_dump(mode="json")))
        self.session.commit()
        return AIAnswerAttempt(answer=parsed)

    @staticmethod
    def _needs_user_input(
        resolution_message: str | None = None, *, retryable: bool = False
    ) -> AIAnswerAttempt:
        return AIAnswerAttempt(
            answer=AIAnswer(status="NEEDS_USER_INPUT", confidence=1.0),
            resolution_message=resolution_message,
            retryable=retryable,
        )


def build_grounding_evidence(
    candidate_context: dict, resume_text: str, role_context: dict[str, str]
) -> dict[str, str]:
    evidence: dict[str, str] = {}

    def add(prefix: str, text: str) -> None:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) < 3:
            return
        number = 1 + sum(key.startswith(prefix) for key in evidence)
        evidence[f"{prefix}{number:03d}"] = normalized

    for key, value in candidate_context.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            for item in value:
                rendered = _render_evidence_value(item)
                if rendered:
                    add("P", f"{key}: {rendered}")
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                if child_value in (None, "", [], {}):
                    continue
                rendered = _render_evidence_value(child_value)
                if rendered:
                    add("P", f"{key}.{child_key}: {rendered}")
        else:
            add("P", f"{key}: {value}")

    for chunk in _evidence_chunks(resume_text):
        add("R", chunk)
    for key in ("company", "title"):
        if value := role_context.get(key):
            add("J", f"{key}: {value}")
    for chunk in _evidence_chunks(role_context.get("description", "")):
        add("J", chunk)
    return evidence


def _render_evidence_value(value: object) -> str:
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {_render_evidence_value(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {})
        )
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _evidence_chunks(text: str, *, max_characters: int = 600) -> list[str]:
    chunks: list[str] = []
    blocks = [block.strip() for block in re.split(r"\n+|(?<=[.!?])\s+", text) if block.strip()]
    for block in blocks:
        if len(block) <= max_characters:
            chunks.append(block)
            continue
        chunks.extend(
            block[start : start + max_characters] for start in range(0, len(block), max_characters)
        )
    return chunks


def answer_is_grounded(answer: AIAnswer, evidence: dict[str, str]) -> bool:
    if not answer.answer or not answer.grounded_facts:
        return False
    evidence_by_id = {key.casefold(): value for key, value in evidence.items()}
    evidence_ids = [item.strip().casefold() for item in answer.grounded_facts]
    if any(evidence_id not in evidence_by_id for evidence_id in evidence_ids):
        return False
    supporting_text = " ".join(evidence_by_id[evidence_id] for evidence_id in evidence_ids)
    context_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", supporting_text))
    answer_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", answer.answer))
    if not answer_numbers <= context_numbers:
        return False

    supporting_tokens = {
        token.casefold()
        for token in TOKEN_RE.findall(supporting_text)
        if token.casefold() not in GROUNDING_STOPWORDS
    }
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer.answer)
        if sentence.strip()
    ]
    for sentence in sentences:
        tokens = {
            token.casefold()
            for token in TOKEN_RE.findall(sentence)
            if token.casefold() not in GROUNDING_STOPWORDS
        }
        if not tokens:
            continue
        coverage = len(tokens & supporting_tokens) / len(tokens)
        if coverage < 0.25:
            return False
    return True
