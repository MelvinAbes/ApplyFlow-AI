from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class EducationInput(BaseModel):
    id: str | None = None
    institution: str = Field(min_length=1, max_length=300)
    degree: str | None = None
    field_of_study: str | None = None
    start_date: date | None = None
    graduation_date: date | None = None
    current_student: bool = False


class WorkExperienceInput(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    company: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None


class LanguageInput(BaseModel):
    id: str | None = None
    language: str = Field(min_length=1, max_length=100)
    proficiency: str | None = None


class CandidateProfileInput(BaseModel):
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
    skills: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    personal_website: str | None = None
    preferred_job_types: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    remote_preference: str | None = None
    minimum_match_score: int = Field(default=60, ge=0, le=100)
    preferred_technologies: list[str] = Field(default_factory=list)
    undesired_roles: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
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
    educations: list[EducationInput] = Field(default_factory=list)
    work_experiences: list[WorkExperienceInput] = Field(default_factory=list)
    languages: list[LanguageInput] = Field(default_factory=list)


class CandidateProfileRead(CandidateProfileInput):
    id: str
    revision: int
    created_at: datetime
    updated_at: datetime


class ResumeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    label: str
    target_roles: list[str]
    skills: list[str]
    is_default: bool
    created_at: datetime


class ResumeUpdate(BaseModel):
    label: str | None = None
    target_roles: list[str] | None = None
    skills: list[str] | None = None
    is_default: bool | None = None


class AnswerBankInput(BaseModel):
    canonical_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(min_length=2)
    answer: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class AnswerBankRead(AnswerBankInput):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
