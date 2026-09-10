import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from playwright.async_api import Locator, Page

from app.config import Settings


class SubmissionBlockedError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class SubmissionEvidence:
    confirmed: bool
    signal: str | None
    final_url: str
    attempted: bool = True
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FormActionInspection:
    action: str
    reason: str
    labels: tuple[str, ...] = ()

    def waiting_message(self) -> str:
        labels = ", ".join(f'“{label[:80]}”' for label in self.labels[:3])
        if self.reason == "multiple_action_controls":
            return (
                f"Multiple possible Next/Submit controls were found ({labels}). "
                "Nothing was clicked. Check the employer page, then check the controls again."
            )
        if self.reason == "disabled_action_control":
            return (
                f"The employer’s {labels or 'Next/Submit control'} is currently disabled. "
                "Look for a missing or invalid required field in the employer window, correct it, "
                "then check the controls again."
            )
        if self.reason == "hidden_action_control":
            return (
                f"The employer’s {labels or 'Next/Submit control'} exists but is hidden. "
                "The portal may be waiting for a required field or validation step. Resolve any "
                "visible warning, then check the controls again."
            )
        if self.reason == "unbound_action_control":
            return (
                f"A possible action control ({labels}) was found outside the selected application "
                "form. Nothing was clicked because it cannot be safely tied to this application."
            )
        if self.reason == "unrecognized_control_labels":
            return (
                f"The form has controls ({labels}), but none is clearly labelled as Next or "
                "Submit. Nothing was clicked; check the employer page and try the control check again."
            )
        return (
            "No Next or Submit control is currently rendered in the application form. The employer "
            "may be withholding it until every required field is valid. Check the employer page for "
            "field warnings, then check the controls again."
        )


@dataclass(frozen=True, slots=True)
class _FormActionCandidate:
    control: Locator
    label: str
    action: str | None
    associated: bool
    visible: bool
    enabled: bool


_PAGE_GUARDS: dict[int, str] = {}
# Some portals render their primary action outside the native ``<form>`` or as an
# accessible custom button.  The selector intentionally remains limited to controls
# that a user can actually activate; plain links and arbitrary containers are never
# considered application actions.
_FORM_ACTION_CONTROL_SELECTOR = (
    "button, input[type='submit'], input[type='button'], input[type='image'], "
    "a[role='button'], [role='button']"
)
_ACTION_SCOPE_ATTRIBUTE = "data-jaa-application-action-scope"
_FINAL_ACTION_LABEL = re.compile(
    r"submit(?: (?:my |the )?application)?|send(?: (?:my |the )?application)?|"
    r"complete(?: (?:my |the )?application)?|finish(?: (?:my |the )?application)?|"
    r"apply(?: now)?|jetzt bewerben|bewerben|"
    r"(?:bewerbung )?(?:absenden|senden|einreichen|abschicken)",
    re.IGNORECASE,
)
_CONTINUE_ACTION_LABEL = re.compile(
    r"next|continue|next step|go to next step|"
    r"save\s*(?:and|&)?\s*continue|"
    r"weiter|fortfahren|n(?:ä|ae)chster schritt|zum n(?:ä|ae)chsten schritt|"
    r"speichern\s*(?:und|&)?\s*weiter",
    re.IGNORECASE,
)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_approval_token(settings: Settings) -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.approval_ttl_minutes)
    return token, token_digest(token), expires_at


def verify_approval_token(
    token: str, expected_digest: str | None, expires_at: datetime | None
) -> bool:
    if not expected_digest or not expires_at:
        return False
    expiry = expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expiry:
        return False
    return hmac.compare_digest(token_digest(token), expected_digest)


async def ensure_submission_guard(page: Page) -> None:
    page_key = id(page)
    guard_name = _PAGE_GUARDS.get(page_key)
    if guard_name and await page.evaluate("name => Boolean(window[name])", guard_name):
        return
    guard_name = f"__jaaGuard_{secrets.token_hex(16)}"
    _PAGE_GUARDS[page_key] = guard_name
    await page.evaluate(
        r"""name => {
          let authorizedForm = null;
          let releaseAfterSubmit = false;
          let releasedForm = null;
          let authorizationTimer = null;
          let attempt = null;
          const nativeSubmit = HTMLFormElement.prototype.submit;
          const nativeRequestSubmit = HTMLFormElement.prototype.requestSubmit;
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const fieldName = element => clean(
            element.getAttribute('aria-label') ||
            Array.from(element.labels || []).map(label => label.textContent || '').join(' ') ||
            element.getAttribute('name') || element.id || element.type || element.tagName
          ).slice(0, 160);
          const clearAuthorization = () => {
            authorizedForm = null;
            releaseAfterSubmit = false;
            if (authorizationTimer !== null) clearTimeout(authorizationTimer);
            authorizationTimer = null;
          };
          const authorize = (form, shouldReleaseAfterSubmit, timeoutMs) => {
            clearAuthorization();
            authorizedForm = form;
            releaseAfterSubmit = shouldReleaseAfterSubmit;
            attempt = {form, invalidFields: [], submitObserved: false};
            authorizationTimer = setTimeout(clearAuthorization, timeoutMs);
          };
          const guard = {
            authorizeFinal(form) {
              authorize(form, true, 2000);
            },
            authorizeAdvance(form) {
              authorize(form, false, 2000);
            },
            release(form) {
              clearAuthorization();
              releasedForm = form;
              attempt = {form, invalidFields: [], submitObserved: false};
            },
            lock(form) {
              clearAuthorization();
              if (releasedForm === form) releasedForm = null;
            },
            clear() { clearAuthorization(); },
            attemptFor(form) {
              if (!attempt || attempt.form !== form) {
                return {invalidFields: [], submitObserved: false};
              }
              return {
                invalidFields: [...attempt.invalidFields],
                submitObserved: attempt.submitObserved
              };
            }
          };
          Object.defineProperty(window, name, {value: guard, configurable: false});
          document.addEventListener('invalid', event => {
            if (!attempt || !attempt.form.contains(event.target)) return;
            const label = fieldName(event.target);
            if (label && !attempt.invalidFields.includes(label)) {
              attempt.invalidFields.push(label);
            }
          }, true);
          document.addEventListener('submit', event => {
            if (event.target === releasedForm) {
              if (attempt?.form === event.target) attempt.submitObserved = true;
              return;
            }
            if (event.target === authorizedForm) {
              if (attempt?.form === event.target) attempt.submitObserved = true;
              if (releaseAfterSubmit) releasedForm = event.target;
              clearAuthorization();
              return;
            }
            event.preventDefault();
            event.stopImmediatePropagation();
          }, true);
          HTMLFormElement.prototype.submit = function() {
            if (this === releasedForm) {
              if (attempt?.form === this) attempt.submitObserved = true;
              return nativeSubmit.call(this);
            }
            if (this !== authorizedForm) return;
            if (attempt?.form === this) attempt.submitObserved = true;
            if (releaseAfterSubmit) releasedForm = this;
            clearAuthorization();
            return nativeSubmit.call(this);
          };
          HTMLFormElement.prototype.requestSubmit = function(submitter) {
            if (this !== releasedForm && this !== authorizedForm) return;
            return nativeRequestSubmit.call(this, submitter);
          };
        }""",
        guard_name,
    )


async def allow_manual_submission(page: Page, form: Locator) -> None:
    """Leave the approved form available for user-triggered submission.

    Callers must consume a fresh backend approval token before reaching this function.
    The selected form remains available until it is explicitly locked or the page unloads.
    """

    if await form.count() != 1:
        raise SubmissionBlockedError("The approved application form is no longer uniquely present")
    await ensure_submission_guard(page)
    guard_name = _PAGE_GUARDS[id(page)]
    await form.evaluate(
        "(element, name) => window[name].release(element)",
        guard_name,
    )


async def lock_submission_form(page: Page, form: Locator) -> None:
    """Restore pre-approval protection for a form returning to review."""

    await ensure_submission_guard(page)
    guard_name = _PAGE_GUARDS[id(page)]
    await form.evaluate("(element, name) => window[name].lock(element)", guard_name)


async def focus_form_action_control(page: Page, form: Locator, action: str) -> None:
    control = await find_form_action_control(form, action)
    if control is None:
        raise SubmissionBlockedError("No unambiguous employer action control was found")
    await page.bring_to_front()
    await control.scroll_into_view_if_needed()
    await control.focus()
    await control.evaluate(
        "element => element.scrollIntoView({block: 'center', inline: 'nearest'})"
    )


async def guarded_submit(
    page: Page,
    form: Locator | None = None,
    *,
    approved: bool,
    confirmation_timeout_ms: int = 10_000,
) -> SubmissionEvidence:
    if not approved:
        raise SubmissionBlockedError("Explicit backend approval is required")
    if form is None:
        raise SubmissionBlockedError("An approved application form is required")
    if await form.count() != 1:
        raise SubmissionBlockedError("The approved application form is no longer uniquely present")

    await ensure_submission_guard(page)
    guard_name = _PAGE_GUARDS[id(page)]
    await form.evaluate("(element, name) => window[name].authorizeFinal(element)", guard_name)
    initial_url = page.url
    submitter = await find_form_action_control(form, "final")
    if submitter is None:
        await page.evaluate("name => window[name].clear()", guard_name)
        raise RuntimeError("No submission control exists inside the approved application form")

    try:
        await submitter.click()
    except Exception:
        if not page.is_closed():
            await page.evaluate("name => window[name]?.clear()", guard_name)
        raise

    confirmation = page.get_by_text(
        re.compile(
            r"application (received|submitted)|thank you for applying|"
            r"bewerbung (eingegangen|erhalten)|vielen dank.*bewerbung",
            re.IGNORECASE,
        )
    )
    attempts = max(1, confirmation_timeout_ms // 250)
    for _ in range(attempts):
        if page.is_closed():
            return SubmissionEvidence(False, "page_closed", initial_url)
        if page.url != initial_url:
            return SubmissionEvidence(True, "url_changed", page.url)
        if not await form.count() or not await form.is_visible():
            return SubmissionEvidence(True, "form_removed_or_hidden", page.url)
        if await confirmation.count() and await confirmation.first.is_visible():
            return SubmissionEvidence(True, "confirmation_text", page.url)
        attempt = await form.evaluate(
            "(element, name) => window[name].attemptFor(element)",
            guard_name,
        )
        invalid_fields = tuple(str(value) for value in attempt["invalidFields"][:5])
        if invalid_fields and not attempt["submitObserved"]:
            return SubmissionEvidence(
                False,
                "validation_failed",
                page.url,
                attempted=False,
                validation_errors=invalid_fields,
            )
        await page.wait_for_timeout(250)
    return SubmissionEvidence(False, None, page.url)


async def classify_form_action(form: Locator) -> str:
    return (await inspect_form_action(form)).action


async def inspect_form_action(form: Locator) -> FormActionInspection:
    """Classify the current form action without clicking or advancing the page.

    The diagnostic reason distinguishes a genuinely missing action from a disabled,
    hidden, unbound, or multiply-matched control. This keeps portal-specific markup out
    of the service layer while giving the user a useful recovery path.
    """

    candidates = await _form_action_candidates(form)
    eligible = [
        candidate
        for candidate in candidates
        if candidate.associated
        and candidate.visible
        and candidate.enabled
        and candidate.action is not None
    ]
    if len(eligible) == 1:
        return FormActionInspection(
            action=eligible[0].action or "unknown",
            reason="recognized",
            labels=(eligible[0].label,),
        )
    if len(eligible) > 1:
        return FormActionInspection(
            action="unknown",
            reason="multiple_action_controls",
            labels=tuple(candidate.label for candidate in eligible),
        )

    disabled = [
        candidate
        for candidate in candidates
        if candidate.associated
        and candidate.visible
        and not candidate.enabled
        and candidate.action is not None
    ]
    if disabled:
        return FormActionInspection(
            action="unknown",
            reason="disabled_action_control",
            labels=tuple(candidate.label for candidate in disabled),
        )

    hidden = [
        candidate
        for candidate in candidates
        if candidate.associated and not candidate.visible and candidate.action is not None
    ]
    if hidden:
        return FormActionInspection(
            action="unknown",
            reason="hidden_action_control",
            labels=tuple(candidate.label for candidate in hidden),
        )

    unbound = [
        candidate
        for candidate in candidates
        if not candidate.associated
        and candidate.visible
        and candidate.enabled
        and candidate.action is not None
    ]
    if unbound:
        return FormActionInspection(
            action="unknown",
            reason="unbound_action_control",
            labels=tuple(candidate.label for candidate in unbound),
        )

    unrecognized = [
        candidate.label
        for candidate in candidates
        if candidate.associated and candidate.visible and candidate.enabled and candidate.label
    ]
    return FormActionInspection(
        action="unknown",
        reason="unrecognized_control_labels" if unrecognized else "no_action_control",
        labels=tuple(unrecognized),
    )


async def find_form_action_control(form: Locator, action: str) -> Locator | None:
    matches = [
        candidate.control
        for candidate in await _form_action_candidates(form)
        if candidate.associated
        and candidate.visible
        and candidate.enabled
        and candidate.action == action
    ]
    return matches[0] if len(matches) == 1 else None


async def _form_action_candidates(form: Locator) -> list[_FormActionCandidate]:
    form_handle = await form.element_handle()
    if form_handle is None:
        return []
    await _mark_external_action_scope(form)
    controls = form.page.locator(_FORM_ACTION_CONTROL_SELECTOR)
    candidates: list[_FormActionCandidate] = []
    for index in range(await controls.count()):
        control = controls.nth(index)
        details = await control.evaluate(
            r"""(element, {selectedForm, scopeAttribute}) => ({
              label: (
                element.getAttribute('aria-label') || element.innerText || element.value ||
                element.getAttribute('title') || ''
              ).replace(/\s+/g, ' ').trim(),
              associated: selectedForm.contains(element) || element.form === selectedForm,
              inActionScope: Boolean(
                element.closest(`[${scopeAttribute}='active']`)
              ),
              ariaDisabled: element.getAttribute('aria-disabled') === 'true'
            })""",
            {"selectedForm": form_handle, "scopeAttribute": _ACTION_SCOPE_ATTRIBUTE},
        )
        label = details["label"]
        action = _action_for_label(label)
        candidates.append(
            _FormActionCandidate(
                control=control,
                label=label,
                action=action,
                # Page-level actions are admitted only after the nearest surrounding
                # application area proved that it contains exactly one recognized
                # Next/Submit control.  An arbitrary button merely sharing a broad
                # page container can therefore never become a submission control.
                associated=details["associated"]
                or (details["inActionScope"] and action is not None),
                visible=await control.is_visible(),
                enabled=not details["ariaDisabled"] and not await control.is_disabled(),
            )
        )
    return candidates


async def _mark_external_action_scope(form: Locator) -> None:
    """Mark the closest safe owner for a page-level Next/Submit control.

    A number of ATS portals keep the input controls in a nested native form, but
    render the final action bar as a sibling.  We accept that layout only when the
    *nearest* shared ancestor contains exactly one visible, enabled, explicitly
    labelled action outside the form.  This is deliberately stricter than scanning
    the whole page, which could otherwise adopt an unrelated job CTA or newsletter
    button.
    """

    await form.evaluate(
        r"""(selectedForm, scopeAttribute) => {
          document.querySelectorAll(`[${scopeAttribute}]`).forEach(element =>
            element.removeAttribute(scopeAttribute)
          );
          const selector = "button, input[type='submit'], input[type='button'], " +
            "input[type='image'], a[role='button'], [role='button']";
          const clean = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
          const label = element => clean(
            element.getAttribute('aria-label') || element.innerText || element.value ||
            element.getAttribute('title') || ''
          );
          const visible = element => {
            const style = window.getComputedStyle(element);
            return element.getClientRects().length > 0 && style.visibility !== 'hidden' &&
              style.display !== 'none';
          };
          const enabled = element => !element.disabled && element.getAttribute('aria-disabled') !== 'true';
          const recognizedAction = value => /^(?:submit(?: (?:my |the )?application)?|send(?: (?:my |the )?application)?|complete(?: (?:my |the )?application)?|finish(?: (?:my |the )?application)?|apply(?: now)?|jetzt bewerben|bewerben|(?:bewerbung )?(?:absenden|senden|einreichen|abschicken)|next|continue|next step|go to next step|save\s*(?:and|&)?\s*continue|weiter|fortfahren|n(?:ä|ae)chster schritt|zum n(?:ä|ae)chsten schritt|speichern\s*(?:und|&)?\s*weiter)$/i.test(value);

          for (
            let ancestor = selectedForm.parentElement;
            ancestor && ancestor !== document.body && ancestor !== document.documentElement;
            ancestor = ancestor.parentElement
          ) {
            const externalActions = Array.from(ancestor.querySelectorAll(selector)).filter(control =>
              !selectedForm.contains(control) && control.form !== selectedForm &&
              visible(control) && enabled(control) && recognizedAction(label(control))
            );
            if (externalActions.length === 1) {
              ancestor.setAttribute(scopeAttribute, 'active');
              return;
            }
          }
        }""",
        _ACTION_SCOPE_ATTRIBUTE,
    )


def _action_for_label(label: str) -> str | None:
    normalized = " ".join(label.casefold().split())
    if _FINAL_ACTION_LABEL.fullmatch(normalized):
        return "final"
    if _CONTINUE_ACTION_LABEL.fullmatch(normalized):
        return "continue"
    return None


async def guarded_advance(page: Page, form: Locator) -> None:
    if await form.count() != 1:
        raise SubmissionBlockedError("The current application step is no longer uniquely present")
    control = await find_form_action_control(form, "continue")
    if control is None:
        raise SubmissionBlockedError("No unambiguous Next/Continue control was found")
    await ensure_submission_guard(page)
    guard_name = _PAGE_GUARDS[id(page)]
    before_url = page.url
    before_html = await form.evaluate("element => element.innerHTML")
    await form.evaluate("(element, name) => window[name].authorizeAdvance(element)", guard_name)
    await control.click()
    for _ in range(40):
        if page.is_closed():
            raise RuntimeError("Application page closed while advancing to the next step")
        if page.url != before_url or not await form.count():
            return
        if await form.evaluate("element => element.innerHTML") != before_html:
            return
        await page.wait_for_timeout(250)
    raise RuntimeError("The application did not advance to a new step")
