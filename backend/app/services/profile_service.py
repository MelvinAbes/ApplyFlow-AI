import re
from datetime import datetime, timezone

from sqlmodel import Session, delete, select

from app.models import CandidateProfile, Education, Language, WorkExperience
from app.schemas.profile import CandidateProfileInput, CandidateProfileRead

PROFILE_FIELDS = set(CandidateProfileInput.model_fields) - {
    "educations",
    "work_experiences",
    "languages",
}


class ProfileService:
    def __init__(self, session: Session):
        self.session = session

    def get_model(self) -> CandidateProfile | None:
        return self.session.exec(select(CandidateProfile).limit(1)).first()

    def get(self) -> CandidateProfileRead | None:
        profile = self.get_model()
        return self._aggregate(profile) if profile else None

    def upsert(self, payload: CandidateProfileInput) -> CandidateProfileRead:
        profile = self.get_model()
        now = datetime.now(timezone.utc)
        values = payload.model_dump(include=PROFILE_FIELDS)
        street_name = values.get("street_name")
        house_number = values.get("house_number")
        address = values.get("address")
        if address and not street_name and not house_number:
            parsed = split_street_address(address)
            if parsed:
                values["street_name"], values["house_number"] = parsed
        elif not address and street_name and house_number:
            values["address"] = combine_street_address(street_name, house_number)
        if profile is None:
            profile = CandidateProfile(**values)
            self.session.add(profile)
            self.session.flush()
        else:
            for field, value in values.items():
                setattr(profile, field, value)
            profile.revision += 1
            profile.updated_at = now
            self.session.add(profile)
            self.session.flush()

        self.session.exec(delete(Education).where(Education.candidate_id == profile.id))
        self.session.exec(delete(WorkExperience).where(WorkExperience.candidate_id == profile.id))
        self.session.exec(delete(Language).where(Language.candidate_id == profile.id))
        for item in payload.educations:
            self.session.add(Education(candidate_id=profile.id, **item.model_dump(exclude={"id"})))
        for item in payload.work_experiences:
            self.session.add(
                WorkExperience(candidate_id=profile.id, **item.model_dump(exclude={"id"}))
            )
        for item in payload.languages:
            self.session.add(Language(candidate_id=profile.id, **item.model_dump(exclude={"id"})))
        self.session.commit()
        self.session.refresh(profile)
        return self._aggregate(profile)

    def flat_facts(self) -> dict[str, str | None]:
        profile = self.get_model()
        if profile is None:
            return {}
        education = self.session.exec(
            select(Education).where(Education.candidate_id == profile.id)
        ).all()
        languages = self.session.exec(
            select(Language).where(Language.candidate_id == profile.id)
        ).all()
        current_education = next((item for item in education if item.current_student), None)
        facts: dict[str, str | None] = {
            field: getattr(profile, field)
            for field in (
                "first_name",
                "last_name",
                "preferred_name",
                "email",
                "phone",
                "city",
                "country",
                "postal_code",
                "street_name",
                "house_number",
                "address",
                "nationality",
                "date_of_birth",
                "gender",
                "honorific_title",
                "name_suffix",
                "current_title",
                "linkedin",
                "github",
                "portfolio",
                "personal_website",
                "availability",
                "preferred_start_date",
                "work_authorization",
                "visa_status",
                "notice_period",
                "salary_expectation",
                "weekly_hours",
                "relocation_willingness",
                "travel_willingness",
                "consider_other_positions",
                "drivers_license",
            )
        }
        facts["full_name"] = (
            " ".join(part for part in (profile.first_name, profile.last_name) if part) or None
        )
        if profile.address and not facts["street_name"] and not facts["house_number"]:
            parsed_address = split_street_address(profile.address)
            if parsed_address:
                facts["street_name"], facts["house_number"] = parsed_address
        if not facts["address"] and facts["street_name"] and facts["house_number"]:
            facts["address"] = combine_street_address(
                facts["street_name"], facts["house_number"]
            )
        facts["preferred_start_date"] = (
            profile.preferred_start_date.isoformat() if profile.preferred_start_date else None
        )
        facts["date_of_birth"] = (
            profile.date_of_birth.isoformat() if profile.date_of_birth else None
        )
        facts["phone_country_code"] = self._phone_country_code(profile.phone, profile.country)
        facts["preferred_location_primary"] = (
            profile.preferred_locations[0] if profile.preferred_locations else None
        )
        facts["preferred_location_secondary"] = (
            profile.preferred_locations[1] if len(profile.preferred_locations) > 1 else None
        )
        facts["university"] = (
            current_education.institution
            if current_education
            else (education[0].institution if education else None)
        )
        facts["degree"] = (
            current_education.degree
            if current_education
            else (education[0].degree if education else None)
        )
        facts["field_of_study"] = current_education.field_of_study if current_education else None
        graduation = current_education.graduation_date if current_education else None
        facts["graduation_date"] = graduation.isoformat() if graduation else None
        facts["current_student"] = (
            "Yes"
            if any(item.current_student for item in education)
            else ("No" if education else None)
        )
        for item in languages:
            language_key = item.language.casefold().replace(" ", "_")
            facts[f"language_{language_key}"] = item.proficiency
            if language_key in {"german", "deutsch"}:
                facts["german_level"] = item.proficiency
            if language_key in {"english", "englisch"}:
                facts["english_level"] = item.proficiency
        return facts

    @staticmethod
    def _phone_country_code(phone: str | None, country: str | None) -> str | None:
        if phone and phone.startswith("+"):
            digits = "".join(character for character in phone[1:] if character.isdigit())
            known = ("49", "44", "43", "41", "33", "31", "39", "34", "1")
            prefix = next((value for value in known if digits.startswith(value)), None)
            if prefix:
                return f"+{prefix}"
        country_codes = {
            "germany": "+49",
            "deutschland": "+49",
            "austria": "+43",
            "osterreich": "+43",
            "switzerland": "+41",
            "schweiz": "+41",
        }
        return country_codes.get((country or "").casefold())

    def professional_context(self) -> dict:
        profile = self.get_model()
        if profile is None:
            return {}
        education = self.session.exec(
            select(Education).where(Education.candidate_id == profile.id)
        ).all()
        roles = self.session.exec(
            select(WorkExperience).where(WorkExperience.candidate_id == profile.id)
        ).all()
        languages = self.session.exec(
            select(Language).where(Language.candidate_id == profile.id)
        ).all()
        return {
            "current_title": profile.current_title,
            "skills": profile.skills,
            "programming_languages": profile.programming_languages,
            "frameworks": profile.frameworks,
            "tools": profile.tools,
            "education": [
                {
                    "institution": item.institution,
                    "degree": item.degree,
                    "field": item.field_of_study,
                    "current_student": item.current_student,
                    "graduation_date": (
                        item.graduation_date.isoformat() if item.graduation_date else None
                    ),
                }
                for item in education
            ],
            "experience": [
                {
                    "title": item.title,
                    "company": item.company,
                    "description": item.description,
                }
                for item in roles
            ],
            "languages": [
                {"language": item.language, "proficiency": item.proficiency} for item in languages
            ],
            "preferences": {
                "locations": profile.preferred_locations,
                "job_types": profile.preferred_job_types,
                "technologies": profile.preferred_technologies,
                "undesired_roles": profile.undesired_roles,
                "remote": profile.remote_preference,
            },
        }

    def contact_values(self) -> list[str]:
        profile = self.get_model()
        if profile is None:
            return []
        full_name = " ".join(
            part for part in (profile.first_name, profile.last_name) if part
        ).strip()
        return [
            value
            for value in (
                profile.email,
                profile.phone,
                profile.address,
                profile.postal_code,
                profile.linkedin,
                profile.github,
                profile.portfolio,
                profile.personal_website,
                full_name or None,
            )
            if value
        ]

    def _aggregate(self, profile: CandidateProfile) -> CandidateProfileRead:
        educations = self.session.exec(
            select(Education).where(Education.candidate_id == profile.id)
        ).all()
        experiences = self.session.exec(
            select(WorkExperience).where(WorkExperience.candidate_id == profile.id)
        ).all()
        languages = self.session.exec(
            select(Language).where(Language.candidate_id == profile.id)
        ).all()
        data = profile.model_dump()
        data.update(
            educations=[item.model_dump() for item in educations],
            work_experiences=[item.model_dump() for item in experiences],
            languages=[item.model_dump() for item in languages],
        )
        return CandidateProfileRead.model_validate(data)


def split_street_address(address: str) -> tuple[str, str] | None:
    """Split an unambiguous street-plus-number value without guessing.

    The conservative pattern supports common European/German suffixes such as 12a, 12 A,
    12-14, and 12/3. Full postal addresses, number-first formats, and values without both a
    real street name and trailing number intentionally remain unresolved.
    """

    cleaned = " ".join(address.split())
    if not cleaned or any(separator in cleaned for separator in (",", ";")):
        return None
    match = re.fullmatch(
        r"(?P<street>.*[A-Za-zÀ-ÖØ-öø-ÿß])\s+"
        r"(?P<number>\d+\s*[A-Za-z]?(?:\s*[/-]\s*\d+\s*[A-Za-z]?)?)",
        cleaned,
    )
    if not match:
        return None
    street = match.group("street").strip()
    number = re.sub(r"\s+", "", match.group("number").strip())
    if len(street) < 2:
        return None
    return street, number


def combine_street_address(street_name: str, house_number: str) -> str:
    return f"{street_name.strip()} {house_number.strip()}".strip()
