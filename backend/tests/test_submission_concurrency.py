import asyncio

import pytest
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.automation.submission import (
    SubmissionBlockedError,
    SubmissionEvidence,
    issue_approval_token,
)
from app.models import Application, ApplicationEvent, Job
from app.models.enums import ApplicationStatus, EventType
from app.services.application_service import ApplicationService
from app.services.job_service import description_hash


class FakeBrowser:
    def __init__(self) -> None:
        self.fake_page = object()

    def page(self, key):
        return self.fake_page

    async def has_captcha(self, page) -> bool:
        return False

    async def has_login(self, page) -> bool:
        return False


@pytest.mark.asyncio
async def test_only_one_concurrent_request_can_consume_approval(
    tmp_path, test_settings, monkeypatch
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrency.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(engine)
    token, digest, expires_at = issue_approval_token(test_settings)
    with Session(engine) as setup:
        job = Job(
            company="Acme",
            title="Engineer",
            description="Build software",
            description_sha256=description_hash("Build software"),
        )
        setup.add(job)
        setup.commit()
        application = Application(
            job_id=job.id,
            status=ApplicationStatus.APPROVED,
            current_url="https://jobs.example/application",
            form_fingerprint="form",
            approval_token_hash=digest,
            approved_payload_hash="payload",
            approval_expires_at=expires_at,
        )
        setup.add(application)
        setup.commit()
        application_id = application.id

    both_validated = asyncio.Event()
    validation_count = 0

    async def validate(self, application, page, fields):
        nonlocal validation_count
        validation_count += 1
        if validation_count == 2:
            both_validated.set()
        await both_validated.wait()
        return object(), "payload"

    submit_calls = 0

    async def submit_once(page, form, *, approved):
        nonlocal submit_calls
        submit_calls += 1
        return SubmissionEvidence(True, "fixture", "https://jobs.example/success")

    monkeypatch.setattr(ApplicationService, "_validate_live_payload", validate)
    monkeypatch.setattr("app.services.application_service.guarded_submit", submit_once)

    session_one = Session(engine)
    session_two = Session(engine)
    try:
        application_one = session_one.get(Application, application_id)
        application_two = session_two.get(Application, application_id)
        assert application_one is not None and application_two is not None
        service_one = ApplicationService(session_one, test_settings, FakeBrowser())  # type: ignore[arg-type]
        service_two = ApplicationService(session_two, test_settings, FakeBrowser())  # type: ignore[arg-type]
        results = await asyncio.gather(
            service_one.submit(application_one, token),
            service_two.submit(application_two, token),
            return_exceptions=True,
        )
    finally:
        session_one.close()
        session_two.close()

    assert submit_calls == 1
    assert sum(isinstance(result, SubmissionBlockedError) for result in results) == 1
    with Session(engine) as verify:
        stored = verify.get(Application, application_id)
        assert stored is not None
        assert stored.status == ApplicationStatus.SUBMITTED
        assert stored.approval_token_hash is None


@pytest.mark.asyncio
async def test_native_validation_block_returns_to_review_without_claiming_an_attempt(
    session, test_settings, monkeypatch
) -> None:
    token, digest, expires_at = issue_approval_token(test_settings)
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.APPROVED,
        current_url="https://jobs.example/application",
        form_fingerprint="form",
        approval_token_hash=digest,
        approved_payload_hash="payload",
        approval_expires_at=expires_at,
    )
    session.add(application)
    session.commit()

    async def validate(self, application, page, fields):
        return object(), "payload"

    async def validation_block(page, form, *, approved):
        return SubmissionEvidence(
            False,
            "validation_failed",
            "https://jobs.example/application",
            attempted=False,
            validation_errors=("Date of birth",),
        )

    monkeypatch.setattr(ApplicationService, "_validate_live_payload", validate)
    monkeypatch.setattr("app.services.application_service.guarded_submit", validation_block)

    service = ApplicationService(session, test_settings, FakeBrowser())  # type: ignore[arg-type]
    result = await service.submit(application, token)

    assert result.status == ApplicationStatus.READY_FOR_REVIEW
    assert result.approval_token_hash is None
    assert result.approval_consumed_at is None
    assert "Date of birth" in (result.error_message or "")
    events = session.exec(
        select(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
    ).all()
    assert EventType.SUBMISSION_BLOCKED.value in {event.event_type for event in events}
