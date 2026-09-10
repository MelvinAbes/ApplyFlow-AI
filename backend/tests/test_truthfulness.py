from datetime import date

import pytest

from app.ai.question_answerer import QuestionAnswerer
from app.automation.field_detector import DetectedField
from app.automation.resolver import FieldResolver
from app.models import CandidateProfile, Job
from app.services.field_mapping import FieldMappingService
from app.services.job_service import description_hash
from app.services.profile_service import ProfileService, split_street_address


@pytest.mark.asyncio
async def test_missing_factual_answer_requires_user_input(session, test_settings) -> None:
    profile = CandidateProfile(first_name="Ada")
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(profile)
    session.add(job)
    session.commit()
    field = DetectedField(
        selector="#salary",
        question="What are your salary expectations?",
        field_type="text",
        required=True,
    )
    resolved = await FieldResolver(session, test_settings).resolve(field, job, None)
    assert resolved.answer is None
    assert resolved.source.value == "unknown"


@pytest.mark.asyncio
async def test_ai_is_not_called_for_unknown_factual_question(session, test_settings) -> None:
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    result = await QuestionAnswerer(session, test_settings).answer(
        "Do you hold an AWS certification?", job, None
    )
    assert result.status == "NEEDS_USER_INPUT"
    assert result.answer is None


def test_current_student_is_unknown_when_education_is_absent(session) -> None:
    session.add(CandidateProfile(first_name="Ada"))
    session.commit()
    assert ProfileService(session).flat_facts()["current_student"] is None


def test_combined_address_is_split_only_when_unambiguous() -> None:
    assert split_street_address("Musterstraße 42") == ("Musterstraße", "42")
    assert split_street_address("Hauptstraße 12 A") == ("Hauptstraße", "12A")
    assert split_street_address("Unter den Linden 5-7") == ("Unter den Linden", "5-7")
    assert split_street_address("12 Main Street") is None
    assert split_street_address("Musterstraße, Berlin") is None


@pytest.mark.asyncio
async def test_separate_address_fields_use_only_address_facts(session, test_settings) -> None:
    session.add(
        CandidateProfile(
            first_name="Ada Marie",
            last_name="Lovelace",
            phone="+49301234567",
            address="Musterstraße 42",
        )
    )
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    session.commit()
    resolver = FieldResolver(session, test_settings)

    street = await resolver.resolve(
        DetectedField(selector="#street", question="Street Name", field_type="text"),
        job,
        None,
    )
    house = await resolver.resolve(
        DetectedField(selector="#house", question="House Number", field_type="text"),
        job,
        None,
    )

    assert street.field_key == "street_name"
    assert street.answer == "Musterstraße"
    assert street.source.value == "candidate_profile"
    assert house.field_key == "house_number"
    assert house.answer == "42"
    assert house.source.value == "candidate_profile"


@pytest.mark.asyncio
async def test_ambiguous_combined_address_does_not_guess_components(
    session, test_settings
) -> None:
    session.add(CandidateProfile(address="12 Main Street"))
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    session.commit()

    result = await FieldResolver(session, test_settings).resolve(
        DetectedField(selector="#house", question="House Number", field_type="text"),
        job,
        None,
    )

    assert result.field_key == "house_number"
    assert result.answer is None
    assert result.source.value == "unknown"


@pytest.mark.asyncio
async def test_confirmed_field_meaning_is_reused_separately_from_answers(
    session, test_settings
) -> None:
    session.add(CandidateProfile(street_name="Musterstraße"))
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    FieldMappingService(session).remember(
        label="Residence road",
        canonical_key="street_name",
    )
    session.commit()

    resolved = await FieldResolver(session, test_settings).resolve(
        DetectedField(selector="#road", question="Residence road", field_type="text"),
        job,
        None,
    )

    assert resolved.field_key == "street_name"
    assert resolved.answer == "Musterstraße"
    assert resolved.source.value == "candidate_profile"


@pytest.mark.asyncio
async def test_german_profile_fields_use_original_employer_values(session, test_settings) -> None:
    session.add(
        CandidateProfile(
            first_name="Ada",
            date_of_birth=date(1990, 5, 17),
            gender="Male",
            consider_other_positions="Yes",
        )
    )
    job = Job(
        company="Deloitte",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    session.commit()
    resolver = FieldResolver(session, test_settings)

    birth_date = await resolver.resolve(
        DetectedField(
            selector="#birth-date",
            question="Geburtsdatum",
            translated_question="Date of birth",
            source_language="de",
            canonical_key="date_of_birth",
            field_type="date",
            required=True,
        ),
        job,
        None,
    )
    other_roles = await resolver.resolve(
        DetectedField(
            selector="[name=other_roles]",
            question="Dürfen wir dich auch für weitere offene Stellen prüfen?",
            translated_question="May this employer consider you for other open positions?",
            source_language="de",
            canonical_key="consider_other_positions",
            field_type="radio",
            required=True,
            options=["Ja, weitere offene Stellen", "Nein, nur die beworbene Stelle"],
            translated_options=[
                "Consider me for other open positions",
                "Consider me only for the position I applied for",
            ],
        ),
        job,
        None,
    )
    gender = await resolver.resolve(
        DetectedField(
            selector="#gender",
            question="Geschlecht",
            translated_question="Gender",
            source_language="de",
            canonical_key="gender",
            field_type="select",
            options=["Männlich", "Weiblich", "Divers", "Keine Angabe"],
            translated_options=["Male", "Female", "Diverse", "Prefer not to say"],
        ),
        job,
        None,
    )

    assert birth_date.answer == "1990-05-17"
    assert other_roles.answer == "Ja, weitere offene Stellen"
    assert gender.answer == "Männlich"
