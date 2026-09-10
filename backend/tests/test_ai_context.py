import pytest
from sqlmodel import select

from app.ai.context import redact_contact_data, relevant_excerpt
from app.ai.providers import StructuredAIResult
from app.ai.question_answerer import (
    AIAnswerAttempt,
    QuestionAnswerer,
    answer_is_grounded,
    build_grounding_evidence,
)
from app.models import (
    AIAnswerCache,
    AIInvocation,
    Application,
    ApplicationField,
    CandidateProfile,
    Job,
    Resume,
)
from app.models.enums import AnswerSource, ApplicationStatus
from app.schemas.application import AIAnswer
from app.services.application_service import ApplicationService
from app.services.job_service import description_hash


def test_contact_data_is_removed_before_ai_use() -> None:
    text = "Ada Lovelace\nada@example.com\n+49 30 1234567\nPython and FastAPI"
    redacted = redact_contact_data(text, ["Ada Lovelace"])
    assert "ada@example.com" not in redacted
    assert "+49 30 1234567" not in redacted
    assert "Ada Lovelace" not in redacted
    assert "Python and FastAPI" in redacted


def test_relevant_excerpt_prefers_query_matching_blocks() -> None:
    text = "Unrelated administration work.\n\nBuilt Python and FastAPI services.\n\nAnother unrelated paragraph."
    excerpt = relevant_excerpt(text, "Python engineer", max_characters=45)
    assert "Python and FastAPI" in excerpt


def test_grounding_rejects_new_numeric_claims_and_unsupported_sentences() -> None:
    evidence = {"R001": "built python services with fastapi"}
    assert answer_is_grounded(
        AIAnswer(
            status="ANSWERED",
            answer="Built Python services with FastAPI.",
            grounded_facts=["R001"],
            confidence=0.9,
        ),
        evidence,
    )
    assert not answer_is_grounded(
        AIAnswer(
            status="ANSWERED",
            answer="I have 10 years of Python experience.",
            grounded_facts=["R001"],
            confidence=0.9,
        ),
        evidence,
    )
    assert not answer_is_grounded(
        AIAnswer(
            status="ANSWERED",
            answer="Built Python services. I am the ideal candidate.",
            grounded_facts=["R001"],
            confidence=0.9,
        ),
        evidence,
    )
    assert not answer_is_grounded(
        AIAnswer(
            status="ANSWERED",
            answer="Built Python services.",
            grounded_facts=["UNKNOWN"],
            confidence=0.9,
        ),
        evidence,
    )


def test_grounding_evidence_uses_stable_ids_for_profile_resume_and_role() -> None:
    evidence = build_grounding_evidence(
        {"skills": ["Python"], "preferences": {"remote": "hybrid"}},
        "Built FastAPI services.\nImproved deployment automation.",
        {"company": "Acme", "title": "Engineer", "description": "Build APIs."},
    )

    assert evidence["P001"] == "skills: Python"
    assert evidence["P002"] == "preferences.remote: hybrid"
    assert evidence["R001"] == "Built FastAPI services."
    assert evidence["R002"] == "Improved deployment automation."
    assert evidence["J001"] == "company: Acme"
    assert evidence["J002"] == "title: Engineer"
    assert evidence["J003"] == "Build APIs."


@pytest.mark.asyncio
async def test_grounded_ai_answer_is_reused_from_local_cache(session, test_settings) -> None:
    profile = CandidateProfile(skills=["Python"])
    job = Job(
        company="Acme",
        title="Python Engineer",
        description="Build Python services",
        description_sha256=description_hash("Build Python services"),
    )
    resume = Resume(
        filename="resume.pdf",
        path="/tmp/resume.pdf",
        label="General",
        extracted_text="Python experience",
        content_sha256="resume-hash",
    )
    session.add(profile)
    session.add(job)
    session.add(resume)
    session.commit()

    class FakeProvider:
        name = "fake"
        model = "test-model"
        cache_identity = "fake:test-model"
        calls = 0

        async def generate(self, **kwargs):
            self.calls += 1
            return StructuredAIResult(
                value=AIAnswer(
                    status="ANSWERED",
                    answer="Python experience.",
                    grounded_facts=["R001"],
                    confidence=0.9,
                ),
                provider=self.name,
                model=self.model,
                output_characters=7,
            )

    provider = FakeProvider()
    answerer = QuestionAnswerer(session, test_settings, provider=provider)
    first = await answerer.answer("Why are you interested in this role?", job, resume)
    second = await answerer.answer("Why are you interested in this role?", job, resume)

    assert first == second
    assert provider.calls == 1
    assert len(session.exec(select(AIAnswerCache)).all()) == 1
    assert len(session.exec(select(AIInvocation)).all()) == 1


@pytest.mark.asyncio
async def test_ai_failure_is_visible_and_retryable(session, test_settings) -> None:
    job = Job(
        company="Acme",
        title="Python Engineer",
        description="Build Python services",
        description_sha256=description_hash("Build Python services"),
    )
    session.add(job)
    session.commit()

    class FailingProvider:
        name = "fake"
        model = "test-model"
        cache_identity = "fake:test-model"

        async def generate(self, **kwargs):
            raise RuntimeError("provider failed")

    attempt = await QuestionAnswerer(
        session,
        test_settings,
        provider=FailingProvider(),
    ).attempt("Why are you interested in this role?", job, None)

    assert attempt.answer.status == "NEEDS_USER_INPUT"
    assert attempt.retryable is True
    assert attempt.resolution_message
    assert "provider failed" not in attempt.resolution_message


@pytest.mark.asyncio
async def test_retry_ai_answer_updates_unresolved_field(
    session, test_settings, monkeypatch
) -> None:
    job = Job(
        company="Acme",
        title="Python Engineer",
        description="Build Python services",
        description_sha256=description_hash("Build Python services"),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        form_action="final",
    )
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#motivation",
        question="Why are you interested in this role?",
        field_type="textarea",
        required=True,
        ai_retryable=True,
    )
    session.add(field)
    session.commit()

    async def successful_attempt(self, question, job, resume, character_limit=None):
        return AIAnswerAttempt(
            answer=AIAnswer(
                status="ANSWERED",
                answer="Python experience.",
                grounded_facts=["Python experience"],
                confidence=0.9,
            )
        )

    monkeypatch.setattr(QuestionAnswerer, "attempt", successful_attempt)

    class NoPageBrowser:
        def page(self, key):
            return None

    result = await ApplicationService(
        session,
        test_settings,
        NoPageBrowser(),  # type: ignore[arg-type]
    ).retry_ai_answer(field)

    session.refresh(field)
    assert result.status == ApplicationStatus.READY_FOR_REVIEW
    assert field.answer == "Python experience."
    assert field.source == AnswerSource.LLM_GENERATED
    assert field.resolution_message is None
