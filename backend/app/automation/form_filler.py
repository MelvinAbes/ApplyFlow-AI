import asyncio
import json
import mimetypes
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import ElementHandle, FileChooser, Locator, Page
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.automation.browser import AUTOMATION_SHIELD_ID
from app.automation.field_detector import (
    DetectedField,
    associated_option_locator,
    rediscover_field,
)

FILE_UPLOAD_CONFIRMATION_TIMEOUT_MS = 15_000
FILE_UPLOAD_STABILITY_MS = 2_000
FILE_CHOOSER_INITIAL_TIMEOUT_MS = 1_500
FILE_CHOOSER_SOURCE_TIMEOUT_MS = 5_000


class FileUploadError(ValueError):
    """A file upload failure with metadata that is safe to write to operational logs."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        safe_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_details = safe_details or {}


class FileUploadUserActionRequired(FileUploadError):
    """The employer requires a human decision before an upload can complete."""


@dataclass(frozen=True)
class FileUploadResult:
    strategy: str
    confirmation_signal: str
    safe_details: dict[str, Any]


@dataclass
class _UploadNetworkTrace:
    write_response_count: int = 0
    http_4xx_count: int = 0
    http_5xx_count: int = 0
    failed_write_request_count: int = 0
    _write_request_ids: set[int] = dataclass_field(default_factory=set)

    def record_response(self, response: Any) -> None:
        request = response.request
        if request.method.upper() not in {"POST", "PUT", "PATCH"}:
            return
        self._write_request_ids.add(id(request))
        self.write_response_count += 1
        if 400 <= response.status < 500:
            self.http_4xx_count += 1
        elif response.status >= 500:
            self.http_5xx_count += 1

    def record_failed_request(self, request: Any) -> None:
        if request.method.upper() in {"POST", "PUT", "PATCH"}:
            self._write_request_ids.add(id(request))
            self.failed_write_request_count += 1

    def snapshot(self) -> dict[str, int]:
        return {
            "write_request_count": len(self._write_request_ids),
            "write_response_count": self.write_response_count,
            "http_4xx_count": self.http_4xx_count,
            "http_5xx_count": self.http_5xx_count,
            "failed_write_request_count": self.failed_write_request_count,
        }


async def fill_field(
    page: Page,
    field: DetectedField,
    answer: str,
    *,
    upload_path: Path | None = None,
    upload_name: str | None = None,
) -> FileUploadResult | None:
    if field.field_type == "file":
        locator = page.locator(field.selector)
        if upload_path is None or not upload_path.is_file():
            raise ValueError("A valid local file is required for a file-upload field")
        original_name = Path(upload_name or upload_path.name).name or upload_path.name
        existing_upload = await confirmed_existing_file_upload(
            page, field, original_name
        )
        if existing_upload is not None:
            return existing_upload
        upload_payload = {
            "name": original_name,
            "mimeType": mimetypes.guess_type(original_name)[0]
            or "application/octet-stream",
            "buffer": await asyncio.to_thread(upload_path.read_bytes),
        }
        network_trace = _UploadNetworkTrace()
        page.on("response", network_trace.record_response)
        page.on("requestfailed", network_trace.record_failed_request)
        try:
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    locator = await _live_file_input(page, field)
                    element, strategy = await _set_file_payload(
                        page, locator, upload_payload
                    )
                    uploaded_names = await element.evaluate(
                        "element => Array.from(element.files || []).map(file => file.name)"
                    )
                    if uploaded_names != [original_name]:
                        raise FileUploadError(
                            "filename_not_preserved",
                            "The resume filename was not preserved by the application form",
                            safe_details={
                                "input_file_count": len(uploaded_names),
                                **network_trace.snapshot(),
                            },
                        )
                    confirmation_signal, observation = (
                        await _wait_for_file_upload_confirmation(
                            page, field, original_name
                        )
                    )
                    return FileUploadResult(
                        strategy=strategy,
                        confirmation_signal=confirmation_signal,
                        safe_details={
                            **_safe_upload_observation(observation),
                            **network_trace.snapshot(),
                        },
                    )
                except FileUploadError as error:
                    error.safe_details = {
                        **error.safe_details,
                        **network_trace.snapshot(),
                    }
                    raise
                except (PlaywrightError, PlaywrightTimeoutError) as error:
                    # Reactive portals can replace their hidden file input between detection and
                    # upload. Rediscover it once instead of waiting on the detached marked node.
                    last_error = error
            raise FileUploadError(
                "browser_input_unavailable",
                "The resume could not be attached to the application form",
                safe_details=network_trace.snapshot(),
            ) from last_error
        finally:
            page.remove_listener("response", network_trace.record_response)
            page.remove_listener("requestfailed", network_trace.record_failed_request)
    live = await rediscover_field(page, field)
    field.selector = live.selector
    locator = page.locator(field.selector)
    try:
        await _fill_non_file_field(page, live, locator, answer)
    except (PlaywrightError, PlaywrightTimeoutError) as error:
        # A React/Vue control can replace itself synchronously from its click/input handler.
        # The action may therefore have succeeded even though Playwright observed a detached
        # element. Only accept that case after rediscovering and reading the live state back.
        if not await _wait_for_matching_state(page, field, answer):
            raise error
        return None
    await _blur_if_present(page, field)
    if not await _wait_for_matching_state(page, field, answer):
        raise ValueError("The employer field did not retain the requested answer")
    return None


async def _fill_non_file_field(
    page: Page,
    field: DetectedField,
    locator: Locator,
    answer: str,
) -> None:
    if field.field_type == "select":
        try:
            await locator.select_option(label=answer)
        except Exception:
            await locator.select_option(value=answer)
        return
    if field.field_type == "combobox":
        await _fill_combobox(page, locator, answer)
        return
    if field.field_type == "date":
        await _fill_date(page, locator, answer)
        return
    if field.field_type == "radio":
        selected = await locator.evaluate_all(
            r"""(elements, expected) => {
              const clean = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
              const wanted = clean(expected);
              const optionText = element => {
                const explicit = element.id
                  ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
                  : null;
                return clean(explicit?.textContent || element.closest('label')?.textContent || element.value);
              };
              const match = elements.find(element =>
                clean(element.value) === wanted || optionText(element) === wanted
              );
              if (!match) return false;
              match.click();
              return true;
            }""",
            answer,
        )
        if not selected:
            raise ValueError("The requested radio option is not present")
        return
    if field.field_type == "checkbox":
        expected = answer.casefold()
        if expected in {"yes", "true", "1", "checked", "on"}:
            wanted = True
        elif expected in {"no", "false", "0", "unchecked", "off"}:
            wanted = False
        else:
            raise ValueError("Checkbox answers must unambiguously mean yes or no")
        await locator.evaluate(
            "(element, checked) => { if (Boolean(element.checked) !== checked) element.click(); }",
            wanted,
        )
        return
    await locator.fill(answer)


async def _blur_if_present(page: Page, field: DetectedField) -> None:
    try:
        live = await rediscover_field(page, field)
        field.selector = live.selector
        await page.locator(field.selector).first.evaluate("element => element.blur()")
    except (PlaywrightError, ValueError):
        pass


async def _wait_for_matching_state(
    page: Page,
    field: DetectedField,
    answer: str,
    *,
    attempts: int = 20,
) -> bool:
    consecutive_matches = 0
    for _ in range(attempts):
        try:
            live = await rediscover_field(page, field)
            field.selector = live.selector
            state = await capture_field_state(page, live)
            if state_matches_answer(live, answer, state):
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    return True
            else:
                consecutive_matches = 0
        except (PlaywrightError, ValueError):
            consecutive_matches = 0
        await page.wait_for_timeout(100)
    return False


async def _replace_existing_file(
    page: Page, locator: Locator, upload_payload: dict[str, Any]
) -> tuple[ElementHandle, str] | None:
    marker = "data-jaa-file-replace-control"
    await page.locator(f"[{marker}]").evaluate_all(
        "(elements, attribute) => elements.forEach(element => element.removeAttribute(attribute))",
        marker,
    )
    found = await locator.evaluate(
        r"""(input, attribute) => {
          const words = /\b(replace|change|choose|select|browse|upload|attach|ersetzen|austauschen|auswählen|hochladen|remplacer|sélectionner|téléverser|reemplazar|seleccionar|subir|sostituire|selezionare|caricare|vervangen|selecteren|uploaden)\b/i;
          const visible = element => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' &&
              Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
          };
          // A custom résumé trigger was already semantically validated by field
          // detection. It may be an icon whose accessible name comes from surrounding
          // markup, so do not require visible upload words a second time here.
          if (!input.matches('input[type="file"]')) {
            if (!visible(input)) return false;
            input.setAttribute(attribute, 'active');
            return true;
          }
          let container = input.parentElement;
          for (let depth = 0; container && depth < 7; depth += 1, container = container.parentElement) {
            const fileInputs = Array.from(container.querySelectorAll('input[type="file"]'));
            if (fileInputs.length > 1) return false;
            const controls = Array.from(container.querySelectorAll(
              'button, [role="button"], label[for]'
            ));
            const trigger = controls.find(control => {
              const labelTarget = control.tagName === 'LABEL'
                ? document.getElementById(control.getAttribute('for') || '')
                : null;
              const text = (
                control.getAttribute('aria-label') || control.innerText || control.textContent || ''
              ).trim();
              return visible(control) && (labelTarget === input || words.test(text));
            });
            if (trigger) {
              trigger.setAttribute(attribute, 'active');
              return true;
            }
          }
          return false;
        }""",
        marker,
    )
    if not found:
        return None
    trigger = page.locator(f"[{marker}='active']")
    try:
        try:
            chooser = await _chooser_from_click(
                page, trigger, timeout_ms=FILE_CHOOSER_INITIAL_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            # Some employer portals first ask where the file should come from (for
            # example, "Upload from Device" versus cloud providers). That intermediate
            # picker is a standard browser-form pattern, not an ATS-specific adapter.
            source = await _local_file_source_control(page)
            if source is None:
                return None
            if await source.evaluate(
                "element => element.matches('input[type=file]')"
            ):
                source_element = await source.element_handle()
                if source_element is None:
                    return None
                # Native upload controls are often transparent overlays. Setting the
                # file on that exact input is both more reliable and more narrowly
                # scoped than synthesizing a click on surrounding presentation markup.
                await source_element.set_input_files(upload_payload)
                return source_element, "local_source_input"
            chooser = await _chooser_from_click(
                page, source, timeout_ms=FILE_CHOOSER_SOURCE_TIMEOUT_MS
            )
        await chooser.set_files(upload_payload)
        return chooser.element, "file_chooser"
    finally:
        try:
            await trigger.evaluate(
                "(element, attribute) => element.removeAttribute(attribute)", marker
            )
        except Exception:
            pass


async def _chooser_from_click(page: Page, control: Locator, *, timeout_ms: int) -> FileChooser:
    shield = page.locator(f"#{AUTOMATION_SHIELD_ID}")
    shield_pointer_events: str | None = None
    try:
        if await shield.count() == 1:
            shield_pointer_events = await shield.evaluate(
                "element => element.style.pointerEvents"
            )
            await shield.evaluate("element => { element.style.pointerEvents = 'none'; }")
        async with page.expect_file_chooser(timeout=timeout_ms) as chooser_info:
            await control.click(force=True)
        return await chooser_info.value
    finally:
        if shield_pointer_events is not None and await shield.count() == 1:
            await shield.evaluate(
                "(element, value) => { element.style.pointerEvents = value; }",
                shield_pointer_events,
            )


async def _local_file_source_control(page: Page) -> Locator | None:
    """Find the single visible control that opens a local-device file chooser.

    The wording is intentionally about the local source rather than a particular ATS. It
    excludes cloud-provider choices, and ambiguity keeps the upload blocked instead of
    guessing which external destination the candidate intended.
    """

    marker = "data-jaa-local-file-source"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FILE_CHOOSER_SOURCE_TIMEOUT_MS / 1_000
    while True:
        await page.locator(f"[{marker}]").evaluate_all(
            "(elements, attribute) => elements.forEach(element => element.removeAttribute(attribute))",
            marker,
        )
        found = await page.evaluate(
            r"""marker => {
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const referencedText = (element, attribute) => clean(
            (element.getAttribute(attribute) || '').split(/\s+/)
              .map(id => document.getElementById(id)?.textContent || '').join(' ')
          );
          const nativeLabels = element => clean(
            Array.from(element.labels || []).map(label => label.textContent || '').join(' ')
          );
          const rendered = element => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' &&
              rect.width > 0 && rect.height > 0;
          };
          const usableSourceControl = element => {
            if (!rendered(element)) return false;
            const style = getComputedStyle(element);
            if (element.matches('input[type="file"]')) {
              // Many upload widgets deliberately place a transparent native input
              // over a visible local-device label. It remains the real, interactive
              // browser control and is safer to target than guessing at its wrapper.
              return style.pointerEvents !== 'none';
            }
            return Number(style.opacity || 1) !== 0;
          };
          const localSource = /\b(?:upload\s+(?:from\s+)?(?:device|computer|this computer|local)|choose\s+(?:a\s+)?file|select\s+(?:a\s+)?file|browse\s+(?:for\s+)?files?|from\s+(?:this\s+)?(?:device|computer)|vom\s+(?:gerät|computer)|von\s+diesem\s+computer|datei\s+auswählen|lokale\s+datei)\b/i;
          const cloudSource = /\b(?:dropbox|google(?: drive)?|onedrive|box\.com|icloud)\b/i;
          const candidates = Array.from(document.querySelectorAll(
            'button, [role="button"], input[type="button"], input[type="submit"], '
            + 'input[type="file"]:not(:disabled)'
          )).filter(element => {
            const text = clean(
              referencedText(element, 'aria-labelledby') ||
              element.getAttribute('aria-label') || nativeLabels(element) ||
              element.getAttribute('title') || element.value ||
              element.innerText || element.textContent
            );
            return usableSourceControl(element) && localSource.test(text) &&
              !cloudSource.test(text);
          });
          const controls = candidates.filter(element => !candidates.some(
            other => other !== element && element.contains(other)
          ));
          if (controls.length !== 1) return false;
          controls[0].setAttribute(marker, 'active');
          return true;
        }""",
            marker,
        )
        if found:
            return page.locator(f"[{marker}='active']")
        if loop.time() >= deadline:
            return None
        await page.wait_for_timeout(100)


async def _set_file_payload(
    page: Page, locator: Locator, upload_payload: dict[str, Any]
) -> tuple[ElementHandle, str]:
    replacement = await _replace_existing_file(page, locator, upload_payload)
    if replacement is not None:
        return replacement
    element = await locator.element_handle(timeout=5_000)
    if element is None:
        raise ValueError("The file-upload control is no longer present")
    if not await element.evaluate("element => element.matches('input[type=file]')"):
        raise FileUploadError(
            "file_source_unavailable",
            "The employer's local-device file option did not become available",
        )
    # Keep the element handle for verification. React-based upload widgets frequently replace
    # the input immediately after their change handler runs, invalidating its temporary selector.
    await element.set_input_files(upload_payload)
    return element, "native_input"


async def _live_file_input(page: Page, field: DetectedField) -> Locator:
    locator = page.locator(field.selector)
    if await locator.count() == 1:
        container_marker = "data-jaa-live-file-container"
        await page.locator(f"[{container_marker}]").evaluate_all(
            "(elements, marker) => elements.forEach(element => element.removeAttribute(marker))",
            container_marker,
        )
        await locator.evaluate(
            r"""(input, marker) => {
              const container = input.closest(
                '[data-testid*="field" i], [class*="form-field" i], '
                + '[class*="field-wrapper" i], [class*="input-wrapper" i], [class*="field-"]'
              ) || input.closest('section, fieldset') || input.parentElement;
              container?.setAttribute(marker, 'active');
            }""",
            container_marker,
        )
        return locator

    marker = "data-jaa-live-file-input"
    container_marker = "data-jaa-live-file-container"
    matched = await page.evaluate(
        r"""({question, marker, containerMarker}) => {
          document.querySelectorAll(`[${marker}]`).forEach(
            element => element.removeAttribute(marker)
          );
          document.querySelectorAll(`[${containerMarker}]`).forEach(
            element => element.removeAttribute(containerMarker)
          );
          const scope = document.querySelector(
            "form[data-jaa-application-form='active']"
          ) || document;
          // A profile form can be nested beside its document widget.  When a reactive
          // portal replaced the marked input, looking only inside that form would make
          // a legitimate résumé picker disappear.  We still bind only to one input
          // whose own label/widget matches the reviewed résumé question below.
          const candidates = Array.from(document.querySelectorAll(
            'input[type="file"]:not(:disabled)'
          ));
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const normalized = value => clean(value).normalize('NFKC').toLocaleLowerCase();
          const referencedText = (element, attribute) => clean(
            (element.getAttribute(attribute) || '').split(/\s+/)
              .map(id => document.getElementById(id)?.textContent || '').join(' ')
          );
          const explicitLabel = element => element.id
            ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
            : null;
          const nativeLabels = element => clean(
            Array.from(element.labels || []).map(label => label.textContent || '').join(' ')
          );
          const fieldContainer = element => element.closest(
            '[data-testid*="field" i], [class*="form-field" i], [class*="field-wrapper" i], '
            + '[class*="input-wrapper" i], [class*="field-"]'
          );
          const accessibleName = element => clean(
            referencedText(element, 'aria-labelledby') || element.getAttribute('aria-label') ||
            nativeLabels(element) || explicitLabel(element)?.textContent ||
            element.closest('label')?.textContent
          );
          const questionFor = element => {
            const container = fieldContainer(element);
            const contextual = container?.querySelector(
              'label, [id*="label" i], [class*="label" i]'
            );
            const nearby = element.parentElement?.querySelector(
              ':scope > label, :scope > .label, :scope > p'
            );
            return clean(
              accessibleName(element) || contextual?.textContent || nearby?.textContent ||
              element.getAttribute('name') || element.id
            );
          };

          const wanted = normalized(question);
          let matches = candidates.filter(element => normalized(questionFor(element)) === wanted);
          if (matches.length !== 1) {
            matches = candidates.filter(element => {
              const context = normalized(fieldContainer(element)?.textContent);
              return wanted && context.includes(wanted);
            });
          }
          if (matches.length !== 1) return false;
          matches[0].setAttribute(marker, 'active');
          const container = fieldContainer(matches[0]) ||
            matches[0].closest('section, fieldset') || matches[0].parentElement;
          container?.setAttribute(containerMarker, 'active');
          return true;
        }""",
        {
            "question": field.question,
            "marker": marker,
            "containerMarker": container_marker,
        },
    )
    if not matched:
        raise ValueError(f"The file-upload control is no longer present: {field.question}")
    return page.locator(f"[{marker}='active']")


async def _wait_for_file_upload_confirmation(
    page: Page, field: DetectedField, expected_filename: str
) -> tuple[str, dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FILE_UPLOAD_CONFIRMATION_TIMEOUT_MS / 1_000
    stable_since: float | None = None
    stable_signal: str | None = None
    while loop.time() < deadline:
        observation = await _file_upload_observation(page, field, expected_filename)
        if await has_employer_consent_dialog(page):
            observation["employer_consent_required"] = True
            raise FileUploadUserActionRequired(
                "employer_consent_required",
                "The employer requires a privacy or data-use decision before the résumé upload can finish.",
                safe_details=_safe_upload_observation(observation),
            )
        signal: str | None = None
        if observation["ui_has_filename"] and not observation["busy"]:
            signal = "employer_widget"
        elif (
            observation["input_visible"]
            and observation["files"] == [expected_filename]
            and not observation["busy"]
        ):
            signal = "native_input"

        now = loop.time()
        if signal is None:
            stable_since = None
            stable_signal = None
        elif signal != stable_signal:
            stable_since = now
            stable_signal = signal
        elif stable_since is not None and (
            now - stable_since
        ) * 1_000 >= FILE_UPLOAD_STABILITY_MS:
            return signal, observation
        await page.wait_for_timeout(100)

    raise FileUploadError(
        "confirmation_timeout",
        "The employer portal did not confirm the résumé upload. The stored résumé is unchanged.",
        safe_details=_safe_upload_observation(observation),
    )


async def confirmed_existing_file_upload(
    page: Page,
    field: DetectedField,
    expected_filename: str,
    *,
    wait_for_presence: bool = False,
) -> FileUploadResult | None:
    """Return a confirmed employer-side upload without selecting the file again.

    Privacy/consent widgets often complete an upload after ``fill_field`` has paused for
    the candidate's decision.  A subsequent full-form scan must reuse that completed
    upload; selecting the file again can reopen the consent dialog or replace a valid
    employer draft.
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + FILE_UPLOAD_CONFIRMATION_TIMEOUT_MS / 1_000
    while True:
        observation = await _file_upload_observation(page, field, expected_filename)
        file_is_present = observation["ui_has_filename"] or observation["files"] == [
            expected_filename
        ]
        if file_is_present:
            break
        if not wait_for_presence or loop.time() >= deadline:
            return None
        await page.wait_for_timeout(100)
    confirmation_signal, observation = await _wait_for_file_upload_confirmation(
        page, field, expected_filename
    )
    return FileUploadResult(
        strategy="existing_employer_widget",
        confirmation_signal=confirmation_signal,
        safe_details=_safe_upload_observation(observation),
    )


def _safe_upload_observation(observation: dict[str, Any]) -> dict[str, Any]:
    files = observation.get("files") or []
    return {
        "input_found": bool(observation.get("input_found")),
        "input_visible": bool(observation.get("input_visible")),
        "input_file_count": len(files),
        "ui_has_filename": bool(observation.get("ui_has_filename")),
        "widget_busy": bool(observation.get("busy")),
        "employer_consent_required": bool(
            observation.get("employer_consent_required")
        ),
    }


async def has_employer_consent_dialog(page: Page) -> bool:
    dialogs = page.locator(
        '[role="dialog"]:visible, [aria-modal="true"]:visible, dialog[open]'
    )
    consent_markers = (
        "privacy",
        "data protection",
        "consent",
        "datenschutz",
        "datenschutzhinweise",
        "einwillig",
        "stimme zu",
        "confidentialité",
        "protection des données",
        "consentement",
        "privacidad",
        "protección de datos",
        "consentimiento",
        "protezione dei dati",
        "consenso",
        "gegevensbescherming",
        "toestemming",
    )
    decision_markers = (
        "agree",
        "accept",
        "consent",
        "cancel",
        "stimme zu",
        "zustimmen",
        "abbrechen",
        "accepter",
        "annuler",
        "aceptar",
        "cancelar",
        "accetto",
        "annulla",
        "akkoord",
        "annuleren",
    )
    for index in range(await dialogs.count()):
        dialog = dialogs.nth(index)
        text = " ".join((await dialog.inner_text()).split()).casefold()
        if not any(marker in text for marker in consent_markers):
            continue
        controls = dialog.locator("button, [role='button'], input[type='button'], input[type='submit']")
        control_text = " ".join(await controls.all_inner_texts()).casefold()
        if any(marker in control_text for marker in decision_markers):
            return True
    return False


async def _file_upload_observation(
    page: Page, field: DetectedField, expected_filename: str
) -> dict[str, Any]:
    return await page.evaluate(
        r"""({question, filename, selector}) => {
          const formScope = document.querySelector(
            "form[data-jaa-application-form='active']"
          ) || document;
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const normalized = value => clean(value).normalize('NFKC').toLocaleLowerCase();
          const referencedText = (element, attribute) => clean(
            (element.getAttribute(attribute) || '').split(/\s+/)
              .map(id => document.getElementById(id)?.textContent || '').join(' ')
          );
          const explicitLabel = element => element.id
            ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)
            : null;
          const nativeLabels = element => clean(
            Array.from(element.labels || []).map(label => label.textContent || '').join(' ')
          );
          const fieldContainer = element => element?.closest(
            '[data-testid*="field" i], [class*="form-field" i], [class*="field-wrapper" i], '
            + '[class*="input-wrapper" i], [class*="field-"]'
          );
          const uploadContainer = element => fieldContainer(element) || element?.closest(
            'section, fieldset, [class*="upload" i], [class*="document" i]'
          );
          const accessibleName = element => clean(
            referencedText(element, 'aria-labelledby') || element.getAttribute('aria-label') ||
            nativeLabels(element) || explicitLabel(element)?.textContent ||
            element.closest('label')?.textContent
          );
          const questionFor = element => {
            const container = uploadContainer(element);
            const contextual = container?.querySelector(
              'label, [id*="label" i], [class*="label" i]'
            );
            const nearby = element.parentElement?.querySelector(
              ':scope > label, :scope > .label, :scope > p'
            );
            return clean(
              accessibleName(element) || contextual?.textContent || nearby?.textContent ||
              element.getAttribute('name') || element.id
            );
          };

          const wanted = normalized(question);
          const expected = normalized(filename);
          const allInputs = Array.from(document.querySelectorAll(
            'input[type="file"]:not(:disabled)'
          ));
          const matchingInputs = candidates => {
            let matches = candidates.filter(
              element => normalized(questionFor(element)) === wanted
            );
            if (matches.length === 1) return matches;
            matches = candidates.filter(element => {
              const context = normalized(uploadContainer(element)?.textContent);
              return wanted && context.includes(wanted);
            });
            return matches;
          };
          const markedControl = selector ? document.querySelector(selector) : null;
          const markedInput = markedControl?.matches('input[type="file"]:not(:disabled)')
            ? markedControl
            : null;
          const formInputs = formScope === document
            ? allInputs
            : allInputs.filter(element => formScope.contains(element));
          let inputMatches = markedInput
            ? [markedInput]
            : matchingInputs(formInputs);
          if (inputMatches.length !== 1 && formScope !== document) {
            inputMatches = matchingInputs(allInputs);
          }
          const input = inputMatches.length === 1 ? inputMatches[0] : null;
          const scope = input && formScope !== document && !formScope.contains(input)
            ? document
            : formScope;
          let container = scope.querySelector(
            "[data-jaa-live-file-container='active']"
          );
          if (!container || !container.isConnected) {
            container = uploadContainer(input) || uploadContainer(markedControl) || null;
          }
          if (!container) {
            const containers = Array.from(scope.querySelectorAll(
              'section, fieldset, div, [data-testid*="field" i], [class*="form-field" i], '
              + '[class*="field-wrapper" i], [class*="input-wrapper" i], [class*="field-"]'
            )).filter(element => {
              const candidateText = normalized(element.textContent);
              return candidateText.includes(wanted) && (
                !expected || candidateText.includes(expected)
              );
            });
            containers.sort((left, right) =>
              clean(left.textContent).length - clean(right.textContent).length
            );
            container = containers[0] || null;
          }
          const text = normalized(container?.textContent);
          const style = input ? getComputedStyle(input) : null;
          const rect = input?.getBoundingClientRect();
          const inputVisible = Boolean(
            input && !input.hidden && style?.display !== 'none' &&
            style?.visibility !== 'hidden' && rect && rect.width > 0 && rect.height > 0
          );
          const busy = Boolean(
            container?.matches('[aria-busy="true"]') ||
            container?.querySelector('[aria-busy="true"], [role="progressbar"], progress')
          );
          return {
            input_found: Boolean(input),
            files: input
              ? Array.from(input.files || []).map(file => file.name)
              : [],
            input_visible: inputVisible,
            ui_has_filename: Boolean(
              expected && (text.includes(expected) || normalized(scope.textContent).includes(expected))
            ),
            busy,
          };
        }""",
        {
            "question": field.question,
            "filename": expected_filename,
            "selector": field.selector,
        },
    )


async def _fill_combobox(page: Page, locator: Locator, answer: str) -> None:
    original = await locator.input_value()
    await _open_custom_control(locator)
    try:
        await locator.fill(answer)
    except Exception:
        pass
    await _open_custom_control(locator)
    await page.wait_for_timeout(150)
    options = await _controlled_options(page, locator)
    wanted = answer.strip().casefold()
    for index, label in enumerate(await options.all_inner_texts()):
        if " ".join(label.split()).casefold() == wanted:
            await options.nth(index).evaluate("element => element.click()")
            await page.wait_for_timeout(50)
            return
    current = (await locator.input_value()).strip().casefold()
    controlled = (await locator.get_attribute("aria-controls") or "").strip()
    if current == wanted and not controlled and not await options.count():
        await locator.press("Tab")
        return
    await page.keyboard.press("Escape")
    await locator.evaluate(
        """(element, value) => {
          const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value'
          )?.set;
          setter?.call(element, value);
          element.dispatchEvent(new Event('input', {bubbles: true}));
          element.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        original,
    )
    await page.keyboard.press("Escape")
    raise ValueError("The requested combobox option is not present")


async def _open_custom_control(locator: Locator) -> None:
    await locator.evaluate(
        """element => {
          element.focus({preventScroll: true});
          element.click();
        }"""
    )


async def _controlled_options(page: Page, locator: Locator) -> Locator:
    for _ in range(25):
        associated = await associated_option_locator(page, locator)
        if associated is not None and await associated.count():
            return associated
        await page.wait_for_timeout(100)
    return page.locator("[data-jaa-no-associated-options]")


def _parse_date_answer(answer: str) -> date:
    value = answer.strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ValueError("Date answers must use YYYY-MM-DD or a recognized local date format")


def _normalized_date_value(value: str) -> str:
    if not value.strip():
        return ""
    try:
        return _parse_date_answer(value).isoformat()
    except ValueError:
        return value.strip()


async def _fill_date(page: Page, locator: Locator, answer: str) -> None:
    target = _parse_date_answer(answer)
    input_type = (await locator.get_attribute("type") or "text").casefold()
    if input_type == "date":
        await locator.fill(target.isoformat())
        return

    readonly = await locator.get_attribute("readonly") is not None
    if not readonly:
        placeholder = (
            (await locator.get_attribute("placeholder"))
            or (await locator.get_attribute("aria-label"))
            or ""
        ).casefold()
        formatted = (
            target.strftime("%d.%m.%Y")
            if "tt" in placeholder or "dd.mm" in placeholder
            else target.isoformat()
        )
        await locator.fill(formatted)
        return

    current_raw = await locator.input_value()
    try:
        current = _parse_date_answer(current_raw) if current_raw else date.today()
    except ValueError:
        current = date.today()
    await _open_custom_control(locator)
    await page.wait_for_timeout(100)
    dialog = await _date_dialog(page, locator)
    if dialog is None:
        raise ValueError("The date picker did not open")

    year_direction = "next" if target.year > current.year else "previous"
    direct_year_navigation = True
    for step in range(abs(target.year - current.year)):
        try:
            await _click_calendar_navigation(page, locator, year_direction, "year")
        except ValueError:
            if step:
                raise
            direct_year_navigation = False
            break
    month_delta = (
        target.month - current.month
        if direct_year_navigation
        else (target.year - current.year) * 12 + target.month - current.month
    )
    month_direction = "next" if month_delta > 0 else "previous"
    for _ in range(abs(month_delta)):
        await _click_calendar_navigation(page, locator, month_direction, "month")

    dialog = await _date_dialog(page, locator)
    if dialog is None:
        raise ValueError("The employer calendar closed before a date was selected")
    cells = dialog.locator("[role='gridcell']")
    target_iso = target.isoformat()
    index = await cells.evaluate_all(
        """(elements, expected) => elements.findIndex(element =>
          [element.getAttribute('title'), element.getAttribute('data-date'),
           element.getAttribute('data-value')].includes(expected)
        )""",
        target_iso,
    )
    if index < 0:
        raise ValueError("The requested date is not available in the employer calendar")
    await cells.nth(index).evaluate(
        "element => (element.querySelector('[role=button], button') || element).click()"
    )
    await page.wait_for_timeout(100)
    if _normalized_date_value(await locator.input_value()) != target_iso:
        raise ValueError("The employer date picker did not accept the requested date")


async def _date_dialog(page: Page, locator: Locator) -> Locator | None:
    controlled_ids = ((await locator.get_attribute("aria-controls")) or "").split()
    for controlled_id in controlled_ids:
        controlled = page.locator(f"[id={json.dumps(controlled_id)}][role='dialog']:visible")
        if await controlled.count() == 1:
            return controlled
    dialogs = page.locator("[role='dialog']:visible").filter(
        has=page.locator("[role='grid'], [role='gridcell']")
    )
    return dialogs if await dialogs.count() == 1 else None


async def _click_calendar_navigation(
    page: Page, locator: Locator, direction: str, unit: str
) -> None:
    dialog = await _date_dialog(page, locator)
    if dialog is None:
        raise ValueError("The employer date picker is no longer available")
    buttons = dialog.locator("button[aria-label]")
    labels = [
        (await buttons.nth(index).get_attribute("aria-label") or "").casefold()
        for index in range(await buttons.count())
    ]
    direction_tokens = {
        "previous": ("previous", "prev", "vorher", "zurück", "preced", "anterior", "vorig"),
        "next": ("next", "nächst", "suivant", "siguiente", "prossim", "volgend"),
    }[direction]
    unit_tokens = {
        "year": ("year", "jahr", "année", "annee", "año", "ano", "anno", "jaar"),
        "month": ("month", "monat", "mois", "mes", "mese", "maand"),
    }[unit]
    index = next(
        (
            position
            for position, label in enumerate(labels)
            if any(token in label for token in direction_tokens)
            and any(token in label for token in unit_tokens)
        ),
        None,
    )
    if index is None:
        raise ValueError(f"The employer calendar has no {direction} {unit} control")
    await buttons.nth(index).evaluate("element => element.click()")
    await page.wait_for_timeout(20)


async def clear_field(page: Page, field: DetectedField) -> None:
    if field.field_type == "file":
        locator = await _live_file_input(page, field)
        await locator.set_input_files([])
        return
    live = await rediscover_field(page, field)
    field.selector = live.selector
    locator = page.locator(field.selector)
    if field.field_type == "checkbox":
        await locator.evaluate(
            "element => { if (element.checked) element.click(); }"
        )
    elif field.field_type == "radio":
        await locator.evaluate_all(
            """elements => elements.forEach(element => {
              element.checked = false;
              element.dispatchEvent(new Event('input', {bubbles: true}));
              element.dispatchEvent(new Event('change', {bubbles: true}));
            })"""
        )
    elif field.field_type == "select":
        await locator.evaluate(
            """element => {
              element.selectedIndex = -1;
              element.dispatchEvent(new Event('input', {bubbles: true}));
              element.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
    elif field.field_type == "date":
        if await locator.get_attribute("readonly") is None:
            await locator.fill("")
        else:
            await locator.evaluate(
                """element => {
                  const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                  )?.set;
                  setter?.call(element, '');
                  element.dispatchEvent(new Event('input', {bubbles: true}));
                  element.dispatchEvent(new Event('change', {bubbles: true}));
                }"""
            )
    else:
        await locator.fill("")
    await _blur_if_present(page, field)
    if not await _wait_for_blank_state(page, field):
        raise ValueError("The employer field did not remain blank")


async def _wait_for_blank_state(
    page: Page,
    field: DetectedField,
    *,
    attempts: int = 20,
) -> bool:
    consecutive_matches = 0
    for _ in range(attempts):
        try:
            live = await rediscover_field(page, field)
            field.selector = live.selector
            if state_is_blank(live, await capture_field_state(page, live)):
                consecutive_matches += 1
                if consecutive_matches >= 2:
                    return True
            else:
                consecutive_matches = 0
        except (PlaywrightError, ValueError):
            consecutive_matches = 0
        await page.wait_for_timeout(100)
    return False


async def capture_field_state(page: Page, field: DetectedField) -> dict[str, Any]:
    locator = page.locator(field.selector)
    if field.field_type == "file":
        locator = await _live_file_input(page, field)
        return await locator.evaluate(
            "element => ({files: Array.from(element.files || []).map(file => file.name)})"
        )
    if not await locator.count():
        raise ValueError(f"Field is no longer present: {field.question}")
    if field.field_type == "select":
        return await locator.evaluate(
            r"""element => ({
              value: element.value,
              label: element.selectedOptions?.[0]?.textContent?.replace(/\s+/g, ' ').trim() || '',
              valid: element.getAttribute('aria-invalid') !== 'true' &&
                (element.validity == null || element.validity.valid)
            })"""
        )
    if field.field_type == "combobox":
        return await locator.evaluate(
            """element => ({
              value: element.value,
              valid: element.getAttribute('aria-invalid') !== 'true' &&
                (element.validity == null || element.validity.valid)
            })"""
        )
    if field.field_type == "date":
        raw = await locator.evaluate(
            """element => ({
              display: element.value,
              valid: element.getAttribute('aria-invalid') !== 'true' &&
                (element.validity == null || element.validity.valid)
            })"""
        )
        display = str(raw.get("display") or "")
        return {
            "value": _normalized_date_value(display),
            "display": display,
            "valid": raw.get("valid", True),
        }
    if field.field_type == "radio":
        return await locator.evaluate_all(
            r"""elements => {
              const selected = elements.find(element => element.checked);
              const valid = elements.every(element =>
                element.getAttribute('aria-invalid') !== 'true' &&
                (element.validity == null || element.validity.valid)
              );
              if (!selected) return {value: null, label: null, valid};
              const explicit = selected.id
                ? document.querySelector(`label[for="${CSS.escape(selected.id)}"]`)
                : null;
              return {
                value: selected.value,
                label: (explicit?.textContent || selected.closest('label')?.textContent || selected.value)
                  .replace(/\s+/g, ' ').trim(),
                valid
              };
            }"""
        )
    if field.field_type == "checkbox":
        return await locator.evaluate(
            """element => ({
              checked: Boolean(element.checked),
              valid: element.getAttribute('aria-invalid') !== 'true' &&
                (element.validity == null || element.validity.valid)
            })"""
        )
    return await locator.evaluate(
        """element => ({
          value: element.value,
          valid: element.getAttribute('aria-invalid') !== 'true' &&
            (element.validity == null || element.validity.valid)
        })"""
    )


def state_matches_answer(field: DetectedField, answer: str, state: dict[str, Any]) -> bool:
    if state.get("valid") is False:
        return False
    expected = answer.strip().casefold()
    if field.field_type == "file":
        return bool(state.get("files"))
    if field.field_type in {"select", "radio"}:
        if field.field_type == "select" and not str(state.get("value") or "").strip():
            return False
        return expected in {
            str(state.get("value") or "").strip().casefold(),
            str(state.get("label") or "").strip().casefold(),
        }
    if field.field_type == "checkbox":
        positive = expected in {"yes", "true", "1", "checked", "on"}
        negative = expected in {"no", "false", "0", "unchecked", "off"}
        return (positive and state.get("checked") is True) or (
            negative and state.get("checked") is False
        )
    if field.field_type == "date":
        return str(state.get("value") or "") == _normalized_date_value(answer)
    return str(state.get("value") or "") == answer


def state_is_blank(field: DetectedField, state: dict[str, Any]) -> bool:
    if field.field_type == "file":
        return not state.get("files")
    if field.field_type == "radio":
        return state.get("value") is None
    if field.field_type == "checkbox":
        return state.get("checked") is False
    return not str(state.get("value") or "").strip()


def display_answer(field: DetectedField, answer: str, resume_label: str | None = None) -> str:
    if field.field_type == "file":
        return resume_label or "Selected resume"
    return answer
