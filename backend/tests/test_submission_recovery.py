from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import select

from app.ats.generic import GenericATSAdapter
from app.automation.browser import BrowserManager
from app.automation.field_detector import detect_fields, form_fingerprint, mark_application_form
from app.automation.submission import SubmissionBlockedError
from app.models import Application, ApplicationEvent, ApplicationField, Job
from app.models.enums import AnswerSource, ApplicationStatus, EventType
from app.services.application_service import ApplicationService
from app.services.job_service import description_hash


@pytest.mark.asyncio
async def test_uncertain_submission_requires_recovery_and_fresh_manual_approval(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "submission_recovery_form.html"
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture.as_uri(),
    )
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.SUBMISSION_UNCONFIRMED,
        current_url=fixture.as_uri(),
        form_action="final",
        approval_consumed_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.add(application)
    session.commit()

    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        raw_form = await GenericATSAdapter().find_form(page)
        assert raw_form is not None
        form = await mark_application_form(page, raw_form)
        detected = await detect_fields(page, form)
        application.form_fingerprint = await form_fingerprint(page, form, detected)
        field = ApplicationField(
            application_id=application.id,
            selector=detected[0].selector,
            question=detected[0].question,
            field_key="first_name",
            field_type="text",
            answer="Ada",
            source=AnswerSource.USER_ENTERED,
            confidence=1,
            required=True,
        )
        session.add(application)
        session.add(field)
        session.commit()
        service = ApplicationService(session, test_settings, browser)

        with pytest.raises(SubmissionBlockedError):
            await service.prepare_manual_submission(application, "not-approved")

        recovered = await service.recover_not_submitted(application)
        assert recovered.status == ApplicationStatus.READY_FOR_REVIEW
        assert recovered.approval_consumed_at is None
        await page.locator("#submit").click()
        assert await page.evaluate("window.submits || 0") == 0

        token, _ = await service.approve(recovered)
        prepared = await service.prepare_manual_submission(recovered, token)
        assert prepared.status == ApplicationStatus.SUBMISSION_UNCONFIRMED
        assert prepared.waiting_reason == "manual_submission"
        assert await page.evaluate("document.activeElement.id") == "submit"

        await page.locator("#submit").click()
        assert await page.evaluate("window.submits || 0") == 1
        await page.locator("#submit").click()
        assert await page.evaluate("window.submits || 0") == 2

        events = session.exec(
            select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        ).all()
        event_types = {event.event_type for event in events}
        assert EventType.SUBMISSION_RETRY_AUTHORIZED.value in event_types
        assert EventType.MANUAL_SUBMISSION_ARMED.value in event_types
    finally:
        await browser.close()
