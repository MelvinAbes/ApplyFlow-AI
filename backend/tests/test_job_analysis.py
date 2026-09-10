import pytest
from pydantic import ValidationError

from app.ai.job_analyzer import JobAnalyzer
from app.models import CandidateProfile, Job, Resume
from app.schemas.job import JobAnalysis
from app.services.job_service import description_hash


def valid_analysis() -> dict:
    return {
        "overall_score": 88,
        "skill_match_score": 90,
        "experience_match_score": 80,
        "education_match_score": 95,
        "location_match_score": 90,
        "recommendation": "strong_apply",
        "matching_skills": ["Python"],
        "missing_skills": [],
        "must_have_requirements": ["Python required"],
        "unmet_must_have_requirements": [],
        "strengths": ["Python"],
        "concerns": [],
        "recommended_resume_id": None,
        "summary": "Strong explicit match.",
    }


def test_invalid_scores_cannot_enter_schema() -> None:
    payload = valid_analysis()
    payload["overall_score"] = 101
    with pytest.raises(ValidationError):
        JobAnalysis.model_validate(payload)


def test_inconsistent_recommendation_is_rejected() -> None:
    payload = valid_analysis()
    payload["overall_score"] = 40
    with pytest.raises(ValidationError, match="inconsistent"):
        JobAnalysis.model_validate(payload)


@pytest.mark.asyncio
async def test_local_analysis_distinguishes_required_and_preferred(session, test_settings) -> None:
    profile = CandidateProfile(
        skills=["Python", "FastAPI"],
        programming_languages=["Python"],
        preferred_locations=["Berlin"],
        current_title="Working Student Software Engineer",
    )
    description = "Python is required. SQL is preferred. German is a plus."
    job = Job(
        company="Acme",
        title="Working Student Python",
        location="Berlin",
        description=description,
        description_sha256=description_hash(description),
    )
    session.add(profile)
    session.add(job)
    session.commit()
    record, result = await JobAnalyzer(session, test_settings).analyze(job, profile, None)
    assert record.ai_used is False
    assert any("Python is required" in item for item in result.must_have_requirements)
    assert "python" in result.matching_skills
    assert "sql" in result.missing_skills


@pytest.mark.asyncio
async def test_failed_ai_provider_reuses_existing_deterministic_analysis(
    session, test_settings
) -> None:
    profile = CandidateProfile(skills=["Python"])
    description = "Python is required."
    job = Job(
        company="Acme",
        title="Python Engineer",
        description=description,
        description_sha256=description_hash(description),
    )
    session.add(profile)
    session.add(job)
    session.commit()
    local_record, _ = await JobAnalyzer(session, test_settings).analyze(job, profile, None)

    class FailingProvider:
        name = "fake"
        model = "test"
        cache_identity = "fake:test"

        async def generate(self, **kwargs):
            raise RuntimeError("model unavailable")

    fallback_record, fallback = await JobAnalyzer(
        session,
        test_settings,
        provider=FailingProvider(),
    ).analyze(job, profile, None)

    assert fallback_record.id == local_record.id
    assert fallback_record.ai_used is False
    assert fallback.overall_score >= 0


def test_most_relevant_resume_is_recommended() -> None:
    job = Job(
        company="Acme",
        title="Machine Learning Working Student",
        description="Python PyTorch machine learning",
        description_sha256="job",
    )
    general = Resume(
        filename="general.pdf",
        path="/tmp/general.pdf",
        label="General",
        extracted_text="Customer support communication",
        is_default=True,
        content_sha256="general",
    )
    ml = Resume(
        filename="ml.pdf",
        path="/tmp/ml.pdf",
        label="ML",
        extracted_text="Python PyTorch machine learning projects",
        target_roles=["Machine Learning"],
        skills=["Python", "PyTorch"],
        content_sha256="ml",
    )
    assert JobAnalyzer.select_resume(job, [general, ml]) is ml
