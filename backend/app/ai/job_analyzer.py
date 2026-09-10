import hashlib
import json
import re

from sqlmodel import Session, select

from app.ai.context import redact_contact_data, relevant_excerpt
from app.ai.providers import StructuredAIProvider, resolve_ai_provider
from app.config import Settings
from app.logging import log_event
from app.models import AIInvocation, CandidateProfile, Job, JobAnalysisRecord, Resume
from app.schemas.job import JobAnalysis
from app.services.profile_service import ProfileService

SKILL_VOCABULARY = {
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "fastapi",
    "django",
    "flask",
    "sql",
    "postgresql",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "pandas",
    "numpy",
    "spark",
    "airflow",
    "dbt",
    "llm",
    "rag",
    "nlp",
    "machine learning",
    "data engineering",
    "git",
    "linux",
    "c++",
    "go",
}


def extract_requirement_lines(description: str) -> tuple[list[str], list[str]]:
    lines = [line.strip(" •\t-") for line in re.split(r"[\n\r]+|(?<=[.!?])\s+", description)]
    required: list[str] = []
    preferred: list[str] = []
    for line in lines:
        lowered = line.casefold()
        if len(line) < 8:
            continue
        if any(word in lowered for word in ("required", "must", "mandatory", "minimum")):
            required.append(line)
        elif any(word in lowered for word in ("preferred", "nice to have", "plus", "bonus")):
            preferred.append(line)
    return required[:20], preferred[:20]


def mentioned_skills(text: str) -> set[str]:
    lowered = text.casefold()
    return {skill for skill in SKILL_VOCABULARY if re.search(rf"\b{re.escape(skill)}\b", lowered)}


class JobAnalyzer:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        provider: StructuredAIProvider | None = None,
    ):
        self.session = session
        self.settings = settings
        self.provider = provider

    async def analyze(
        self, job: Job, profile: CandidateProfile, resume: Resume | None, force: bool = False
    ) -> tuple[JobAnalysisRecord, JobAnalysis]:
        provider = self.provider or await resolve_ai_provider(self.settings)
        provider_identity = provider.cache_identity if provider else "deterministic"
        cache_key = self._cache_key(job, profile, resume, provider_identity)
        if not force:
            cached = self.session.exec(
                select(JobAnalysisRecord).where(
                    JobAnalysisRecord.job_id == job.id,
                    JobAnalysisRecord.cache_key == cache_key,
                )
            ).first()
            if cached:
                return cached, JobAnalysis.model_validate(cached.result)

        ai_used = False
        if provider:
            try:
                result = await self._analyze_with_ai(job, resume, provider)
                ai_used = True
            except Exception as error:
                log_event(
                    "AI_ANALYSIS_FALLBACK",
                    entity_id=job.id,
                    error_type=type(error).__name__,
                )
                result = self._analyze_locally(job, profile, resume)
                cache_key = self._cache_key(job, profile, resume, "deterministic")
        else:
            result = self._analyze_locally(job, profile, resume)
        if provider and not ai_used:
            cached_fallback = self.session.exec(
                select(JobAnalysisRecord).where(
                    JobAnalysisRecord.job_id == job.id,
                    JobAnalysisRecord.cache_key == cache_key,
                )
            ).first()
            if cached_fallback:
                return cached_fallback, JobAnalysis.model_validate(cached_fallback.result)
        result.recommended_resume_id = resume.id if resume else None

        record = JobAnalysisRecord(
            job_id=job.id,
            resume_id=resume.id if resume else None,
            cache_key=cache_key,
            result=result.model_dump(mode="json"),
            ai_used=ai_used,
        )
        self.session.add(record)
        self.session.commit()
        self.session.refresh(record)
        return record, result

    async def _analyze_with_ai(
        self, job: Job, resume: Resume | None, provider: StructuredAIProvider
    ) -> JobAnalysis:
        profile_service = ProfileService(self.session)
        professional = profile_service.professional_context()
        query = f"{job.title}\n{job.description}"
        compact_resume = (
            relevant_excerpt(
                redact_contact_data(resume.extracted_text, profile_service.contact_values()),
                query,
                max_characters=6_000,
            )
            if resume
            else ""
        )
        prompt = json.dumps(
            {
                "candidate_professional_profile": professional,
                "resume_text": compact_resume,
                "job": {
                    "title": job.title,
                    "company": job.company,
                    "location": job.location,
                    "description": relevant_excerpt(
                        job.description,
                        "requirements qualifications responsibilities",
                        max_characters=12_000,
                    ),
                },
                "resume_id": resume.id if resume else None,
            },
            ensure_ascii=False,
        )
        generated = await provider.generate(
            instructions=(
                "Evaluate job fit conservatively. Distinguish required, preferred, and "
                "nice-to-have language. Use only supplied facts; never infer credentials. "
                "Scores are 0-100 and the recommendation must be consistent with the score."
            ),
            prompt=prompt,
            output_model=JobAnalysis,
        )
        self.session.add(
            AIInvocation(
                purpose="job_analysis",
                cache_key=hashlib.sha256(prompt.encode()).hexdigest(),
                model=f"{generated.provider}:{generated.model}",
                input_characters=len(prompt),
                output_characters=generated.output_characters,
            )
        )
        return generated.value

    def _analyze_locally(
        self, job: Job, profile: CandidateProfile, resume: Resume | None
    ) -> JobAnalysis:
        candidate_text = " ".join(
            [
                profile.current_title or "",
                *profile.skills,
                *profile.programming_languages,
                *profile.frameworks,
                *profile.tools,
                resume.extracted_text if resume else "",
            ]
        )
        job_skills = mentioned_skills(job.description + " " + job.title)
        candidate_skills = mentioned_skills(candidate_text)
        matching = sorted(job_skills & candidate_skills)
        missing = sorted(job_skills - candidate_skills)
        skill_score = round(100 * len(matching) / max(1, len(job_skills)))
        title_tokens = set(re.findall(r"[a-z]{3,}", job.title.casefold()))
        candidate_tokens = set(re.findall(r"[a-z]{3,}", candidate_text.casefold()))
        experience_score = round(
            100 * len(title_tokens & candidate_tokens) / max(1, len(title_tokens))
        )
        context = ProfileService(self.session).professional_context()
        has_education = bool(context.get("education"))
        education_score = 80 if has_education else 40
        preferred_locations = {x.casefold() for x in profile.preferred_locations}
        location_score = 70
        if job.location and preferred_locations:
            location_score = (
                100 if any(x in job.location.casefold() for x in preferred_locations) else 45
            )
        required, preferred = extract_requirement_lines(job.description)
        unmet = [line for line in required if mentioned_skills(line) - candidate_skills]
        overall = round(
            skill_score * 0.45
            + experience_score * 0.25
            + education_score * 0.15
            + location_score * 0.15
        )
        overall = max(0, min(100, overall - min(20, 7 * len(unmet))))
        recommendation = (
            "strong_apply"
            if overall >= 80
            else "apply"
            if overall >= 65
            else "maybe"
            if overall >= 35
            else "skip"
        )
        strengths = [f"Matches {skill}" for skill in matching[:8]]
        if not strengths:
            strengths.append("Profile and role require manual comparison")
        concerns = [f"Missing or unconfirmed: {skill}" for skill in missing[:8]]
        concerns.extend(f"Unmet required statement: {line}" for line in unmet[:4])
        return JobAnalysis(
            overall_score=overall,
            skill_match_score=skill_score,
            experience_match_score=experience_score,
            education_match_score=education_score,
            location_match_score=location_score,
            recommendation=recommendation,
            matching_skills=matching,
            missing_skills=missing,
            must_have_requirements=required,
            unmet_must_have_requirements=unmet,
            strengths=strengths,
            concerns=concerns,
            recommended_resume_id=resume.id if resume else None,
            summary=(
                "Deterministic match based on explicit skills, title, education presence, and location. "
                f"{len(required)} required and {len(preferred)} preferred statements were identified."
            ),
        )

    @staticmethod
    def _cache_key(
        job: Job,
        profile: CandidateProfile,
        resume: Resume | None,
        provider_identity: str,
    ) -> str:
        raw = (
            f"{job.description_sha256}:{profile.revision}:"
            f"{resume.content_sha256 if resume else '-'}:{provider_identity}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def select_resume(job: Job, resumes: list[Resume]) -> Resume | None:
        if not resumes:
            return None
        job_terms = set(
            re.findall(r"[a-z0-9+#.-]{2,}", f"{job.title} {job.description}".casefold())
        )

        def relevance(resume: Resume) -> tuple[int, int]:
            resume_terms = set(
                re.findall(
                    r"[a-z0-9+#.-]{2,}",
                    " ".join(
                        [*resume.target_roles, *resume.skills, resume.extracted_text[:12000]]
                    ).casefold(),
                )
            )
            return len(job_terms & resume_terms), int(resume.is_default)

        return max(resumes, key=relevance)
