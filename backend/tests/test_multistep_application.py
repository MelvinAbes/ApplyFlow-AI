from pathlib import Path

import pytest

from app.automation.browser import BrowserManager
from app.models import ApplicationField, Job
from app.models.enums import ApplicationStatus
from app.schemas.profile import CandidateProfileInput
from app.services.application_service import ApplicationService
from app.services.job_service import description_hash
from app.services.profile_service import ProfileService


@pytest.mark.asyncio
async def test_multistep_form_advances_separately_from_final_submission(
    session, test_settings
) -> None:
    ProfileService(session).upsert(
        CandidateProfileInput(first_name="Ada", email="ada@example.test")
    )
    fixture_url = (Path(__file__).parent / "fixtures" / "multistep_application_form.html").as_uri()
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture_url,
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        application = await service.start(job, None)
        assert application.status == ApplicationStatus.READY_TO_ADVANCE
        first_step = service.read(application)
        assert first_step.current_step == 1
        assert len(first_step.fields) == 1
        assert first_step.fields[0].answer == "Ada"
        assert first_step.fields[0].active

        application = await service.advance(application)
        assert application.status == ApplicationStatus.READY_FOR_REVIEW
        second_step = service.read(application)
        assert second_step.current_step == 2
        assert len(second_step.fields) == 2
        assert not second_step.fields[0].active
        assert second_step.fields[1].active
        assert second_step.fields[1].answer == "ada@example.test"

        token, _ = await service.approve(application)
        submitted = await service.submit(application, token)
        assert submitted.status == ApplicationStatus.SUBMITTED
        page = browser.page(application.id)
        assert page is not None
        assert await page.locator("#success").is_visible()
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_disabled_action_can_be_rechecked_after_required_answer(
    session, test_settings
) -> None:
    ProfileService(session).upsert(
        CandidateProfileInput(first_name="Ada", email="ada@example.test")
    )
    fixture_url = (Path(__file__).parent / "fixtures" / "form_action_recheck.html").as_uri()
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture_url,
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        application = await service.start(job, None)
        assert application.status == ApplicationStatus.NEEDS_USER_INPUT
        missing_read = next(field for field in service.read(application).fields if not field.answer)
        missing = session.get(ApplicationField, missing_read.id)
        assert missing is not None

        application = await service.update_field(missing, "Yes")
        assert application.status == ApplicationStatus.WAITING_FOR_USER
        assert application.waiting_reason == "ambiguous_form_action"

        application = await service.recheck_form_action(application)
        assert application.status == ApplicationStatus.READY_FOR_REVIEW
        assert application.form_action == "final"
        page = browser.page(application.id)
        assert page is not None
        assert await page.evaluate("window.submitCount || 0") == 0
    finally:
        await browser.close()
