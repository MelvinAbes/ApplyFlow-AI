from datetime import datetime, timezone

import pytest

from app.models import Application, Job
from app.models.enums import ApplicationStatus
from app.schemas.job import JobCreate
from app.services.application_service import ApplicationService, DuplicateApplicationError
from app.services.job_service import DuplicateJobError, JobService


class NoBrowser:
    pass


def test_exact_job_url_is_rejected_as_duplicate(session) -> None:
    service = JobService(session)
    payload = JobCreate(
        company="Acme",
        title="Working Student AI",
        description="Python",
        application_url="https://jobs.example/42?utm_source=newsletter",
    )
    existing = service.create(payload)
    with pytest.raises(DuplicateJobError) as error:
        service.create(
            payload.model_copy(
                update={"application_url": "https://jobs.example/42?utm_source=another"}
            )
        )
    assert error.value.existing_id == existing.id


def test_same_company_and_role_is_marked_probable_duplicate(session) -> None:
    service = JobService(session)
    original = service.create(
        JobCreate(company="Acme", title="Data Intern", application_url="https://a.example/1")
    )
    second = service.create(
        JobCreate(company="Acme", title="Data Intern", application_url="https://a.example/2")
    )
    assert second.probable_duplicate_of == original.id


def test_same_url_is_duplicate_even_if_title_changed(session) -> None:
    service = JobService(session)
    original = service.create(
        JobCreate(company="Acme", title="Data Intern", application_url="https://a.example/9")
    )
    with pytest.raises(DuplicateJobError) as error:
        service.create(
            JobCreate(
                company="Acme GmbH",
                title="Data Engineering Internship",
                application_url="https://a.example/9/",
            )
        )
    assert error.value.existing_id == original.id


@pytest.mark.asyncio
async def test_second_application_for_the_same_job_is_rejected(session, test_settings) -> None:
    job = Job(
        company="Acme",
        title="Data Intern",
        description="Python",
        description_sha256="fixture",
        application_url="https://jobs.example/42",
    )
    session.add(job)
    session.commit()
    existing = Application(job_id=job.id, status=ApplicationStatus.READY_FOR_REVIEW)
    session.add(existing)
    session.commit()

    service = ApplicationService(session, test_settings, NoBrowser())  # type: ignore[arg-type]
    with pytest.raises(DuplicateApplicationError) as error:
        await service.start(job, None)
    assert error.value.existing_id == existing.id


@pytest.mark.asyncio
async def test_archived_attempt_does_not_block_one_fresh_application(
    session, test_settings
) -> None:
    job = Job(
        company="Acme",
        title="Data Intern",
        description="Python",
        description_sha256="fixture-archive",
        application_url="https://jobs.example/43",
    )
    session.add(job)
    session.commit()
    archived = Application(
        job_id=job.id,
        status=ApplicationStatus.ARCHIVED,
        archived_at=datetime.now(timezone.utc),
    )
    session.add(archived)
    session.commit()

    service = ApplicationService(session, test_settings, NoBrowser())  # type: ignore[arg-type]
    restarted = await service.start(job, None)

    assert restarted.id != archived.id
    assert restarted.job_id == archived.job_id
