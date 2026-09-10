from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.api.dependencies import get_browser_manager
from app.automation.browser import BrowserManager
from app.automation.submission import SubmissionBlockedError
from app.config import Settings, get_settings
from app.database import get_session
from app.models import Application, ApplicationField, Job, Resume
from app.models.enums import ApplicationStatus
from app.schemas.application import (
    ApplicationFieldUpdate,
    ApplicationRead,
    ApplicationStart,
    ApplicationStatusUpdate,
    ApprovalResponse,
    SubmissionRecoveryRequest,
    SubmissionRequest,
)
from app.services.application_service import (
    ApplicationService,
    ApplicationStateError,
    DuplicateApplicationError,
    FieldUpdateError,
)

router = APIRouter(tags=["applications"])


def service(session: Session, settings: Settings, browser: BrowserManager) -> ApplicationService:
    return ApplicationService(session, settings, browser)


@router.get("/applications", response_model=list[ApplicationRead])
def list_applications(
    application_status: ApplicationStatus | None = Query(default=None, alias="status"),
    company: str | None = None,
    location: str | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    applications = session.exec(select(Application).order_by(Application.created_at.desc())).all()
    result = []
    app_service = service(session, settings, browser)
    for application in applications:
        item = app_service.read(application)
        if application_status and item.status != application_status:
            continue
        if company and company.casefold() not in item.company.casefold():
            continue
        if location and location.casefold() not in (item.location or "").casefold():
            continue
        result.append(item)
    return result


@router.post(
    "/jobs/{job_id}/applications",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_application(
    job_id: str,
    payload: ApplicationStart,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    resume = (
        session.get(Resume, payload.resume_id)
        if payload.resume_id
        else session.exec(
            select(Resume).order_by(Resume.is_default.desc(), Resume.created_at.desc())
        ).first()
    )
    if payload.resume_id and resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    app_service = service(session, settings, browser)
    try:
        application = await app_service.start(job, resume, payload.target_url)
        return app_service.read(application)
    except DuplicateApplicationError as error:
        raise HTTPException(
            status_code=409,
            detail={"message": str(error), "existing_application_id": error.existing_id},
        ) from error
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def get_application(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return service(session, settings, browser).read(application)


@router.post("/applications/{application_id}/continue", response_model=ApplicationRead)
async def continue_application(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        continued = await app_service.continue_after_user_action(application)
        return app_service.read(continued)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/applications/{application_id}/focus-browser",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def focus_application_browser(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
) -> None:
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        await service(session, settings, browser).focus_browser(application)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/applications/{application_id}/recheck-controls", response_model=ApplicationRead)
async def recheck_application_controls(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        checked = await app_service.recheck_form_action(application)
        return app_service.read(checked)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/applications/{application_id}/advance", response_model=ApplicationRead)
async def advance_application(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        advanced = await app_service.advance(application)
        return app_service.read(advanced)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.put("/applications/{application_id}/fields/{field_id}", response_model=ApplicationRead)
async def update_application_field(
    application_id: str,
    field_id: str,
    payload: ApplicationFieldUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    field = session.get(ApplicationField, field_id)
    if not field or field.application_id != application_id:
        raise HTTPException(status_code=404, detail="Field not found")
    app_service = service(session, settings, browser)
    try:
        application = await app_service.update_field(
            field,
            payload.answer,
            skip=payload.skip,
            remember=payload.remember,
            semantic_key=payload.semantic_key,
        )
        return app_service.read(application)
    except FieldUpdateError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "field_id": error.field_id,
                "error_code": error.code,
            },
        ) from error
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/applications/{application_id}/fields/{field_id}/focus",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def focus_application_field(
    application_id: str,
    field_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
) -> None:
    field = session.get(ApplicationField, field_id)
    if not field or field.application_id != application_id:
        raise HTTPException(status_code=404, detail="Field not found")
    try:
        await service(session, settings, browser).focus_field(field)
    except FieldUpdateError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "field_id": error.field_id,
                "error_code": error.code,
            },
        ) from error


@router.post(
    "/applications/{application_id}/fields/{field_id}/retry-ai",
    response_model=ApplicationRead,
)
async def retry_application_field_with_ai(
    application_id: str,
    field_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    field = session.get(ApplicationField, field_id)
    if not field or field.application_id != application_id:
        raise HTTPException(status_code=404, detail="Field not found")
    app_service = service(session, settings, browser)
    try:
        application = await app_service.retry_ai_answer(field)
        return app_service.read(application)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/applications/{application_id}/approve", response_model=ApprovalResponse)
async def approve_application(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        token, expires_at = await service(session, settings, browser).approve(application)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ApprovalResponse(
        application_id=application.id,
        status=application.status,
        approval_token=token,
        expires_at=expires_at,
    )


@router.post("/applications/{application_id}/submit", response_model=ApplicationRead)
async def submit_application(
    application_id: str,
    payload: SubmissionRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        submitted = await app_service.submit(application, payload.approval_token)
        return app_service.read(submitted)
    except SubmissionBlockedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post(
    "/applications/{application_id}/submission-recovery",
    response_model=ApplicationRead,
)
async def recover_unsubmitted_application(
    application_id: str,
    payload: SubmissionRecoveryRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        recovered = await app_service.recover_not_submitted(application)
        return app_service.read(recovered)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/applications/{application_id}/manual-submit",
    response_model=ApplicationRead,
)
async def prepare_manual_application_submission(
    application_id: str,
    payload: SubmissionRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        prepared = await app_service.prepare_manual_submission(
            application, payload.approval_token
        )
        return app_service.read(prepared)
    except (ApplicationStateError, SubmissionBlockedError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/applications/{application_id}/focus-submit",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def focus_application_submission_control(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
) -> None:
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        await service(session, settings, browser).focus_submission_control(application)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post(
    "/applications/{application_id}/archive-and-restart",
    response_model=ApplicationRead,
)
async def archive_and_restart_application(
    application_id: str,
    payload: SubmissionRecoveryRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        restarted = await app_service.archive_and_restart(application)
        return app_service.read(restarted)
    except DuplicateApplicationError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(error),
                "existing_application_id": error.existing_id,
            },
        ) from error
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.patch("/applications/{application_id}/status", response_model=ApplicationRead)
def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    app_service = service(session, settings, browser)
    try:
        updated = app_service.set_tracker_status(application, payload.status)
        return app_service.read(updated)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete(
    "/applications/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_application(
    application_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    browser: BrowserManager = Depends(get_browser_manager),
):
    application = session.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    try:
        await service(session, settings, browser).delete_application(application)
    except ApplicationStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
