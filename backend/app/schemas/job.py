from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobCreate(BaseModel):
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    location: str | None = None
    description: str = ""
    employment_type: str | None = None
    source_url: str | None = None
    application_url: str | None = None
    source: str = "manual_text"
    external_job_id: str | None = None


class JobUrlInput(BaseModel):
    url: str = Field(pattern=r"^https?://")


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    company: str
    title: str
    location: str | None
    description: str
    employment_type: str | None
    source_url: str | None
    application_url: str | None
    source: str
    external_job_id: str | None
    probable_duplicate_of: str | None
    created_at: datetime
    updated_at: datetime


class JobAnalysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    skill_match_score: int = Field(ge=0, le=100)
    experience_match_score: int = Field(ge=0, le=100)
    education_match_score: int = Field(ge=0, le=100)
    location_match_score: int = Field(ge=0, le=100)
    recommendation: Literal["strong_apply", "apply", "maybe", "skip"]
    matching_skills: list[str]
    missing_skills: list[str]
    must_have_requirements: list[str]
    unmet_must_have_requirements: list[str]
    strengths: list[str]
    concerns: list[str]
    recommended_resume_id: str | None = None
    summary: str = Field(min_length=1, max_length=1500)

    @model_validator(mode="after")
    def recommendation_matches_score(self) -> "JobAnalysis":
        allowed = {
            "strong_apply": range(80, 101),
            "apply": range(65, 101),
            "maybe": range(35, 81),
            "skip": range(0, 66),
        }
        if self.overall_score not in allowed[self.recommendation]:
            raise ValueError("recommendation is inconsistent with overall_score")
        return self


class JobAnalysisRead(JobAnalysis):
    analysis_id: str
    ai_used: bool
    created_at: datetime


class JobListItem(JobRead):
    match_score: int | None = None
    recommendation: str | None = None


class CsvImportResult(BaseModel):
    imported: int
    duplicates: int
    errors: list[str]
