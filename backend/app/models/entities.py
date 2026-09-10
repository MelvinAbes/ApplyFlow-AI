from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, Index, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.enums import AnswerSource, ApplicationStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class CandidateProfile(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    first_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    country: str | None = None
    postal_code: str | None = None
    street_name: str | None = None
    house_number: str | None = None
    address: str | None = None
    nationality: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    honorific_title: str | None = None
    name_suffix: str | None = None
    current_title: str | None = None
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    programming_languages: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    frameworks: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    tools: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    personal_website: str | None = None
    preferred_job_types: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    preferred_locations: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    remote_preference: str | None = None
    minimum_match_score: int = Field(default=60, ge=0, le=100)
    preferred_technologies: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    undesired_roles: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    seniority: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    availability: str | None = None
    preferred_start_date: date | None = None
    work_authorization: str | None = None
    visa_status: str | None = None
    notice_period: str | None = None
    salary_expectation: str | None = None
    weekly_hours: str | None = None
    relocation_willingness: str | None = None
    travel_willingness: str | None = None
    consider_other_positions: str | None = None
    drivers_license: str | None = None
    revision: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Education(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    candidate_id: str = Field(foreign_key="candidateprofile.id", index=True)
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    start_date: date | None = None
    graduation_date: date | None = None
    current_student: bool = False


class WorkExperience(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    candidate_id: str = Field(foreign_key="candidateprofile.id", index=True)
    title: str
    company: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = Field(default=None, sa_column=Column(Text))


class Language(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    candidate_id: str = Field(foreign_key="candidateprofile.id", index=True)
    language: str
    proficiency: str | None = None


class Resume(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    filename: str
    path: str
    label: str
    extracted_text: str = Field(default="", sa_column=Column(Text))
    target_roles: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    is_default: bool = False
    content_sha256: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class Job(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    company: str
    title: str
    location: str | None = None
    description: str = Field(default="", sa_column=Column(Text))
    employment_type: str | None = None
    source_url: str | None = Field(default=None, index=True)
    application_url: str | None = None
    source: str = "manual"
    external_job_id: str | None = None
    description_sha256: str
    probable_duplicate_of: str | None = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class JobAnalysisRecord(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("job_id", "cache_key", name="uq_job_analysis_cache"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    job_id: str = Field(foreign_key="job.id", index=True)
    resume_id: str | None = Field(default=None, foreign_key="resume.id")
    cache_key: str = Field(index=True)
    result: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    ai_used: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class AnswerBankEntry(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    canonical_key: str = Field(index=True)
    question: str
    normalized_question: str = Field(index=True)
    answer: str = Field(sa_column=Column(Text))
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FieldMappingEntry(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("normalized_label", name="uq_field_mapping_normalized_label"),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    normalized_label: str = Field(index=True)
    source_label: str
    canonical_key: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Application(SQLModel, table=True):
    __table_args__ = (
        Index(
            "uq_active_application_job",
            "job_id",
            unique=True,
            sqlite_where=text("archived_at IS NULL"),
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    job_id: str = Field(foreign_key="job.id", index=True)
    resume_id: str | None = Field(default=None, foreign_key="resume.id")
    status: ApplicationStatus = Field(default=ApplicationStatus.DISCOVERED, index=True)
    current_step: int = Field(default=1, ge=1)
    current_url: str | None = None
    ats_adapter: str | None = None
    form_fingerprint: str | None = None
    form_action: str | None = None
    waiting_reason: str | None = None
    error_message: str | None = None
    approval_token_hash: str | None = None
    approved_payload_hash: str | None = None
    approval_expires_at: datetime | None = None
    approved_at: datetime | None = None
    approval_consumed_at: datetime | None = None
    submitted_at: datetime | None = None
    archived_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class ApplicationField(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    application_id: str = Field(foreign_key="application.id", index=True)
    step_index: int = Field(default=1, ge=1)
    active: bool = Field(default=True, index=True)
    field_key: str | None = None
    selector: str
    question: str
    translated_question: str | None = None
    source_language: str | None = None
    field_type: str
    answer: str | None = Field(default=None, sa_column=Column(Text))
    source: AnswerSource = Field(default=AnswerSource.UNKNOWN)
    confidence: float = Field(default=0, ge=0, le=1)
    required: bool = False
    uncertain: bool = False
    requires_verification: bool = False
    disqualifying: bool = False
    character_limit: int | None = None
    options: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    translated_options: list[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    translation_confidence: float = Field(default=0, ge=0, le=1)
    skipped: bool = False
    resolution_message: str | None = Field(default=None, sa_column=Column(Text))
    ai_retryable: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ApplicationEvent(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    application_id: str = Field(foreign_key="application.id", index=True)
    event_type: str = Field(index=True)
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AIInvocation(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    purpose: str = Field(index=True)
    cache_key: str | None = Field(default=None, index=True)
    model: str
    input_characters: int = 0
    output_characters: int = 0
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AIAnswerCache(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("cache_key", name="uq_ai_answer_cache_key"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    cache_key: str = Field(index=True)
    result: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class AIFieldInterpretationCache(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("cache_key", name="uq_ai_field_interpretation_cache_key"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    cache_key: str = Field(index=True)
    result: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)
