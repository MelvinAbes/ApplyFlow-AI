from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Application, ApplicationEvent, ApplicationField, Job
from app.models.enums import ApplicationStatus, EventType
from app.services.application_service import ApplicationService, ApplicationStateError
from app.services.job_service import description_hash


class TrackingBrowser:
    def __init__(self) -> None:
        self.forgotten: list[str] = []

    async def forget(self, key: str) -> None:
        self.forgotten.append(key)


def make_job(session) -> Job:
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url="https://jobs.example/42",
    )
    session.add(job)
    session.commit()
    return job


@pytest.mark.asyncio
async def test_confirmed_unsubmitted_attempt_is_archived_before_clean_restart(
    session, test_settings, monkeypatch
) -> None:
    job = make_job(session)
    original = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        current_url=job.application_url,
    )
    session.add(original)
    session.commit()
    original_field = ApplicationField(
        application_id=original.id,
        selector="#birth-date",
        question="Date of birth",
        field_type="date",
        required=True,
    )
    session.add(original_field)
    session.add(
        ApplicationEvent(
            application_id=original.id,
            event_type=EventType.SUBMISSION_RETRY_AUTHORIZED.value,
        )
    )
    session.commit()
    browser = TrackingBrowser()
    service = ApplicationService(session, test_settings, browser)  # type: ignore[arg-type]

    async def create_fresh_application(job, resume, target_url=None):
        restarted = Application(
            job_id=job.id,
            resume_id=resume.id if resume else None,
            status=ApplicationStatus.FORM_LOADING,
            current_url=target_url or job.application_url,
        )
        session.add(restarted)
        session.commit()
        session.refresh(restarted)
        return restarted

    monkeypatch.setattr(service, "start", create_fresh_application)

    restarted = await service.archive_and_restart(original)

    archived = session.get(Application, original.id)
    assert archived is not None
    assert archived.status == ApplicationStatus.ARCHIVED
    assert archived.archived_at is not None
    assert restarted.id != archived.id
    assert restarted.job_id == archived.job_id
    assert browser.forgotten == [archived.id]
    assert session.get(ApplicationField, original_field.id) is not None

    events = session.exec(
        select(ApplicationEvent).where(ApplicationEvent.application_id == archived.id)
    ).all()
    event_types = {event.event_type for event in events}
    assert EventType.APPLICATION_ARCHIVED.value in event_types
    assert EventType.APPLICATION_RESTARTED.value in event_types
    restart_event = next(
        event
        for event in events
        if event.event_type == EventType.APPLICATION_RESTARTED.value
    )
    assert restart_event.details["replacement_application_id"] == restarted.id

    session.add(
        Application(
            job_id=job.id,
            status=ApplicationStatus.NEEDS_USER_INPUT,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


@pytest.mark.asyncio
async def test_restart_requires_recorded_confirmation_that_nothing_was_submitted(
    session, test_settings
) -> None:
    job = make_job(session)
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
    )
    session.add(application)
    session.commit()

    service = ApplicationService(
        session,
        test_settings,
        TrackingBrowser(),  # type: ignore[arg-type]
    )
    with pytest.raises(ApplicationStateError, match="explicitly confirmed"):
        await service.archive_and_restart(application)

    stored = session.get(Application, application.id)
    assert stored is not None
    assert stored.archived_at is None


@pytest.mark.asyncio
async def test_unconfirmed_or_submitted_application_cannot_restart(
    session, test_settings
) -> None:
    job = make_job(session)
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.SUBMISSION_UNCONFIRMED,
        approval_consumed_at=datetime.now(timezone.utc),
    )
    session.add(application)
    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=EventType.SUBMISSION_RETRY_AUTHORIZED.value,
        )
    )
    session.commit()

    service = ApplicationService(
        session,
        test_settings,
        TrackingBrowser(),  # type: ignore[arg-type]
    )
    with pytest.raises(ApplicationStateError, match="verify the employer result"):
        await service.archive_and_restart(application)
