from pathlib import Path

import pytest

from app.automation.browser import BrowserManager
from app.models import Application, ApplicationField, Job
from app.models.enums import ApplicationStatus
from app.schemas.profile import CandidateProfileInput
from app.services.answer_bank import AnswerBankService
from app.services.application_service import ApplicationService
from app.services.field_mapping import FieldMappingService
from app.services.job_service import description_hash
from app.services.profile_service import ProfileService


@pytest.mark.asyncio
async def test_separate_address_controls_receive_only_structured_address_facts(
    session, test_settings
) -> None:
    ProfileService(session).upsert(
        CandidateProfileInput(
            first_name="Ada Marie",
            last_name="Lovelace",
            phone="+49301234567",
            address="Musterstraße 42",
            postal_code="10115",
            city="Berlin",
        )
    )
    fixture = Path(__file__).parent / "fixtures" / "structured_address_form.html"
    job = Job(
        company="Acme",
        title="Platform Engineer",
        description="Build reliable software",
        description_sha256=description_hash("Build reliable software"),
        application_url=fixture.as_uri(),
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        service = ApplicationService(session, test_settings, browser)

        application = await service.start(job, resume=None)
        review = service.read(application)
        page = browser.page(application.id)

        assert application.status == ApplicationStatus.READY_FOR_REVIEW
        assert page is not None
        assert await page.locator("#first-name").input_value() == "Ada Marie"
        assert await page.locator("#phone").input_value() == "+49301234567"
        assert await page.locator("#street").input_value() == "Musterstraße"
        assert await page.locator("#house-number").input_value() == "42"
        assert await page.locator("#postal-code").input_value() == "10115"
        assert await page.locator("#city").input_value() == "Berlin"
        by_question = {field.question: field for field in review.fields}
        assert by_question["Street Name"].field_key == "street_name"
        assert by_question["House Number"].field_key == "house_number"
        assert by_question["Street Name"].answer != "Ada Marie Lovelace"
        assert by_question["House Number"].answer != "301234567"
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_explicit_semantic_correction_teaches_mapping_not_answer(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "structured_address_form.html"
    job = Job(
        company="Acme",
        title="Platform Engineer",
        description="Build reliable software",
        description_sha256=description_hash("semantic-correction"),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        current_url=fixture.as_uri(),
    )
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#road",
        question="Residence road",
        field_type="text",
        required=True,
    )
    session.add(field)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        await page.set_content(
            """
            <form><label for="road">Residence road</label><input id="road" required></form>
            """
        )

        await ApplicationService(session, test_settings, browser).update_field(
            field,
            "Musterstraße",
            semantic_key="street_name",
        )

        learned = FieldMappingService(session).match("Residence road")
        assert learned is not None
        assert learned.canonical_key == "street_name"
        assert AnswerBankService(session).list() == []
        assert await page.locator("#road").input_value() == "Musterstraße"
    finally:
        await browser.close()
