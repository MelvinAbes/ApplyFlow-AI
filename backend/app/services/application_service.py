import asyncio
import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from app.ai.field_interpreter import FieldInterpreter
from app.ai.question_answerer import QuestionAnswerer, is_open_ended_question
from app.ats import ATSRegistry
from app.ats.base import ATSAdapter
from app.automation.browser import BrowserManager, same_site
from app.automation.field_detector import (
    DetectedField,
    detect_fields,
    form_fingerprint,
    mark_application_form,
    rediscover_field,
)
from app.automation.field_mapper import FIELD_ALIASES
from app.automation.form_filler import (
    FileUploadError,
    FileUploadResult,
    FileUploadUserActionRequired,
    capture_field_state,
    clear_field,
    confirmed_existing_file_upload,
    fill_field,
    has_employer_consent_dialog,
    state_is_blank,
    state_matches_answer,
)
from app.automation.resolver import FieldResolver
from app.automation.submission import (
    SubmissionBlockedError,
    allow_manual_submission,
    ensure_submission_guard,
    focus_form_action_control,
    guarded_advance,
    guarded_submit,
    inspect_form_action,
    issue_approval_token,
    lock_submission_form,
    token_digest,
    verify_approval_token,
)
from app.config import Settings
from app.logging import log_event, sanitized_error
from app.models import (
    Application,
    ApplicationEvent,
    ApplicationField,
    Job,
    JobAnalysisRecord,
    Resume,
)
from app.models.entities import utc_now
from app.models.enums import AnswerSource, ApplicationStatus, EventType
from app.schemas.application import ApplicationRead
from app.services.answer_bank import AnswerBankService
from app.services.field_mapping import FieldMappingService
from app.services.screening import (
    is_potentially_disqualifying,
    requires_screening_verification,
)

TRACKABLE_USER_STATUSES = {
    ApplicationStatus.REJECTED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.WITHDRAWN,
    ApplicationStatus.ARCHIVED,
}

PROTECTED_APPLICATION_STATUSES = {
    ApplicationStatus.SUBMITTING,
    ApplicationStatus.SUBMISSION_UNCONFIRMED,
    ApplicationStatus.SUBMITTED,
    ApplicationStatus.REJECTED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.WITHDRAWN,
}

EDIT_LOCKED_STATUSES = PROTECTED_APPLICATION_STATUSES | {ApplicationStatus.APPROVED}

FORM_DISCOVERY_TIMEOUT_SECONDS = 10.0
FORM_DISCOVERY_POLL_SECONDS = 0.25


class ApplicationStateError(ValueError):
    pass


class FieldUpdateError(ApplicationStateError):
    def __init__(self, field_id: str, code: str, message: str):
        self.field_id = field_id
        self.code = code
        super().__init__(message)


class DuplicateApplicationError(ApplicationStateError):
    def __init__(self, existing_id: str):
        self.existing_id = existing_id
        super().__init__(f"An application already exists for this job: {existing_id}")


def availability_answer_error(field: ApplicationField, answer: str | None) -> str | None:
    if not answer or not _is_availability_field(field):
        return None
    parsed_dates = []
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            parsed_dates.append(datetime.strptime(answer.strip(), pattern).date())
        except ValueError:
            continue
    if parsed_dates and all(value < datetime.now().astimezone().date() for value in parsed_dates):
        return (
            "A preferred start date cannot be in the past. Choose Immediately, enter a truthful "
            "notice period, or provide a current or future date."
        )
    return None


def _is_availability_field(field: ApplicationField) -> bool:
    if field.field_key == "preferred_start_date":
        return True
    question = " ".join((field.translated_question or field.question).casefold().split())
    return any(
        marker in question
        for marker in ("start date", "notice period", "starttermin", "kündigungsfrist")
    )


class ApplicationService:
    def __init__(self, session: Session, settings: Settings, browser: BrowserManager):
        self.session = session
        self.settings = settings
        self.browser = browser

    async def start(
        self, job: Job, resume: Resume | None, target_url: str | None = None
    ) -> Application:
        existing = self.session.exec(
            select(Application)
            .where(Application.job_id == job.id)
            .where(Application.archived_at.is_(None))
            .limit(1)
        ).first()
        if existing and existing.status != ApplicationStatus.FAILED:
            raise DuplicateApplicationError(existing.id)
        url = target_url or job.application_url or job.source_url
        if not url:
            raise ApplicationStateError("No application URL is available")
        if existing:
            application = existing
            self.session.exec(
                delete(ApplicationField).where(ApplicationField.application_id == application.id)
            )
            application.resume_id = resume.id if resume else None
            application.status = ApplicationStatus.FORM_LOADING
            application.current_step = 1
            application.current_url = url
            application.ats_adapter = None
            application.form_fingerprint = None
            application.form_action = None
            application.waiting_reason = None
            application.error_message = None
            self._invalidate_approval(application)
        else:
            application = Application(
                job_id=job.id,
                resume_id=resume.id if resume else None,
                status=ApplicationStatus.FORM_LOADING,
                current_url=url,
            )
        self.session.add(application)
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            duplicate = self.session.exec(
                select(Application)
                .where(Application.job_id == job.id)
                .where(Application.archived_at.is_(None))
                .limit(1)
            ).first()
            if duplicate:
                raise DuplicateApplicationError(duplicate.id) from error
            raise
        self.session.refresh(application)
        self._event(application, EventType.APPLICATION_STARTED, {"host": self._host(url)})
        log_event(EventType.APPLICATION_STARTED, entity_id=application.id)

        page: Page | None = None
        failure_stage = "opening_employer_page"
        try:
            page = await self.browser.open(application.id, url)
            application.current_url = page.url
            failure_stage = "checking_for_login"
            if await self.browser.has_login(page):
                return self._wait_for_user(
                    application,
                    "login",
                    "Sign in or create the employer account in the visible application window. "
                    "The assistant never reads or stores the password.",
                )
            failure_stage = "checking_for_captcha"
            if await self.browser.has_captcha(page):
                return self._wait_for_user(
                    application, "captcha", "Manual CAPTCHA completion required."
                )
            failure_stage = "locating_and_scanning_form"
            return await self._process_page(
                application, job, resume, page, reuse_existing_tabs=False
            )
        except PlaywrightTimeoutError as error:
            recovery_page = self.browser.page(application.id) or page
            if recovery_page is not None:
                log_event(
                    "FORM_SCAN_RECOVERY_STARTED",
                    entity_id=application.id,
                    failure_stage=failure_stage,
                )
                try:
                    return await self._process_page(
                        application,
                        job,
                        resume,
                        recovery_page,
                        reuse_existing_tabs=True,
                    )
                except PlaywrightTimeoutError:
                    application.current_url = recovery_page.url
                    return self._wait_for_user(
                        application,
                        "browser_timeout",
                        "The employer page took too long to finish navigating. If the application "
                        "form is visible, leave it open and continue so the assistant can scan it.",
                    )
                except Exception as recovery_error:
                    return await self._fail_start(
                        application,
                        recovery_page,
                        recovery_error,
                        failure_stage="timeout_recovery",
                    )
            return await self._fail_start(
                application, page, error, failure_stage=failure_stage
            )
        except Exception as error:
            return await self._fail_start(
                application, page, error, failure_stage=failure_stage
            )

    async def _fail_start(
        self,
        application: Application,
        page: Page | None,
        error: Exception,
        *,
        failure_stage: str,
    ) -> Application:
        if page:
            try:
                await self.browser.capture_failure(page, application.id)
            except Exception:
                pass
        application.status = ApplicationStatus.FAILED
        application.error_message = sanitized_error(error)
        self._save(application)
        details = {
            "error_type": type(error).__name__,
            "failure_stage": failure_stage,
        }
        self._event(application, EventType.APPLICATION_FAILED, details)
        log_event(EventType.APPLICATION_FAILED, entity_id=application.id, **details)
        return application

    async def continue_after_user_action(self, application: Application) -> Application:
        if application.status != ApplicationStatus.WAITING_FOR_USER:
            raise ApplicationStateError("Application is not waiting for a manual browser action")
        job = self.session.get(Job, application.job_id)
        if not job:
            raise ApplicationStateError("Job not found")
        page = self.browser.page(application.id)
        if page is None:
            if not application.current_url:
                raise ApplicationStateError("Application page cannot be reopened")
            page = await self.browser.open(application.id, application.current_url)

        if application.waiting_reason in {"captcha", "login"}:
            completed_page = await self._find_completed_access_page(page, job.title)
            if completed_page is not None:
                await self.browser.adopt(application.id, completed_page)
                page = completed_page

            # A sign-in page can legitimately contain a CAPTCHA. Treat the complete page as
            # one account-access step instead of asking the user to solve an apparently
            # unrelated CAPTCHA while the credentials form is still present.
            if await self.browser.has_login(page):
                raise ApplicationStateError(
                    "Sign-in or employer account setup is not complete yet"
                )
            if await self.browser.has_captcha(page):
                raise ApplicationStateError("CAPTCHA is still present")

        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        if application.waiting_reason == "employer_consent":
            if await has_employer_consent_dialog(page):
                raise ApplicationStateError(
                    "The employer privacy or data-use notice still requires your decision"
                )
            resume_field = next(
                (
                    field
                    for field in self._fields(application.id, active_only=True)
                    if field.field_type == "file" and field.field_key == "resume"
                ),
                None,
            )
            if resume is None or resume_field is None:
                raise ApplicationStateError(
                    "The résumé upload cannot be reconciled with this application draft"
                )
            try:
                upload_result = await confirmed_existing_file_upload(
                    page,
                    self._detected_from_stored(resume_field),
                    resume.filename,
                    wait_for_presence=True,
                )
            except FileUploadError as error:
                raise ApplicationStateError(
                    "The employer is still processing the résumé upload"
                ) from error
            if upload_result is None:
                raise ApplicationStateError(
                    "The employer has not confirmed the résumé upload yet"
                )
            log_event(
                "FILE_UPLOAD_CONFIRMED",
                entity_id=application.id,
                strategy=upload_result.strategy,
                confirmation_signal=upload_result.confirmation_signal,
                **upload_result.safe_details,
            )
        self.session.exec(
            delete(ApplicationField).where(
                ApplicationField.application_id == application.id,
                ApplicationField.active.is_(True),
            )
        )
        self.session.commit()
        application.error_message = None
        application.waiting_reason = None
        application.current_url = page.url
        self._invalidate_approval(application)
        return await self._process_page(application, job, resume, page)

    async def recheck_form_action(self, application: Application) -> Application:
        """Re-inspect the live form action without clicking or rebuilding reviewed fields."""

        if (
            application.status != ApplicationStatus.WAITING_FOR_USER
            or application.waiting_reason != "ambiguous_form_action"
        ):
            raise ApplicationStateError("The application is not waiting for a form-control check")
        page = self.browser.page(application.id)
        if page is None:
            page = await self._restore_page(application)
        located = await self._locate_application_form(page, allow_navigation=False)
        if located is None:
            raise ApplicationStateError("The application form is no longer present")
        page, _, form = located
        await lock_submission_form(page, form)
        await self._reconcile_live_fields(application, page)
        inspection = await inspect_form_action(form)
        application.form_action = inspection.action
        application.current_url = page.url
        self._refresh_readiness(application)
        if application.status == ApplicationStatus.WAITING_FOR_USER:
            application.error_message = inspection.waiting_message()
        self._invalidate_approval(application)
        application.updated_at = utc_now()
        self._save(application)
        log_event(
            "FORM_ACTION_RECHECKED",
            entity_id=application.id,
            action=inspection.action,
            reason=inspection.reason,
            candidate_count=len(inspection.labels),
        )
        return application

    async def _process_page(
        self,
        application: Application,
        job: Job,
        resume: Resume | None,
        page: Page,
        *,
        reuse_existing_tabs: bool = True,
    ) -> Application:
        located = await self._locate_application_form(
            page,
            allow_navigation=True,
            application_key=application.id,
            expected_title=job.title,
            reuse_existing_tabs=reuse_existing_tabs,
        )
        if located is None:
            gate_reference = self.browser.page(application.id) or page
            access_gate = await self._find_access_gate_page(gate_reference)
            if access_gate is not None:
                gate_page, reason = access_gate
                await self.browser.adopt(application.id, gate_page)
                application.current_url = gate_page.url
                if reason == "captcha":
                    return self._wait_for_user(
                        application, "captcha", "Manual CAPTCHA completion required."
                    )
                return self._wait_for_user(
                    application,
                    "login",
                    "Sign in or create the employer account in the visible application window. "
                    "Use your own credentials; the assistant never reads or stores the password. "
                    "The portal session remains in the local application browser for later use.",
                )
            return self._wait_for_user(
                application,
                "form_not_found",
                "No application form was detected. If it did not open automatically, navigate to the form in the visible browser, but do not fill it manually; then continue.",
            )
        page, adapter, form = located
        application.current_url = page.url
        if await self.browser.has_captcha(page):
            return self._wait_for_user(
                application, "captcha", "Manual CAPTCHA completion required."
            )
        if await self.browser.has_login(page):
            return self._wait_for_user(
                application,
                "login",
                "Sign in or create the employer account in the visible application window. "
                "The assistant never reads or stores the password.",
            )
        log_event("FORM_SCAN_STARTED", entity_id=application.id, host=self._host(page.url))
        async with self.browser.protect_from_manual_input(page):
            fields = await detect_fields(page, form)
            if not fields:
                return self._wait_for_user(
                    application,
                    "form_empty",
                    "The selected application form has no supported fields. Complete navigation manually, then continue.",
                )
            fields = await FieldInterpreter(self.session, self.settings).interpret(fields)
            application.status = ApplicationStatus.FORM_ANALYZED
            application.current_url = page.url
            application.ats_adapter = type(adapter).__name__
            application.form_fingerprint = await form_fingerprint(page, form, fields)
            application.form_action = None
            application.waiting_reason = None
            application.error_message = None
            self._invalidate_approval(application)
            self._save(application)

            resolver = FieldResolver(self.session, self.settings)
            missing = False
            for detected in fields:
                resolved = await resolver.resolve(detected, job, resume)
                field = ApplicationField(
                    application_id=application.id,
                    step_index=application.current_step,
                    active=True,
                    field_key=resolved.field_key,
                    selector=detected.selector,
                    question=detected.question,
                    translated_question=detected.translated_question,
                    source_language=detected.source_language,
                    field_type=detected.field_type,
                    answer=resolved.answer,
                    source=resolved.source,
                    confidence=resolved.confidence,
                    required=detected.required,
                    uncertain=resolved.answer is None or resolved.confidence < 0.75,
                    requires_verification=requires_screening_verification(
                        detected.translated_question or detected.question
                    ),
                    disqualifying=is_potentially_disqualifying(
                        detected.translated_question or detected.question, resolved.answer
                    ),
                    character_limit=detected.character_limit,
                    options=detected.options,
                    translated_options=detected.translated_options,
                    translation_confidence=detected.translation_confidence,
                    skipped=False,
                    resolution_message=resolved.resolution_message,
                    ai_retryable=resolved.ai_retryable,
                )
                self.session.add(field)
                self._event(
                    application,
                    EventType.FIELD_DETECTED,
                    {"field_type": detected.field_type, "resolved": resolved.answer is not None},
                )
                if resolved.answer is None:
                    missing = True
                    self._event(
                        application,
                        EventType.USER_INPUT_REQUIRED,
                        {"field_type": detected.field_type},
                    )
                    continue
                try:
                    fill_result = await fill_field(
                        page,
                        detected,
                        resolved.answer,
                        upload_path=resolved.upload_path,
                        upload_name=resolved.upload_name,
                    )
                except Exception as error:
                    field.selector = detected.selector
                    field.answer = None
                    field.source = AnswerSource.UNKNOWN
                    field.confidence = 0.0
                    field.uncertain = True
                    if resolved.source == AnswerSource.LLM_GENERATED:
                        field.resolution_message = (
                            "AI generated an answer, but the browser field could not be filled. "
                            "Try AI again or enter the answer manually."
                        )
                        field.ai_retryable = True
                    elif detected.field_type == "file" and resolved.upload_path is not None:
                        if isinstance(error, FileUploadUserActionRequired):
                            field.resolution_message = (
                                "The résumé is selected. Waiting for your privacy or data-use "
                                "decision in the employer window."
                            )
                        else:
                            field.resolution_message = (
                                "The selected résumé is stored locally, but the employer upload "
                                "did not complete."
                            )
                        upload_details: dict[str, object] = {
                            "error_type": type(error).__name__,
                            "failure_code": "unexpected_error",
                        }
                        if isinstance(error, FileUploadError):
                            upload_details["failure_code"] = error.code
                            upload_details.update(error.safe_details)
                        log_event(
                            "FILE_UPLOAD_WAITING_FOR_USER"
                            if isinstance(error, FileUploadUserActionRequired)
                            else "FILE_UPLOAD_FAILED",
                            entity_id=application.id,
                            **upload_details,
                        )
                    self.session.add(field)
                    if isinstance(error, FileUploadUserActionRequired):
                        return self._wait_for_user(
                            application,
                            "employer_consent",
                            "The employer requires you to review and accept or decline a privacy "
                            "or data-use notice before the résumé upload can finish. Complete "
                            "that decision in the visible browser, then continue. The assistant "
                            "will not accept it for you.",
                        )
                    missing = True
                    self._event(
                        application,
                        EventType.USER_INPUT_REQUIRED,
                        {"field_type": detected.field_type, "reason": type(error).__name__},
                    )
                    continue
                field.selector = detected.selector
                self._event(
                    application,
                    EventType.FIELD_FILLED,
                    {"source": resolved.source.value, "field_type": detected.field_type},
                )
                if isinstance(fill_result, FileUploadResult):
                    log_event(
                        "FILE_UPLOAD_CONFIRMED",
                        entity_id=application.id,
                        strategy=fill_result.strategy,
                        confirmation_signal=fill_result.confirmation_signal,
                        **fill_result.safe_details,
                    )
                if resolved.source == AnswerSource.LLM_GENERATED:
                    self._event(application, EventType.AI_ANSWER_GENERATED, {})
            await adapter.fill_application(page, fields)
            self.session.flush()
            await self._reconcile_live_fields(application, page)
            action_inspection = await inspect_form_action(form)
            application.form_action = action_inspection.action
            missing = any(
                not self._field_is_resolved(item)
                for item in self._fields(application.id, active_only=True)
            )
        if missing:
            application.status = ApplicationStatus.NEEDS_USER_INPUT
        elif application.form_action == "final":
            application.status = ApplicationStatus.READY_FOR_REVIEW
        elif application.form_action == "continue":
            application.status = ApplicationStatus.READY_TO_ADVANCE
        else:
            application.status = ApplicationStatus.WAITING_FOR_USER
            application.waiting_reason = "ambiguous_form_action"
            application.error_message = action_inspection.waiting_message()
        application.updated_at = utc_now()
        self.session.add(application)
        self.session.commit()
        self.session.refresh(application)
        log_event(
            "FORM_SCAN_COMPLETED",
            entity_id=application.id,
            field_count=len(fields),
            unresolved_count=sum(
                1 for field in self._fields(application.id, active_only=True) if not field.answer
            ),
            status=application.status.value,
        )
        return application

    async def update_field(
        self,
        field: ApplicationField,
        answer: str | None,
        *,
        skip: bool = False,
        remember: bool = False,
        semantic_key: str | None = None,
    ) -> Application:
        application = self.session.get(Application, field.application_id)
        if not application:
            raise ApplicationStateError("Application not found")
        if application.status in EDIT_LOCKED_STATUSES:
            raise ApplicationStateError("Approved or submitted applications cannot be edited")
        if not field.active:
            raise ApplicationStateError(
                "A completed application step is read-only. Restart the application to change it."
            )
        corrected_semantic_key = semantic_key.strip() if semantic_key is not None else None
        if corrected_semantic_key and corrected_semantic_key not in FIELD_ALIASES:
            raise ApplicationStateError("Choose a supported field meaning")
        if skip and field.required:
            raise ApplicationStateError("Required fields cannot be left blank")
        if not skip and (answer is None or not answer.strip()):
            raise ApplicationStateError("Enter an answer or explicitly leave the field blank")
        if not skip:
            availability_error = availability_answer_error(field, answer)
            if availability_error:
                raise ApplicationStateError(availability_error)
        if not skip and field.field_type == "file":
            raise ApplicationStateError(
                "File fields cannot be resolved with text. Select a stored resume when starting the application."
            )
        if not skip and field.field_type in {"select", "radio", "combobox"} and field.options:
            assert answer is not None
            normalized_answer = " ".join(answer.split()).casefold()
            matching_option = next(
                (
                    option
                    for option in field.options
                    if " ".join(option.split()).casefold() == normalized_answer
                ),
                None,
            )
            if matching_option is None:
                raise ApplicationStateError("Choose one of the available options")
            answer = matching_option
        if answer and field.character_limit and len(answer) > field.character_limit:
            raise ApplicationStateError(f"Answer exceeds {field.character_limit} characters")

        detected = self._detected_from_stored(field)
        page = self.browser.page(application.id)
        if page is None and application.current_url:
            raise FieldUpdateError(
                field.id,
                "employer_window_closed",
                "The live employer application is no longer connected. Restart this draft "
                "before saving more answers.",
            )
        if page:
            await ensure_submission_guard(page)
            try:
                if skip:
                    await clear_field(page, detected)
                else:
                    assert answer is not None
                    await fill_field(page, detected, answer)
                field.selector = detected.selector
            except ValueError as error:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                message = str(error)
                if field.field_type in {"select", "radio", "combobox"} and "not present" in message:
                    message = (
                        "The selected option is no longer available in the live employer field. "
                        "Open that field, confirm its current choices, and try again."
                    )
                elif "did not retain" in message or "did not remain" in message:
                    message = (
                        "The employer form did not keep this value. The field may have refreshed "
                        "or rejected it; open the live field and try again."
                    )
                log_event(
                    "FIELD_UPDATE_FAILED",
                    entity_id=application.id,
                    field_id=field.id,
                    field_type=field.field_type,
                    error_code="employer_rejected_answer",
                    error_type=type(error).__name__,
                )
                raise FieldUpdateError(
                    field.id,
                    "employer_rejected_answer",
                    message,
                ) from error
            except Exception as error:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                if isinstance(error, PlaywrightTimeoutError):
                    code = "employer_control_timeout"
                    message = "The employer field did not respond in time."
                elif isinstance(error, PlaywrightError):
                    code = "employer_control_changed"
                    message = "The employer field changed or closed while it was being updated."
                else:
                    code = "employer_control_error"
                    message = "The employer field did not accept this answer."
                log_event(
                    "FIELD_UPDATE_FAILED",
                    entity_id=application.id,
                    field_id=field.id,
                    field_type=field.field_type,
                    error_code=code,
                    error_type=type(error).__name__,
                )
                raise FieldUpdateError(field.id, code, message) from error
        if semantic_key is not None:
            field.field_key = corrected_semantic_key or None
        field.answer = None if skip else answer
        field.skipped = skip
        field.source = AnswerSource.USER_ENTERED
        field.confidence = 1.0
        field.uncertain = False
        semantic_question = field.translated_question or field.question
        field.requires_verification = requires_screening_verification(semantic_question)
        field.disqualifying = is_potentially_disqualifying(semantic_question, field.answer)
        field.resolution_message = None
        field.ai_retryable = False
        field.updated_at = utc_now()
        self.session.add(field)
        self.session.flush()
        if page:
            await self._reconcile_live_fields(application, page)
        if semantic_key is not None and field.field_key:
            mapping_memory = FieldMappingService(self.session)
            mapping_memory.remember(label=field.question, canonical_key=field.field_key)
            if field.translated_question and field.translated_question != field.question:
                mapping_memory.remember(
                    label=field.translated_question,
                    canonical_key=field.field_key,
                )
        if remember and field.answer:
            AnswerBankService(self.session).remember(
                question=field.translated_question or field.question,
                answer=field.answer,
                canonical_key=field.field_key,
                aliases=[field.question] if field.translated_question else [],
            )
        self._refresh_readiness(application)
        self._invalidate_approval(application)
        application.updated_at = utc_now()
        self.session.add(application)
        self.session.commit()
        self.session.refresh(application)
        return application

    async def focus_field(self, field: ApplicationField) -> None:
        application = self.session.get(Application, field.application_id)
        if not application:
            raise ApplicationStateError("Application not found")
        page = self.browser.page(application.id)
        if page is None:
            raise FieldUpdateError(
                field.id,
                "employer_window_closed",
                "The employer application window is no longer open.",
            )
        locator = page.locator(field.selector)
        if not await locator.count():
            try:
                live = await rediscover_field(page, self._detected_from_stored(field))
                field.selector = live.selector
                self.session.add(field)
                self.session.commit()
                locator = page.locator(field.selector)
            except ValueError:
                pass
        if not await locator.count():
            raise FieldUpdateError(
                field.id,
                "employer_field_missing",
                "This field is no longer present in the employer application.",
            )
        try:
            await page.bring_to_front()
            target = locator.first
            await target.scroll_into_view_if_needed()
            await target.focus()
            await target.evaluate(
                "element => element.scrollIntoView({block: 'center', inline: 'nearest'})"
            )
        except PlaywrightError as error:
            raise FieldUpdateError(
                field.id,
                "employer_field_unavailable",
                "The employer field could not be focused. It may have changed or closed.",
            ) from error

    async def focus_browser(self, application: Application) -> None:
        """Bring the managed employer window forward without exposing its session."""

        page = self.browser.page(application.id)
        if page is None:
            if not application.current_url:
                raise ApplicationStateError("The employer application window cannot be reopened")
            page = await self.browser.open(application.id, application.current_url)
        await page.bring_to_front()

    async def retry_ai_answer(self, field: ApplicationField) -> Application:
        application = self.session.get(Application, field.application_id)
        if not application:
            raise ApplicationStateError("Application not found")
        if application.status in EDIT_LOCKED_STATUSES:
            raise ApplicationStateError("Approved or submitted applications cannot be edited")
        if not field.active:
            raise ApplicationStateError("A completed application step is read-only")
        if field.answer or field.skipped:
            raise ApplicationStateError("AI retry is available only for unresolved fields")
        semantic_question = field.translated_question or field.question
        if not is_open_ended_question(semantic_question):
            raise ApplicationStateError("AI can retry only open-ended application questions")

        job = self.session.get(Job, application.job_id)
        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        if job is None:
            raise ApplicationStateError("Job not found")
        page = self.browser.page(application.id)
        if page is None and application.current_url:
            raise FieldUpdateError(
                field.id,
                "employer_window_closed",
                "The live employer application is no longer connected. Restart this draft "
                "before generating another answer.",
            )
        attempt = await QuestionAnswerer(self.session, self.settings).attempt(
            semantic_question,
            job,
            resume,
            field.character_limit,
        )
        if attempt.answer.status != "ANSWERED" or not attempt.answer.answer:
            field.resolution_message = attempt.resolution_message
            field.ai_retryable = attempt.retryable
            field.updated_at = utc_now()
            application.status = ApplicationStatus.NEEDS_USER_INPUT
            self._invalidate_approval(application)
            self.session.add(field)
            self._save(application)
            return application

        detected = self._detected_from_stored(field)
        if page:
            await ensure_submission_guard(page)
            await fill_field(page, detected, attempt.answer.answer)
            field.selector = detected.selector
        field.answer = attempt.answer.answer
        field.skipped = False
        field.source = AnswerSource.LLM_GENERATED
        field.confidence = attempt.answer.confidence
        field.uncertain = attempt.answer.confidence < 0.75
        field.requires_verification = requires_screening_verification(semantic_question)
        field.disqualifying = is_potentially_disqualifying(semantic_question, attempt.answer.answer)
        field.resolution_message = None
        field.ai_retryable = False
        field.updated_at = utc_now()
        self.session.add(field)
        self.session.flush()
        if page:
            await self._reconcile_live_fields(application, page)
        self._refresh_readiness(application)
        self._invalidate_approval(application)
        self._save(application)
        self._event(application, EventType.AI_ANSWER_GENERATED, {"retry": True})
        return application

    async def advance(self, application: Application) -> Application:
        if application.status != ApplicationStatus.READY_TO_ADVANCE:
            raise ApplicationStateError("The application is not ready to advance to another step")
        active_fields = self._fields(application.id, active_only=True)
        if not active_fields or any(not self._field_is_resolved(field) for field in active_fields):
            raise ApplicationStateError("Resolve the current step before continuing")
        page = self.browser.page(application.id)
        if page is None:
            page = await self._restore_page(application)
        form, _ = await self._validate_live_payload(application, page, active_fields)
        try:
            await guarded_advance(page, form)
        except Exception as error:
            try:
                await self.browser.capture_failure(page, application.id)
            except Exception:
                pass
            raise ApplicationStateError(
                "The application could not advance to the next step"
            ) from error

        for field in active_fields:
            field.active = False
            self.session.add(field)
        application.current_step += 1
        application.status = ApplicationStatus.FORM_LOADING
        application.current_url = page.url
        application.form_fingerprint = None
        application.form_action = None
        application.waiting_reason = None
        application.error_message = None
        self._invalidate_approval(application)
        self._save(application)
        job = self.session.get(Job, application.job_id)
        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        if job is None:
            raise ApplicationStateError("Job not found")
        return await self._process_page(application, job, resume, page)

    async def approve(self, application: Application) -> tuple[str, datetime]:
        if application.status not in {
            ApplicationStatus.READY_FOR_REVIEW,
            ApplicationStatus.APPROVED,
        }:
            raise ApplicationStateError("Application is not ready for review")
        fields = self._fields(application.id)
        active_fields = self._fields(application.id, active_only=True)
        if not fields or any(not self._field_is_resolved(field) for field in fields):
            raise ApplicationStateError("Every detected field must be resolved before approval")
        if application.form_action != "final" or not active_fields:
            raise ApplicationStateError(
                "The current form step is not an unambiguous final submission step"
            )
        page = self.browser.page(application.id)
        if page is None:
            page = await self._restore_page(application)
        _, payload_hash = await self._validate_live_payload(application, page, active_fields)
        token, digest, expires_at = issue_approval_token(self.settings)
        application.status = ApplicationStatus.APPROVED
        application.approval_token_hash = digest
        application.approved_payload_hash = payload_hash
        application.approval_expires_at = expires_at
        application.approved_at = utc_now()
        application.approval_consumed_at = None
        application.error_message = None
        application.waiting_reason = None
        application.updated_at = utc_now()
        self._save(application)
        self._event(application, EventType.APPLICATION_REVIEWED, {"field_count": len(fields)})
        return token, expires_at

    async def submit(self, application: Application, token: str) -> Application:
        if application.status != ApplicationStatus.APPROVED:
            raise SubmissionBlockedError("Application has not been explicitly approved")
        if not verify_approval_token(
            token, application.approval_token_hash, application.approval_expires_at
        ):
            raise SubmissionBlockedError("Approval token is invalid or expired")

        page = self.browser.page(application.id)
        try:
            if page is None:
                page = await self._restore_page(application)
        except ApplicationStateError as error:
            self._invalidate_approval(application)
            application.status = ApplicationStatus.READY_FOR_REVIEW
            application.error_message = str(error)
            self._save(application)
            raise SubmissionBlockedError(str(error)) from error
        if await self.browser.has_captcha(page):
            self._invalidate_approval(application)
            return self._wait_for_user(
                application, "captcha", "Manual CAPTCHA completion required."
            )
        if await self.browser.has_login(page):
            self._invalidate_approval(application)
            return self._wait_for_user(
                application,
                "login",
                "Manual login required before submission. Approval has been invalidated.",
            )

        fields = self._fields(application.id, active_only=True)
        try:
            form, payload_hash = await self._validate_live_payload(application, page, fields)
        except ApplicationStateError as error:
            self._invalidate_approval(application)
            application.status = ApplicationStatus.READY_FOR_REVIEW
            application.error_message = str(error)
            self._save(application)
            raise SubmissionBlockedError(str(error)) from error
        if not application.approved_payload_hash or not secrets_compare(
            payload_hash, application.approved_payload_hash
        ):
            self._invalidate_approval(application)
            application.status = ApplicationStatus.READY_FOR_REVIEW
            application.error_message = (
                "The live form changed after approval. Review the current values and approve again."
            )
            self._save(application)
            raise SubmissionBlockedError(application.error_message)

        application = self._consume_approval(application, token, payload_hash)

        try:
            evidence = await guarded_submit(page, form, approved=True)
            application.current_url = evidence.final_url
            if evidence.confirmed:
                application.status = ApplicationStatus.SUBMITTED
                application.submitted_at = utc_now()
                application.error_message = None
                application.waiting_reason = None
                self._save(application)
                self._event(
                    application,
                    EventType.APPLICATION_SUBMITTED,
                    {"confirmation_signal": evidence.signal},
                )
                log_event(EventType.APPLICATION_SUBMITTED, entity_id=application.id)
            elif not evidence.attempted:
                invalid_fields = ", ".join(evidence.validation_errors[:3])
                self._invalidate_approval(application)
                application.status = ApplicationStatus.READY_FOR_REVIEW
                application.waiting_reason = None
                application.error_message = (
                    "The employer blocked submission before anything was sent because "
                    f"{invalid_fields or 'a required field'} is invalid. Correct the highlighted "
                    "employer field, review the live values, and approve again."
                )
                self._save(application)
                self._event(
                    application,
                    EventType.SUBMISSION_BLOCKED,
                    {"invalid_field_count": len(evidence.validation_errors)},
                )
            else:
                application.status = ApplicationStatus.SUBMISSION_UNCONFIRMED
                application.error_message = (
                    "The submit action ran, but employer confirmation was not detected. "
                    "Check the visible browser before recording the application as submitted; do not retry."
                )
                self._save(application)
                self._event(application, EventType.SUBMISSION_UNCONFIRMED, {})
            return application
        except Exception as error:
            try:
                await self.browser.capture_failure(page, application.id)
            except Exception:
                pass
            application.status = ApplicationStatus.SUBMISSION_UNCONFIRMED
            application.error_message = f"{sanitized_error(error)} The approval was consumed; verify the employer page before retrying."
            self._save(application)
            self._event(
                application,
                EventType.SUBMISSION_UNCONFIRMED,
                {"error_type": type(error).__name__},
            )
            return application

    async def recover_not_submitted(self, application: Application) -> Application:
        """Return an uncertain submission to review after an explicit user assertion.

        This transition never retries submission. It restores or reuses the selected form,
        reconciles its live values, invalidates the consumed approval, and requires a fresh
        review plus approval before any later automated or manual attempt.
        """

        if application.status != ApplicationStatus.SUBMISSION_UNCONFIRMED:
            raise ApplicationStateError("Only an unconfirmed submission can return to review")
        page = self.browser.page(application.id)
        if page is None:
            page = await self._restore_page(application)
        located = await self._locate_application_form(page, allow_navigation=False)
        if located is None:
            raise ApplicationStateError("The employer application form is no longer present")
        page, _, form = located
        await lock_submission_form(page, form)
        await self._reconcile_live_fields(application, page)
        inspection = await inspect_form_action(form)
        application.form_action = inspection.action
        application.current_url = page.url
        self._invalidate_approval(application)
        self._refresh_readiness(application)
        if application.status == ApplicationStatus.WAITING_FOR_USER:
            application.error_message = inspection.waiting_message()
        self._save(application)
        self._event(application, EventType.SUBMISSION_RETRY_AUTHORIZED, {})
        return application

    async def prepare_manual_submission(
        self,
        application: Application,
        token: str,
    ) -> Application:
        """Consume a fresh approval and make the reviewed form manually available."""

        if application.status != ApplicationStatus.APPROVED:
            raise SubmissionBlockedError("Application has not been explicitly approved")
        if not verify_approval_token(
            token, application.approval_token_hash, application.approval_expires_at
        ):
            raise SubmissionBlockedError("Approval token is invalid or expired")
        page = self.browser.page(application.id)
        if page is None:
            page = await self._restore_page(application)
        if await self.browser.has_captcha(page):
            self._invalidate_approval(application)
            return self._wait_for_user(
                application, "captcha", "Manual CAPTCHA completion required."
            )
        if await self.browser.has_login(page):
            self._invalidate_approval(application)
            return self._wait_for_user(
                application,
                "login",
                "Manual login required before submission. Approval has been invalidated.",
            )
        fields = self._fields(application.id, active_only=True)
        try:
            form, payload_hash = await self._validate_live_payload(application, page, fields)
        except ApplicationStateError as error:
            self._invalidate_approval(application)
            application.status = ApplicationStatus.READY_FOR_REVIEW
            application.error_message = str(error)
            self._save(application)
            raise SubmissionBlockedError(str(error)) from error
        if not application.approved_payload_hash or not secrets_compare(
            payload_hash, application.approved_payload_hash
        ):
            self._invalidate_approval(application)
            application.status = ApplicationStatus.READY_FOR_REVIEW
            application.error_message = (
                "The live form changed after approval. Review the current values and approve again."
            )
            self._save(application)
            raise SubmissionBlockedError(application.error_message)

        await focus_form_action_control(page, form, "final")
        application = self._consume_approval(application, token, payload_hash)
        try:
            await allow_manual_submission(page, form)
        except Exception as error:
            application.status = ApplicationStatus.SUBMISSION_UNCONFIRMED
            application.waiting_reason = None
            application.error_message = (
                f"{sanitized_error(error)} The approval was consumed, but manual submission "
                "could not be enabled. Verify the employer page before taking another action."
            )
            self._save(application)
            self._event(
                application,
                EventType.SUBMISSION_UNCONFIRMED,
                {"error_type": type(error).__name__},
            )
            return application
        application.status = ApplicationStatus.SUBMISSION_UNCONFIRMED
        application.waiting_reason = "manual_submission"
        application.error_message = (
            "The reviewed employer form is ready for manual submission. Submit in the employer "
            "window, then record it only after the employer confirms receipt."
        )
        application.current_url = page.url
        self._save(application)
        self._event(application, EventType.MANUAL_SUBMISSION_ARMED, {})
        return application

    async def focus_submission_control(self, application: Application) -> None:
        if application.status != ApplicationStatus.SUBMISSION_UNCONFIRMED:
            raise ApplicationStateError("The application is not awaiting submission verification")
        page = self.browser.page(application.id)
        if page is None:
            raise ApplicationStateError(
                "The employer window is no longer connected. Return to review before retrying."
            )
        located = await self._locate_application_form(page, allow_navigation=False)
        if located is None:
            raise ApplicationStateError("The employer application form is no longer present")
        page, _, form = located
        await focus_form_action_control(page, form, "final")

    def _consume_approval(
        self,
        application: Application,
        token: str,
        payload_hash: str,
    ) -> Application:
        consumed_at = utc_now()
        statement = (
            update(Application)
            .where(
                Application.id == application.id,
                Application.status == ApplicationStatus.APPROVED,
                Application.approval_token_hash == token_digest(token),
                Application.approved_payload_hash == payload_hash,
                Application.approval_expires_at > consumed_at,
            )
            .values(
                status=ApplicationStatus.SUBMITTING,
                approval_token_hash=None,
                approval_expires_at=None,
                approval_consumed_at=consumed_at,
                updated_at=consumed_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(statement)
        if result.rowcount != 1:
            self.session.rollback()
            raise SubmissionBlockedError("Approval was already consumed or the application changed")
        self.session.commit()
        self.session.expire_all()
        consumed = self.session.get(Application, application.id)
        if consumed is None:
            raise ApplicationStateError("Application disappeared during submission")
        return consumed

    def set_tracker_status(
        self, application: Application, status: ApplicationStatus
    ) -> Application:
        if status == ApplicationStatus.SUBMITTED:
            if application.status != ApplicationStatus.SUBMISSION_UNCONFIRMED:
                raise ApplicationStateError(
                    "Only an unconfirmed submission can be manually confirmed as submitted"
                )
            application.submitted_at = utc_now()
            application.error_message = None
            application.waiting_reason = None
            application.status = status
            self._save(application)
            self._event(
                application,
                EventType.APPLICATION_SUBMITTED,
                {"confirmation_signal": "user_confirmed"},
            )
            return application
        if status not in TRACKABLE_USER_STATUSES:
            raise ApplicationStateError("Only outcome statuses can be set manually")
        if application.status not in {
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.INTERVIEW,
            ApplicationStatus.OFFER,
            ApplicationStatus.WITHDRAWN,
        }:
            raise ApplicationStateError("An outcome can only be recorded after submission")
        application.status = status
        application.updated_at = utc_now()
        self._save(application)
        return application

    def read(self, application: Application) -> ApplicationRead:
        job = self.session.get(Job, application.job_id)
        assert job is not None
        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        analysis = self.session.exec(
            select(JobAnalysisRecord)
            .where(JobAnalysisRecord.job_id == job.id)
            .order_by(JobAnalysisRecord.created_at.desc())
        ).first()
        is_demo = self._is_demo(application, job)
        delete_blocked_reason = self._delete_blocked_reason(application, is_demo=is_demo)
        restart_blocked_reason = self._restart_blocked_reason(application, is_demo=is_demo)
        return ApplicationRead(
            id=application.id,
            job_id=job.id,
            company=job.company,
            role=job.title,
            location=job.location,
            source_url=job.source_url,
            match_score=(analysis.result.get("overall_score") if analysis else None),
            resume_id=resume.id if resume else None,
            resume_label=resume.label if resume else None,
            resume_filename=resume.filename if resume else None,
            status=application.status,
            current_step=application.current_step,
            current_url=application.current_url,
            waiting_reason=application.waiting_reason,
            error_message=application.error_message,
            created_at=application.created_at,
            approved_at=application.approved_at,
            submitted_at=application.submitted_at,
            archived_at=application.archived_at,
            is_demo=is_demo,
            can_delete=delete_blocked_reason is None,
            delete_blocked_reason=delete_blocked_reason,
            can_restart=restart_blocked_reason is None,
            restart_blocked_reason=restart_blocked_reason,
            fields=self._fields(application.id),
        )

    async def archive_and_restart(self, application: Application) -> Application:
        """Preserve a confirmed-unsubmitted attempt and begin a clean one.

        The prior fields and event trail remain intact. A compare-and-set transition plus
        the database's one-active-application index prevent concurrent restart requests
        from creating duplicate active attempts for the same job.
        """

        job = self.session.get(Job, application.job_id)
        if job is None:
            raise ApplicationStateError("Job not found")
        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        reason = self._restart_blocked_reason(
            application,
            is_demo=self._is_demo(application, job),
        )
        if reason:
            raise ApplicationStateError(reason)

        archived_at = utc_now()
        statement = (
            update(Application)
            .where(
                Application.id == application.id,
                Application.status == application.status,
                Application.archived_at.is_(None),
                Application.submitted_at.is_(None),
                Application.approval_consumed_at.is_(None),
            )
            .values(
                status=ApplicationStatus.ARCHIVED,
                archived_at=archived_at,
                approval_token_hash=None,
                approved_payload_hash=None,
                approval_expires_at=None,
                approved_at=None,
                waiting_reason="restarted",
                error_message=None,
                updated_at=archived_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = self.session.execute(statement)
        if result.rowcount != 1:
            self.session.rollback()
            raise ApplicationStateError(
                "This application changed while it was being restarted. Refresh and check it again."
            )
        self.session.commit()
        self.session.expire_all()
        archived = self.session.get(Application, application.id)
        if archived is None:
            raise ApplicationStateError("Application disappeared while it was being archived")
        self._event(archived, EventType.APPLICATION_ARCHIVED, {})
        await self.browser.forget(archived.id)

        restarted = await self.start(job, resume)
        self._event(
            archived,
            EventType.APPLICATION_RESTARTED,
            {"replacement_application_id": restarted.id},
        )
        return restarted

    async def delete_application(self, application: Application) -> None:
        job = self.session.get(Job, application.job_id)
        if job is None:
            raise ApplicationStateError("Job not found")
        reason = self._delete_blocked_reason(
            application,
            is_demo=self._is_demo(application, job),
        )
        if reason:
            raise ApplicationStateError(reason)
        await self.browser.forget(application.id)
        self.session.exec(
            delete(ApplicationEvent).where(ApplicationEvent.application_id == application.id)
        )
        self.session.exec(
            delete(ApplicationField).where(ApplicationField.application_id == application.id)
        )
        self.session.delete(application)
        self.session.commit()

    async def _restore_page(self, application: Application) -> Page:
        if not application.current_url or not application.form_fingerprint:
            raise ApplicationStateError("Application page cannot be safely restored")
        page = await self.browser.open(application.id, application.current_url)
        located = await self._locate_application_form(page, allow_navigation=False)
        if located is None:
            raise ApplicationStateError("The approved application form is no longer present")
        page, _, form = located
        detected = await detect_fields(page, form)
        fingerprint = await form_fingerprint(page, form, detected)
        if not secrets_compare(fingerprint, application.form_fingerprint):
            raise ApplicationStateError("The application form changed and must be reviewed again")
        live_by_selector = {field.selector: field for field in detected}
        stored_fields = self._fields(application.id, active_only=True)
        if set(live_by_selector) != {field.selector for field in stored_fields}:
            raise ApplicationStateError("The application fields changed and must be reviewed again")
        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        for field in stored_fields:
            if field.skipped:
                await clear_field(page, live_by_selector[field.selector])
                continue
            if not field.answer:
                raise ApplicationStateError("Unresolved field blocks page restoration")
            live = live_by_selector[field.selector]
            upload = Path(resume.path) if live.field_type == "file" and resume else None
            await fill_field(
                page,
                live,
                field.answer,
                upload_path=upload,
                upload_name=resume.filename if upload and resume else None,
            )
        return page

    async def _locate_application_form(
        self,
        page: Page,
        *,
        allow_navigation: bool,
        application_key: str | None = None,
        expected_title: str | None = None,
        reuse_existing_tabs: bool = True,
    ) -> tuple[Page, ATSAdapter, Locator] | None:
        await ensure_submission_guard(page)
        adapter = ATSRegistry().for_url(page.url)
        form = await adapter.find_form(page)
        if form is None and allow_navigation:
            existing = (
                await self._find_existing_application_tab(page, expected_title)
                if reuse_existing_tabs
                else None
            )
            if existing is not None:
                page, adapter, form = existing
            else:
                pages_before_click = set(self.browser.open_pages())
                navigation_attempted = False
                try:
                    navigation_attempted = await adapter.open_application(page)
                except PlaywrightTimeoutError:
                    # The click can successfully open or focus an application tab while
                    # Playwright is still waiting on the source page's navigation lifecycle.
                    navigation_attempted = True
                if navigation_attempted:
                    located = await self._wait_for_opened_application_form(
                        page,
                        pages_before_click=pages_before_click,
                        expected_title=expected_title,
                    )
                    if located is not None:
                        page, adapter, form = located
                    else:
                        existing_after_click = await self._find_existing_application_tab(
                            page, expected_title
                        )
                        if existing_after_click is not None:
                            page, adapter, form = existing_after_click
        if form is None:
            access_gate = await self._find_access_gate_page(page)
            if access_gate is not None:
                if application_key is not None:
                    await self.browser.adopt(application_key, access_gate[0])
                return None
            located = await self._wait_for_application_form(page)
            if located is not None:
                adapter, form = located
        if form is None:
            return None
        await ensure_submission_guard(page)
        marked = await mark_application_form(page, form)
        if await marked.count() != 1:
            return None
        if application_key is not None:
            await self.browser.adopt(application_key, page)
        return page, adapter, marked

    async def _find_access_gate_page(self, reference_page: Page) -> tuple[Page, str] | None:
        # Once a signed-in application shell is active, an older login tab must not pull the
        # workflow backwards into WAITING_FOR_USER. A CAPTCHA on the active application page
        # itself is still detected below and always requires the user.
        try:
            if (
                not await self.browser.has_login(reference_page)
                and not await self.browser.has_captcha(reference_page)
                and await self._is_authenticated_application_page(reference_page)
            ):
                return None
        except PlaywrightError:
            pass

        candidates = await self._related_pages(reference_page, include_reference=True)
        for candidate in candidates:
            try:
                if await self.browser.has_login(candidate):
                    return candidate, "login"
            except PlaywrightError:
                continue
        for candidate in candidates:
            try:
                if await self.browser.has_captcha(candidate):
                    return candidate, "captcha"
            except PlaywrightError:
                continue
        return None

    async def _find_completed_access_page(
        self, reference_page: Page, expected_title: str | None
    ) -> Page | None:
        """Find a related tab that has completed an employer account handoff.

        Authentication portals commonly leave their sign-in tab open and launch the actual
        application in another tab. Only adopt a related page after proving that it is no
        longer gated and contains either a real application form or a strongly identified,
        signed-in application shell.
        """

        for candidate in await self._related_pages(reference_page, include_reference=False):
            try:
                if await self.browser.has_login(candidate) or await self.browser.has_captcha(
                    candidate
                ):
                    continue
                authenticated_shell = await self._is_authenticated_application_page(candidate)
                adapter = ATSRegistry().for_url(candidate.url)
                form = await adapter.find_form(candidate)
                if authenticated_shell:
                    return candidate
                if form is None:
                    continue
                if await self._page_mentions_job(candidate, expected_title) or await self._is_child_page(
                    candidate, reference_page
                ):
                    return candidate
            except PlaywrightError:
                # A portal can replace a tab's document while authentication redirects settle.
                continue
        return None

    async def _related_pages(
        self, reference_page: Page, *, include_reference: bool
    ) -> list[Page]:
        candidates = [reference_page] if include_reference else []
        for candidate in reversed(self.browser.open_pages()):
            if candidate is reference_page:
                continue
            try:
                if same_site(reference_page.url, candidate.url) or await self._is_child_page(
                    candidate, reference_page
                ):
                    candidates.append(candidate)
            except PlaywrightError:
                continue
        return candidates

    @staticmethod
    async def _is_authenticated_application_page(page: Page) -> bool:
        return bool(
            await page.locator("body").evaluate(
                r"""body => {
                  const text = (body.innerText || '').replace(/\s+/g, ' ').toLowerCase();
                  const signedIn = /\bsign out\b|\blog out\b|\blogout\b|\babmelden\b|\bausloggen\b/.test(text);
                  if (!signedIn) return false;

                  const explicitApplication = /job application form|application form|bewerbungsformular|meine bewerbung/.test(text);
                  const applicationSections =
                    (/my documents|meine dokumente|dokumente/.test(text) &&
                     /profile information|candidate information|pers[oö]nliche daten|profilinformationen/.test(text));
                  const applicationForm = Array.from(body.querySelectorAll('form')).some(form => {
                    const action = (form.getAttribute('action') || '').toLowerCase();
                    return /apply|application|bewerb/.test(action) ||
                      Boolean(form.querySelector('input[type=file], textarea'));
                  });
                  const actionLabels = Array.from(body.querySelectorAll(
                    'button, input[type=submit], a[role=button]'
                  )).map(element => (
                    element.innerText || element.value || element.getAttribute('aria-label') || ''
                  ).trim().toLowerCase());
                  const hasApply = actionLabels.some(label =>
                    /^(apply|submit application|send application|bewerbung absenden|bewerben)$/.test(label)
                  );
                  const hasSave = actionLabels.some(label => /^(save|speichern)$/.test(label));
                  return signedIn && (
                    explicitApplication || applicationSections || applicationForm ||
                    (hasApply && hasSave)
                  );
                }"""
            )
        )

    @staticmethod
    async def _is_child_page(candidate: Page, reference_page: Page) -> bool:
        current: Page | None = candidate
        seen: set[Page] = set()
        while current is not None and current not in seen:
            if current is reference_page:
                return True
            seen.add(current)
            current = await current.opener()
        return False

    async def _find_existing_application_tab(
        self, reference_page: Page, expected_title: str | None
    ) -> tuple[Page, ATSAdapter, Locator] | None:
        for candidate in reversed(self.browser.open_pages()):
            if candidate is reference_page or not same_site(reference_page.url, candidate.url):
                continue
            try:
                if not await self._page_mentions_job(candidate, expected_title):
                    continue
                adapter = ATSRegistry().for_url(candidate.url)
                form = await adapter.find_form(candidate)
                if form is not None:
                    return candidate, adapter, form
            except PlaywrightError:
                # New tabs and SPA routes can transiently replace their execution context.
                continue
        return None

    async def _wait_for_opened_application_form(
        self,
        reference_page: Page,
        *,
        pages_before_click: set[Page],
        expected_title: str | None,
        timeout_seconds: float = FORM_DISCOVERY_TIMEOUT_SECONDS,
    ) -> tuple[Page, ATSAdapter, Locator] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            current_pages = self.browser.open_pages()
            new_pages = [
                candidate for candidate in current_pages if candidate not in pages_before_click
            ]
            candidates = list(reversed(new_pages)) + [reference_page]
            title_matches: list[tuple[Page, ATSAdapter, Locator]] = []
            other_matches: list[tuple[Page, ATSAdapter, Locator]] = []
            for candidate in candidates:
                try:
                    adapter = ATSRegistry().for_url(candidate.url)
                    form = await adapter.find_form(candidate)
                    if form is None:
                        continue
                    match = (candidate, adapter, form)
                    if await self._page_mentions_job(candidate, expected_title):
                        title_matches.append(match)
                    else:
                        other_matches.append(match)
                except PlaywrightError:
                    continue
            if title_matches:
                return title_matches[0]
            if len(other_matches) == 1:
                return other_matches[0]
            for candidate in candidates:
                try:
                    if await self.browser.has_captcha(candidate) or await self.browser.has_login(
                        candidate
                    ):
                        return None
                except PlaywrightError:
                    continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(FORM_DISCOVERY_POLL_SECONDS, remaining))

    @staticmethod
    async def _page_mentions_job(page: Page, expected_title: str | None) -> bool:
        if not expected_title:
            return True
        expected = " ".join(expected_title.casefold().split())
        content = " ".join((await page.locator("body").inner_text()).casefold().split())
        return expected in content

    async def _wait_for_application_form(
        self,
        page: Page,
        *,
        timeout_seconds: float = FORM_DISCOVERY_TIMEOUT_SECONDS,
    ) -> tuple[ATSAdapter, Locator] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            try:
                adapter = ATSRegistry().for_url(page.url)
                form = await adapter.find_form(page)
                if form is not None:
                    return adapter, form
            except PlaywrightError:
                pass
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(FORM_DISCOVERY_POLL_SECONDS, remaining))

    async def _validate_live_payload(
        self,
        application: Application,
        page: Page,
        stored_fields: list[ApplicationField],
    ) -> tuple[Locator, str]:
        if not application.form_fingerprint:
            raise ApplicationStateError("The application has no reviewable form identity")
        located = await self._locate_application_form(page, allow_navigation=False)
        if located is None:
            raise ApplicationStateError("The reviewed application form is no longer present")
        page, _, form = located
        live_fields = await detect_fields(page, form)
        fingerprint = await form_fingerprint(page, form, live_fields)
        if not secrets_compare(fingerprint, application.form_fingerprint):
            raise ApplicationStateError("The application form changed and requires a new review")
        live_by_selector = {field.selector: field for field in live_fields}
        if set(live_by_selector) != {field.selector for field in stored_fields}:
            raise ApplicationStateError(
                "The application field set changed and requires a new review"
            )

        resume = self.session.get(Resume, application.resume_id) if application.resume_id else None
        states: list[dict] = []
        for stored in stored_fields:
            live = live_by_selector[stored.selector]
            availability_error = availability_answer_error(stored, stored.answer)
            if availability_error:
                raise ApplicationStateError(availability_error)
            state = await capture_field_state(page, live)
            if stored.skipped:
                if stored.required:
                    raise ApplicationStateError("A required field cannot be left blank")
                if not state_is_blank(live, state):
                    raise ApplicationStateError(
                        f"The optional field '{stored.question}' was not left blank as reviewed"
                    )
            elif not stored.answer:
                raise ApplicationStateError("Every field must have a truthful reviewed answer")
            elif live.field_type == "file":
                if resume is None:
                    raise ApplicationStateError(
                        f"No stored résumé is selected for '{stored.question}'"
                    )
                try:
                    upload_confirmation = await confirmed_existing_file_upload(
                        page,
                        live,
                        resume.filename,
                    )
                except FileUploadError as error:
                    raise ApplicationStateError(
                        f"The employer has not confirmed the selected résumé for "
                        f"'{stored.question}'. Check the employer window and try again."
                    ) from error
                if upload_confirmation is None:
                    raise ApplicationStateError(
                        f"The employer has not confirmed the selected résumé for "
                        f"'{stored.question}'. Check that the expected filename is visible "
                        "in the employer window, then try again."
                    )
                # Custom upload widgets commonly clear the native FileList after moving the
                # file into employer-managed storage. Bind approval to the exact stored
                # filename confirmed by either the native input or the stable widget UI.
                # Keep the hashed state independent of the portal-specific confirmation path
                # so a harmless native-input cleanup cannot invalidate the approval token.
                state = {"files": [resume.filename], "confirmed": True}
            elif not state_matches_answer(live, stored.answer, state):
                raise ApplicationStateError(
                    f"The live value for '{stored.question}' differs from the reviewed answer"
                )
            states.append({"selector": stored.selector, "state": state, "skipped": stored.skipped})
        historical_fields = [
            {
                "id": field.id,
                "step": field.step_index,
                "question": field.question,
                "type": field.field_type,
                "answer": field.answer,
                "skipped": field.skipped,
                "source": field.source.value,
            }
            for field in self._fields(application.id)
            if not field.active
        ]
        payload = {
            "application_id": application.id,
            "job_id": application.job_id,
            "resume_id": application.resume_id,
            "url": page.url,
            "form_fingerprint": fingerprint,
            "fields": states,
            "completed_steps": historical_fields,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return form, hashlib.sha256(encoded.encode()).hexdigest()

    async def _reconcile_live_fields(
        self,
        application: Application,
        page: Page,
    ) -> list[ApplicationField]:
        """Make persisted review state agree with the currently rendered employer form.

        Reactive portals may replace or reset controls after another field changes. A stored
        answer is therefore kept only when the rediscovered live control still contains that
        answer and reports a valid state. File widgets use their separate upload confirmation
        workflow because many portals intentionally clear the native file input after upload.
        """

        drifted: list[ApplicationField] = []
        for stored in self._fields(application.id, active_only=True):
            if stored.field_type == "file" or not self._field_is_resolved(stored):
                continue
            detected = self._detected_from_stored(stored)
            semantic_error = availability_answer_error(stored, stored.answer)
            try:
                live = await rediscover_field(page, detected)
                stored.selector = live.selector
                state = await capture_field_state(page, live)
                matches = (
                    state_is_blank(live, state)
                    if stored.skipped
                    else bool(stored.answer)
                    and state_matches_answer(live, stored.answer or "", state)
                )
                matches = matches and semantic_error is None
            except (PlaywrightError, ValueError):
                matches = False
            if matches:
                self.session.add(stored)
                continue
            stored.answer = None
            stored.skipped = False
            stored.source = AnswerSource.UNKNOWN
            stored.confidence = 0.0
            stored.uncertain = True
            stored.disqualifying = False
            stored.resolution_message = semantic_error or (
                "The employer form no longer contains the saved value. Enter this field again."
            )
            stored.ai_retryable = False
            stored.updated_at = utc_now()
            self.session.add(stored)
            drifted.append(stored)
            log_event(
                "FIELD_STATE_DRIFTED",
                entity_id=application.id,
                field_id=stored.id,
                field_type=stored.field_type,
            )
        self.session.flush()
        return drifted

    def _fields(self, application_id: str, *, active_only: bool = False) -> list[ApplicationField]:
        statement = select(ApplicationField).where(
            ApplicationField.application_id == application_id
        )
        if active_only:
            statement = statement.where(ApplicationField.active.is_(True))
        return list(
            self.session.exec(
                statement.order_by(
                    ApplicationField.step_index,
                    ApplicationField.created_at,
                    ApplicationField.id,
                )
            ).all()
        )

    def _refresh_readiness(self, application: Application) -> None:
        all_fields = self._fields(application.id)
        if any(not self._field_is_resolved(item) for item in all_fields):
            application.status = ApplicationStatus.NEEDS_USER_INPUT
            application.waiting_reason = None
            application.error_message = None
        elif application.form_action == "continue":
            application.status = ApplicationStatus.READY_TO_ADVANCE
            application.waiting_reason = None
            application.error_message = None
        elif application.form_action == "final":
            application.status = ApplicationStatus.READY_FOR_REVIEW
            application.waiting_reason = None
            application.error_message = None
        else:
            application.status = ApplicationStatus.WAITING_FOR_USER
            application.waiting_reason = "ambiguous_form_action"
            application.error_message = (
                "The form has no unambiguous final Submit or Next/Continue control. "
                "Inspect the visible browser; submission remains blocked."
            )

    def _delete_blocked_reason(self, application: Application, *, is_demo: bool) -> str | None:
        if is_demo:
            return None
        if application.status == ApplicationStatus.ARCHIVED and application.archived_at is not None:
            submitted_event = self.session.exec(
                select(ApplicationEvent)
                .where(ApplicationEvent.application_id == application.id)
                .where(
                    ApplicationEvent.event_type == EventType.APPLICATION_SUBMITTED.value
                )
                .limit(1)
            ).first()
            if application.submitted_at is None and submitted_event is None:
                return None
        submission_event = self.session.exec(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .where(
                ApplicationEvent.event_type.in_(
                    {
                        EventType.APPLICATION_SUBMITTED.value,
                        EventType.SUBMISSION_UNCONFIRMED.value,
                    }
                )
            )
            .limit(1)
        ).first()
        if (
            application.status in PROTECTED_APPLICATION_STATUSES
            or application.current_step > 1
            or application.submitted_at is not None
            or application.approval_consumed_at is not None
            or submission_event is not None
        ):
            return (
                "This record may represent data already sent to an employer, so it is retained to "
                "prevent an accidental duplicate application. Local demo records can always be deleted."
            )
        return None

    def _restart_blocked_reason(self, application: Application, *, is_demo: bool) -> str | None:
        if is_demo:
            return "Local demos can use Delete & redo instead."
        if application.archived_at is not None or application.status == ApplicationStatus.ARCHIVED:
            return "This attempt is already archived."
        if application.submitted_at is not None or application.status in PROTECTED_APPLICATION_STATUSES:
            return (
                "First verify the employer result. If the employer did not receive it, choose "
                "Not submitted — return to review before restarting."
            )
        if application.approval_consumed_at is not None:
            return "A consumed submission approval must be resolved before restarting."
        submission_recorded = self.session.exec(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .where(ApplicationEvent.event_type == EventType.APPLICATION_SUBMITTED.value)
            .limit(1)
        ).first()
        if submission_recorded is not None:
            return "An application recorded as received by the employer cannot be restarted."
        confirmed_not_submitted = self.session.exec(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .where(
                ApplicationEvent.event_type == EventType.SUBMISSION_RETRY_AUTHORIZED.value
            )
            .limit(1)
        ).first()
        if confirmed_not_submitted is None:
            return (
                "Restart is available only after an uncertain attempt is explicitly confirmed "
                "as not submitted."
            )
        return None

    def _is_demo(self, application: Application, job: Job) -> bool:
        url = application.current_url or job.application_url or job.source_url
        if not url:
            return False
        parsed = urlsplit(url)
        base = urlsplit(self.settings.app_base_url)
        parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        base_port = base.port or (443 if base.scheme == "https" else 80)
        return (
            parsed.scheme == base.scheme
            and parsed.hostname == base.hostname
            and parsed_port == base_port
            and parsed.path.rstrip("/") == "/demo/application"
        )

    @staticmethod
    def _detected_from_stored(field: ApplicationField) -> DetectedField:
        return DetectedField(
            selector=field.selector,
            question=field.question,
            translated_question=field.translated_question,
            source_language=field.source_language,
            canonical_key=field.field_key,
            field_type=field.field_type,
            required=field.required,
            character_limit=field.character_limit,
            options=field.options,
            translated_options=field.translated_options,
            translation_confidence=field.translation_confidence,
        )

    @staticmethod
    def _field_is_resolved(field: ApplicationField) -> bool:
        return field.skipped or bool(field.answer and field.answer.strip())

    @staticmethod
    def _invalidate_approval(application: Application) -> None:
        application.approval_token_hash = None
        application.approved_payload_hash = None
        application.approval_expires_at = None
        application.approved_at = None
        application.approval_consumed_at = None

    def _wait_for_user(self, application: Application, reason: str, message: str) -> Application:
        application.status = ApplicationStatus.WAITING_FOR_USER
        application.waiting_reason = reason
        application.error_message = message
        self._save(application)
        self._event(application, EventType.USER_INPUT_REQUIRED, {"reason": reason})
        return application

    def _event(self, application: Application, event_type: EventType, details: dict) -> None:
        self.session.add(
            ApplicationEvent(
                application_id=application.id,
                event_type=event_type.value,
                details=details,
            )
        )
        self.session.commit()

    def _save(self, application: Application) -> None:
        application.updated_at = utc_now()
        self.session.add(application)
        self.session.commit()
        self.session.refresh(application)

    @staticmethod
    def _host(url: str) -> str:
        return url.split("//", 1)[-1].split("/", 1)[0]


def secrets_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
