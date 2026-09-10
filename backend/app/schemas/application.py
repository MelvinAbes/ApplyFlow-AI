from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AnswerSource, ApplicationStatus


class ApplicationStart(BaseModel):
    resume_id: str | None = None
    target_url: str | None = None


class ApplicationFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    step_index: int
    active: bool
    field_key: str | None
    selector: str
    question: str
    translated_question: str | None
    source_language: str | None
    field_type: str
    answer: str | None
    source: AnswerSource
    confidence: float
    required: bool
    uncertain: bool
    requires_verification: bool
    disqualifying: bool
    character_limit: int | None
    options: list[str]
    translated_options: list[str]
    translation_confidence: float
    skipped: bool
    resolution_message: str | None
    ai_retryable: bool


class ApplicationFieldUpdate(BaseModel):
    answer: str | None = None
    skip: bool = False
    remember: bool = False
    semantic_key: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def answer_or_skip(self) -> "ApplicationFieldUpdate":
        if self.skip:
            if self.answer not in (None, ""):
                raise ValueError("Skipped fields cannot also have an answer")
            return self
        if not self.answer or not self.answer.strip():
            raise ValueError("Enter an answer or explicitly leave an optional field blank")
        return self


class ApplicationRead(BaseModel):
    id: str
    job_id: str
    company: str
    role: str
    location: str | None
    source_url: str | None
    match_score: int | None
    resume_id: str | None
    resume_label: str | None
    resume_filename: str | None
    status: ApplicationStatus
    current_step: int
    current_url: str | None
    waiting_reason: str | None
    error_message: str | None
    created_at: datetime
    approved_at: datetime | None
    submitted_at: datetime | None
    archived_at: datetime | None
    is_demo: bool
    can_delete: bool
    delete_blocked_reason: str | None
    can_restart: bool
    restart_blocked_reason: str | None
    fields: list[ApplicationFieldRead] = Field(default_factory=list)


class ApplicationStatusUpdate(BaseModel):
    status: ApplicationStatus


class ApprovalResponse(BaseModel):
    application_id: str
    status: ApplicationStatus
    approval_token: str
    expires_at: datetime


class SubmissionRequest(BaseModel):
    approval_token: str = Field(min_length=20)


class SubmissionRecoveryRequest(BaseModel):
    confirmed_not_submitted: bool

    @model_validator(mode="after")
    def require_explicit_confirmation(self) -> "SubmissionRecoveryRequest":
        if not self.confirmed_not_submitted:
            raise ValueError("Confirm that the employer did not receive the application")
        return self


class DashboardStats(BaseModel):
    jobs_discovered: int
    strong_matches: int
    ready_to_apply: int
    applications_submitted: int
    interviews: int
    offers: int
    highest_matches: list[dict]
    application_queue: list[dict]
    recent_applications: list[dict]


class FunnelAnalytics(BaseModel):
    applications: int
    rejections: int
    interviews: int
    offers: int
    pending: int
    application_to_interview_rate: float
    interview_to_offer_rate: float


class AIAnswer(BaseModel):
    status: str = Field(pattern=r"^(ANSWERED|NEEDS_USER_INPUT)$")
    answer: str | None = None
    grounded_facts: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
