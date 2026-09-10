from datetime import timedelta

import pytest

from app.automation.submission import (
    SubmissionBlockedError,
    guarded_submit,
    issue_approval_token,
    verify_approval_token,
)
from app.models import Application, ApplicationField, Job
from app.models.enums import ApplicationStatus
from app.services.application_service import ApplicationService, ApplicationStateError
from app.services.job_service import description_hash


class NoBrowser:
    def page(self, key):
        return None


@pytest.mark.asyncio
async def test_browser_submit_is_impossible_without_approval() -> None:
    with pytest.raises(SubmissionBlockedError, match="approval"):
        await guarded_submit(None, approved=False)  # type: ignore[arg-type]


def test_approval_token_is_bound_and_expires(test_settings) -> None:
    token, digest, expires = issue_approval_token(test_settings)
    assert verify_approval_token(token, digest, expires)
    assert not verify_approval_token("wrong-token", digest, expires)
    assert not verify_approval_token(token, digest, expires - timedelta(hours=1))


@pytest.mark.asyncio
async def test_missing_answer_blocks_backend_approval(session, test_settings) -> None:
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    session.commit()
    application = Application(job_id=job.id, status=ApplicationStatus.READY_FOR_REVIEW)
    session.add(application)
    session.commit()
    session.add(
        ApplicationField(
            application_id=application.id,
            selector="#unknown",
            question="Unknown factual question",
            field_type="text",
            required=True,
        )
    )
    session.commit()
    service = ApplicationService(session, test_settings, NoBrowser())  # type: ignore[arg-type]
    with pytest.raises(ApplicationStateError, match="resolved"):
        await service.approve(application)


@pytest.mark.asyncio
async def test_file_field_cannot_be_resolved_with_text(session, test_settings) -> None:
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    session.commit()
    application = Application(job_id=job.id, status=ApplicationStatus.NEEDS_USER_INPUT)
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#resume",
        question="Resume upload",
        field_type="file",
        required=True,
    )
    session.add(field)
    session.commit()
    service = ApplicationService(session, test_settings, NoBrowser())  # type: ignore[arg-type]
    with pytest.raises(ApplicationStateError, match="cannot be resolved with text"):
        await service.update_field(field, "anything typed here")
    session.refresh(field)
    assert field.answer is None
