from datetime import datetime, timezone

import pytest
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


def make_job(session, url: str) -> Job:
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=url,
        source_url=url,
    )
    session.add(job)
    session.commit()
    return job


@pytest.mark.asyncio
async def test_unsubmitted_draft_can_be_deleted(session, test_settings) -> None:
    job = make_job(session, "https://jobs.example/42")
    application = Application(job_id=job.id, status=ApplicationStatus.NEEDS_USER_INPUT)
    session.add(application)
    session.commit()
    session.add(
        ApplicationField(
            application_id=application.id,
            selector="#motivation",
            question="Why are you interested?",
            field_type="textarea",
        )
    )
    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=EventType.APPLICATION_STARTED.value,
        )
    )
    session.commit()
    browser = TrackingBrowser()

    await ApplicationService(session, test_settings, browser).delete_application(application)  # type: ignore[arg-type]

    assert session.get(Application, application.id) is None
    assert (
        session.exec(
            select(ApplicationField).where(ApplicationField.application_id == application.id)
        ).first()
        is None
    )
    assert (
        session.exec(
            select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        ).first()
        is None
    )
    assert browser.forgotten == [application.id]


@pytest.mark.asyncio
async def test_real_submission_record_cannot_be_deleted(session, test_settings) -> None:
    job = make_job(session, "https://jobs.example/42")
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.SUBMITTED,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(application)
    session.commit()

    with pytest.raises(ApplicationStateError, match="sent to an employer"):
        await ApplicationService(
            session,
            test_settings,
            TrackingBrowser(),  # type: ignore[arg-type]
        ).delete_application(application)

    assert session.get(Application, application.id) is not None


@pytest.mark.asyncio
async def test_real_multistep_progress_cannot_be_deleted(session, test_settings) -> None:
    job = make_job(session, "https://jobs.example/43")
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        current_step=2,
    )
    session.add(application)
    session.commit()

    with pytest.raises(ApplicationStateError, match="sent to an employer"):
        await ApplicationService(
            session,
            test_settings,
            TrackingBrowser(),  # type: ignore[arg-type]
        ).delete_application(application)

    assert session.get(Application, application.id) is not None


@pytest.mark.asyncio
async def test_archived_unsubmitted_attempt_can_be_deleted(session, test_settings) -> None:
    job = make_job(session, "https://jobs.example/archived")
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.ARCHIVED,
        current_step=3,
        archived_at=datetime.now(timezone.utc),
    )
    session.add(application)
    session.commit()
    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=EventType.SUBMISSION_UNCONFIRMED.value,
        )
    )
    session.commit()
    browser = TrackingBrowser()
    service = ApplicationService(session, test_settings, browser)  # type: ignore[arg-type]

    assert service.read(application).can_delete is True

    await service.delete_application(application)

    assert session.get(Application, application.id) is None
    assert browser.forgotten == [application.id]


@pytest.mark.asyncio
async def test_archived_attempt_with_submission_evidence_cannot_be_deleted(
    session, test_settings
) -> None:
    job = make_job(session, "https://jobs.example/submitted-archive")
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.ARCHIVED,
        archived_at=datetime.now(timezone.utc),
    )
    session.add(application)
    session.commit()
    session.add(
        ApplicationEvent(
            application_id=application.id,
            event_type=EventType.APPLICATION_SUBMITTED.value,
        )
    )
    session.commit()
    service = ApplicationService(
        session,
        test_settings,
        TrackingBrowser(),  # type: ignore[arg-type]
    )

    assert service.read(application).can_delete is False
    with pytest.raises(ApplicationStateError, match="sent to an employer"):
        await service.delete_application(application)

    assert session.get(Application, application.id) is not None


@pytest.mark.asyncio
async def test_submitted_local_demo_can_be_deleted_and_redone(session, test_settings) -> None:
    demo_url = f"{test_settings.app_base_url}/demo/application"
    job = make_job(session, demo_url)
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.WITHDRAWN,
        current_url=demo_url,
        submitted_at=datetime.now(timezone.utc),
    )
    session.add(application)
    session.commit()
    browser = TrackingBrowser()
    service = ApplicationService(session, test_settings, browser)  # type: ignore[arg-type]

    assert service.read(application).is_demo is True
    assert service.read(application).can_delete is True

    await service.delete_application(application)

    assert session.get(Application, application.id) is None
