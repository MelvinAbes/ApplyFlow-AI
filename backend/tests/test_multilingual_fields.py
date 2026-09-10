import json
from datetime import date
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from app.ai.field_interpreter import (
    AIFieldInterpretation,
    AIFieldInterpretationBatch,
    FieldInterpreter,
)
from app.ai.providers import StructuredAIResult
from app.automation.browser import BrowserManager
from app.automation.field_detector import DetectedField, detect_fields, mark_application_form
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
from app.models import Application, ApplicationField, Job, Resume
from app.models.enums import AnswerSource, ApplicationStatus
from app.schemas.profile import CandidateProfileInput
from app.services.application_service import (
    ApplicationService,
    ApplicationStateError,
    FieldUpdateError,
)
from app.services.job_service import description_hash
from app.services.profile_service import ProfileService


class FakeTranslationProvider:
    name = "fake"
    model = "translation-test"
    cache_identity = "fake:translation-test"

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def generate(self, *, instructions, prompt, output_model):
        self.calls += 1
        self.prompts.append(prompt)
        payload = json.loads(prompt)
        fields = [
            AIFieldInterpretation(
                input_index=item["input_index"],
                source_language="de",
                english_question="Which office community would you prefer?",
                canonical_key=None,
                english_options=["Technology", "Financial services"],
                confidence=0.94,
            )
            for item in payload["fields"]
        ]
        value = AIFieldInterpretationBatch(fields=fields)
        return StructuredAIResult(
            value=output_model.model_validate(value.model_dump()),
            provider=self.name,
            model=self.model,
            output_characters=120,
        )


@pytest.mark.asyncio
async def test_deloitte_style_accessible_labels_combobox_and_hidden_resume_are_detected(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "deloitte_multilingual_form.html"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(fixture.as_uri())
            fields = await detect_fields(page, page.locator("#deloitte-application"))
            by_question = {field.question: field for field in fields}

            assert "Weitere Angaben" not in by_question
            assert by_question["Lebenslauf"].field_type == "file"
            assert by_question["Lebenslauf"].required
            assert by_question["Geburtsdatum"].field_type == "date"
            assert by_question["Geburtsdatum"].required
            assert by_question["Telefon"].field_type == "text"
            assert by_question["Straße und Hausnummer"].field_type == "text"
            attachments = by_question[
                "Upload der Zeugnisse (verpflichtend für Ausbildung und Duales Studium)"
            ]
            assert attachments.field_type == "file"
            assert not attachments.required
            location = by_question["Standortwahl Priorität 1"]
            assert location.field_type == "combobox"
            assert location.options == ["Berlin", "Frankfurt", "München"]
            other_roles = by_question["Dürfen wir dich auch für weitere offene Stellen prüfen?"]
            assert other_roles.field_type == "radio"
            assert other_roles.options == [
                "Ja, weitere offene Stellen",
                "Nein, nur die beworbene Stelle",
            ]

            interpreted = await FieldInterpreter(session, test_settings).interpret(fields)
            semantic = {field.question: field for field in interpreted}
            assert semantic["Lebenslauf"].canonical_key == "resume"
            assert semantic["Geburtsdatum"].translated_question == "Date of birth"
            assert semantic["Telefon"].translated_question == "Phone number"
            assert (
                semantic["Straße und Hausnummer"].translated_question == "Street and house number"
            )
            assert (
                semantic[attachments.question].translated_question
                == "Certificates upload (required for apprenticeships and dual-study programs)"
            )
            assert (
                semantic["Standortwahl Priorität 1"].translated_question
                == "Preferred location — first choice"
            )
            assert semantic[other_roles.question].canonical_key == "consider_other_positions"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_custom_combobox_can_be_filled_and_optional_checkbox_cleared() -> None:
    fixture = Path(__file__).parent / "fixtures" / "deloitte_multilingual_form.html"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(fixture.as_uri())
            fields = await detect_fields(page, page.locator("#deloitte-application"))
            by_question = {field.question: field for field in fields}

            location = by_question["Standortwahl Priorität 1"]
            await fill_field(page, location, "Frankfurt")
            assert (await capture_field_state(page, location))["value"] == "Frankfurt"

            birth_date = by_question["Geburtsdatum"]
            await fill_field(page, birth_date, "1990-05-17")
            birth_state = await capture_field_state(page, birth_date)
            assert birth_state == {
                "value": "1990-05-17",
                "display": "17.05.1990",
                "valid": True,
            }
            assert state_matches_answer(birth_date, "1990-05-17", birth_state)

            save_answers = by_question["Meine Antworten für zukünftige Anwendungen speichern"]
            assert (await capture_field_state(page, save_answers))["checked"] is True
            await clear_field(page, save_answers)
            state = await capture_field_state(page, save_answers)
            assert state_is_blank(save_answers, state)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_generic_portal_widgets_preserve_employer_control_contracts() -> None:
    fixture = Path(__file__).parent / "fixtures" / "generic_widget_form.html"
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(fixture.as_uri())
            fields = await detect_fields(page, page.locator("#application"))
            by_question = {field.question: field for field in fields}

            expected_types = {
                "Email address": "email",
                "Available from": "date",
                "Office": "select",
                "Work arrangement": "radio",
                "Send application updates": "checkbox",
                "Motivation": "textarea",
                "Working language": "combobox",
            }
            assert {
                question: by_question[question].field_type for question in expected_types
            } == expected_types
            assert by_question["Office"].options == ["Berlin", "Hamburg"]
            assert by_question["Work arrangement"].options == ["Hybrid", "On site"]
            assert by_question["Working language"].options == ["English", "German"]

            answers = {
                "Email address": "candidate@example.com",
                "Available from": "2026-09-01",
                "Office": "Hamburg",
                "Work arrangement": "Hybrid",
                "Send application updates": "Yes",
                "Motivation": "A truthful motivation answer.",
                "Working language": "German",
            }
            for question, answer in answers.items():
                field = by_question[question]
                await fill_field(page, field, answer)
                assert state_matches_answer(field, answer, await capture_field_state(page, field))
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_existing_attachment_is_replaced_through_file_chooser_with_original_name(
    tmp_path,
) -> None:
    stored_resume = tmp_path / "internal-storage-id.pdf"
    stored_resume.write_bytes(b"%PDF-1.4\n% named upload fixture")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <section class="file-upload">
                    <label id="resume-label">Resume</label>
                    <input id="resume" type="file" aria-labelledby="resume-label" hidden>
                    <span id="filename">internal-storage-id.pdf</span>
                    <button id="replace" type="button">Replace</button>
                  </section>
                </form>
                <div id="jaa-automation-interaction-shield" style="position:fixed;inset:0;z-index:9999;pointer-events:all"></div>
                <script>
                  const input = document.querySelector('#resume')
                  document.querySelector('#replace').addEventListener('click', () => input.click())
                  input.addEventListener('change', () => {
                    document.querySelector('#filename').textContent = input.files[0]?.name || ''
                  })
                </script>
                """
            )
            field = (await detect_fields(page, page.locator("#application")))[0]

            result = await fill_field(
                page,
                field,
                "Selected resume",
                upload_path=stored_resume,
                upload_name="Ada_Lovelace_CV.pdf",
            )

            assert await page.locator("#filename").inner_text() == "Ada_Lovelace_CV.pdf"
            assert (await capture_field_state(page, field))["files"] == ["Ada_Lovelace_CV.pdf"]
            assert result == FileUploadResult(
                strategy="file_chooser",
                confirmation_signal="employer_widget",
                safe_details={
                    "input_found": True,
                    "input_visible": False,
                    "input_file_count": 1,
                    "ui_has_filename": True,
                    "widget_busy": False,
                    "employer_consent_required": False,
                    "write_request_count": 0,
                    "write_response_count": 0,
                    "http_4xx_count": 0,
                    "http_5xx_count": 0,
                    "failed_write_request_count": 0,
                },
            )
            assert await page.locator("#resume").evaluate(
                "element => element.files[0].type"
            ) == "application/pdf"
            assert "named upload fixture" in await page.locator("#resume").evaluate(
                "element => element.files[0].text()"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_reactive_portal_file_input_is_rediscovered_after_rerender(tmp_path) -> None:
    stored_resume = tmp_path / "internal-storage-id.pdf"
    stored_resume.write_bytes(b"%PDF-1.4\n% reactive upload fixture")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <div class="field-resume">
                    <div class="field-label">Lade deinen Lebenslauf hoch</div>
                    <span class="upload-resume-dropzone">
                      <input type="file" accept=".docx,.doc,.pdf" hidden>
                      <button type="button">Datei auswählen</button>
                      <span class="filename"></span>
                    </span>
                  </div>
                  <div class="field-attachments">
                    <div class="field-label">Upload der Zeugnisse</div>
                    <span class="file-upload-resume">
                      <input type="file" accept=".pdf,.doc,.docx" hidden>
                      <button type="button">Dateien auswählen</button>
                    </span>
                  </div>
                </form>
                <script>
                  const installResumeHandler = input => input.addEventListener('change', () => {
                    document.querySelector('.field-resume .filename').textContent =
                      input.files[0]?.name || ''
                  })
                  installResumeHandler(document.querySelector('.field-resume input'))
                  document.querySelector('.field-resume button').addEventListener(
                    'click', () => document.querySelector('.field-resume input').click()
                  )
                  window.rerenderResumeUpload = () => {
                    const current = document.querySelector('.field-resume input')
                    const replacement = current.cloneNode()
                    replacement.removeAttribute('data-jaa-field')
                    current.replaceWith(replacement)
                    installResumeHandler(replacement)
                  }
                </script>
                """
            )
            fields = await detect_fields(page, page.locator("#application"))
            resume = next(
                field for field in fields if field.question == "Lade deinen Lebenslauf hoch"
            )

            # Eightfold's React component can replace the hidden input after field discovery,
            # which removes the temporary data-jaa-field marker used by the original locator.
            await page.evaluate("window.rerenderResumeUpload()")
            assert await page.locator(resume.selector).count() == 0

            result = await fill_field(
                page,
                resume,
                "Selected resume",
                upload_path=stored_resume,
                upload_name="Ada_Lovelace_CV.pdf",
            )

            assert await page.locator(".field-resume .filename").inner_text() == (
                "Ada_Lovelace_CV.pdf"
            )
            assert await page.locator(".field-resume input").evaluate(
                "element => element.files[0].name"
            ) == "Ada_Lovelace_CV.pdf"
            assert await page.locator(".field-attachments input").evaluate(
                "element => element.files.length"
            ) == 0
            assert isinstance(result, FileUploadResult)
            assert result.strategy == "file_chooser"
            assert result.confirmation_signal == "employer_widget"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_transient_file_list_does_not_count_as_confirmed_upload(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.automation.form_filler.FILE_UPLOAD_CONFIRMATION_TIMEOUT_MS", 500
    )
    monkeypatch.setattr("app.automation.form_filler.FILE_UPLOAD_STABILITY_MS", 150)
    stored_resume = tmp_path / "internal-storage-id.pdf"
    stored_resume.write_bytes(b"%PDF-1.4\n% rejected upload fixture")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <section>
                    <label id="resume-label">Resume upload</label>
                    <input id="resume" type="file" aria-labelledby="resume-label" hidden>
                  </section>
                </form>
                <script>
                  const input = document.querySelector('#resume')
                  input.addEventListener('change', () => setTimeout(() => {
                    const replacement = document.createElement('input')
                    replacement.id = 'resume'
                    replacement.type = 'file'
                    replacement.hidden = true
                    replacement.setAttribute('aria-labelledby', 'resume-label')
                    input.replaceWith(replacement)
                  }, 50))
                </script>
                """
            )
            field = (await detect_fields(page, page.locator("#application")))[0]

            with pytest.raises(FileUploadError, match="did not confirm") as caught:
                await fill_field(
                    page,
                    field,
                    "Selected resume",
                    upload_path=stored_resume,
                    upload_name="Ada_Lovelace_CV.pdf",
                )

            assert await page.locator("#resume").evaluate(
                "element => element.files.length"
            ) == 0
            assert caught.value.code == "confirmation_timeout"
            assert caught.value.safe_details == {
                "input_found": True,
                "input_visible": False,
                "input_file_count": 0,
                "ui_has_filename": False,
                "widget_busy": False,
                "employer_consent_required": False,
                "write_request_count": 0,
                "write_response_count": 0,
                "http_4xx_count": 0,
                "http_5xx_count": 0,
                "failed_write_request_count": 0,
            }
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_visible_file_chooser_pauses_for_employer_privacy_consent(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("app.automation.form_filler.FILE_UPLOAD_STABILITY_MS", 100)
    stored_resume = tmp_path / "internal-storage-id.pdf"
    stored_resume.write_bytes(b"%PDF-1.4\n% consent upload fixture")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <div class="portal-upload-control">
                    <label id="resume-label">Resume</label>
                    <input id="resume" type="file" aria-labelledby="resume-label" hidden>
                    <button id="choose" type="button">Choose file</button>
                    <span id="filename"></span>
                  </div>
                  <div id="consent" role="dialog" aria-modal="true" hidden>
                    <h2>Privacy agreement</h2>
                    <p>I confirm that I have read the data protection notice.</p>
                    <button id="cancel" type="button">Cancel</button>
                    <button id="agree" type="button">I agree</button>
                  </div>
                </form>
                <script>
                  const input = document.querySelector('#resume')
                  const dialog = document.querySelector('#consent')
                  let accepted = false
                  document.querySelector('#choose').addEventListener('click', () => input.click())
                  input.addEventListener('change', () => {
                    if (!accepted) dialog.hidden = false
                    else document.querySelector('#filename').textContent = input.files[0]?.name || ''
                  })
                  document.querySelector('#agree').addEventListener('click', () => {
                    accepted = true
                    dialog.hidden = true
                    const uploadedName = input.files[0]?.name || ''
                    input.remove()
                    setTimeout(() => {
                      document.querySelector('#filename').textContent = uploadedName
                    }, 200)
                  })
                </script>
                """
            )
            field = (await detect_fields(page, page.locator("#application")))[0]

            with pytest.raises(FileUploadUserActionRequired) as caught:
                await fill_field(
                    page,
                    field,
                    "Selected resume",
                    upload_path=stored_resume,
                    upload_name="Ada_Lovelace_CV.pdf",
                )

            assert caught.value.code == "employer_consent_required"
            assert caught.value.safe_details["employer_consent_required"] is True
            assert await has_employer_consent_dialog(page) is True
            assert await page.locator("#resume").evaluate(
                "element => element.files[0].name"
            ) == "Ada_Lovelace_CV.pdf"

            await page.locator("#agree").click()

            assert await has_employer_consent_dialog(page) is False
            reconciled = await confirmed_existing_file_upload(
                page,
                field,
                "Ada_Lovelace_CV.pdf",
                wait_for_presence=True,
            )
            assert reconciled is not None
            assert reconciled.strategy == "existing_employer_widget"

            result = await fill_field(
                page,
                field,
                "Selected resume",
                upload_path=stored_resume,
                upload_name="Ada_Lovelace_CV.pdf",
            )

            assert result == FileUploadResult(
                strategy="existing_employer_widget",
                confirmation_signal="employer_widget",
                safe_details={
                    "input_found": False,
                    "input_visible": False,
                    "input_file_count": 0,
                    "ui_has_filename": True,
                    "widget_busy": False,
                    "employer_consent_required": False,
                },
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_dynamic_combobox_uses_its_owned_listbox_not_a_stale_visible_one() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <div id="stale-list" role="listbox">
                    <div role="option">Wrong stale choice</div>
                  </div>
                  <label id="gender-label">Gender</label>
                  <input id="gender" role="combobox" aria-labelledby="gender-label">
                  <div id="gender-list" role="listbox" hidden>
                    <div role="option">Female</div>
                    <div role="option">Male</div>
                    <div role="option">Diverse</div>
                  </div>
                </form>
                <script>
                  const input = document.querySelector('#gender')
                  const list = document.querySelector('#gender-list')
                  input.addEventListener('click', () => {
                    input.setAttribute('aria-controls', 'gender-list')
                    list.hidden = false
                  })
                  input.addEventListener('keydown', event => {
                    if (event.key === 'Escape') list.hidden = true
                  })
                </script>
                """
            )

            field = (await detect_fields(page, page.locator("#application")))[0]

            assert field.question == "Gender"
            assert field.options == ["Female", "Male", "Diverse"]
            assert "Wrong stale choice" not in field.options
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_delayed_combobox_options_stay_with_the_control_that_opened_them() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <div class="widget">
                    <label for="university">University</label>
                    <input id="university" role="combobox">
                  </div>
                  <div class="widget">
                    <label for="location">Preferred location</label>
                    <input id="location" role="combobox">
                  </div>
                </form>
                <script>
                  const wire = (id, values) => {
                    const input = document.querySelector(`#${id}`)
                    let requested = false
                    input.addEventListener('click', () => {
                      if (requested) return
                      requested = true
                      setTimeout(() => {
                        const list = document.createElement('div')
                        list.setAttribute('role', 'listbox')
                        values.forEach(value => {
                          const option = document.createElement('div')
                          option.setAttribute('role', 'option')
                          option.textContent = value
                          list.append(option)
                        })
                        input.parentElement.append(list)
                      }, 250)
                    })
                    input.addEventListener('keydown', event => {
                      if (event.key === 'Escape') {
                        input.parentElement.querySelector('[role="listbox"]')?.remove()
                      }
                    })
                  }
                  wire('university', ['TU Berlin', 'RWTH Aachen'])
                  wire('location', ['Berlin', 'Düsseldorf'])
                </script>
                """
            )

            fields = await detect_fields(page, page.locator("#application"))
            by_question = {field.question: field for field in fields}

            assert by_question["University"].options == ["TU Berlin", "RWTH Aachen"]
            assert by_question["Preferred location"].options == ["Berlin", "Düsseldorf"]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_question_identity_does_not_come_from_choice_text(session, test_settings) -> None:
    field = DetectedField(
        selector="#location",
        question="Standortwahl Priorität 1",
        field_type="combobox",
        options=["Darmstadt, Technische Universität Darmstadt", "Berlin"],
    )

    interpreted = await FieldInterpreter(session, test_settings).interpret([field])

    assert interpreted[0].canonical_key == "preferred_location_primary"
    assert interpreted[0].translated_question == "Preferred location — first choice"


@pytest.mark.asyncio
async def test_select_placeholder_is_not_an_answer_and_optional_select_can_be_cleared() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <label for="office">Office</label>
                  <select id="office" name="office">
                    <option value="">Bitte auswählen</option>
                    <option value="berlin">Berlin</option>
                  </select>
                </form>
                """
            )
            field = (await detect_fields(page, page.locator("#application")))[0]

            assert field.options == ["Berlin"]
            placeholder_state = await capture_field_state(page, field)
            assert not state_matches_answer(field, "Bitte auswählen", placeholder_state)
            await fill_field(page, field, "Berlin")
            await clear_field(page, field)
            assert state_is_blank(field, await capture_field_state(page, field))
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_known_german_fields_translate_without_ai(session, test_settings) -> None:
    provider = FakeTranslationProvider()
    fields = [
        DetectedField(
            selector="#postal-code",
            question="Postleitzahl",
            field_type="text",
            required=True,
        ),
        DetectedField(
            selector="#save",
            question="Meine Antworten für zukünftige Anwendungen speichern",
            field_type="checkbox",
            options=["Ja", "Nein"],
        ),
        DetectedField(
            selector="[name=other-roles]",
            question="Dürfen wir dich auch für weitere offene Stellen prüfen?",
            field_type="radio",
            options=["Ja, weitere offene Stellen", "Nein, nur die beworbene Stelle"],
        ),
    ]

    translated = await FieldInterpreter(session, test_settings, provider=provider).interpret(fields)

    assert provider.calls == 0
    assert translated[0].translated_question == "Postal code"
    assert translated[0].canonical_key == "postal_code"
    assert translated[0].source_language == "de"
    assert translated[1].translated_options == ["Yes", "No"]
    assert translated[2].canonical_key == "consider_other_positions"


@pytest.mark.asyncio
async def test_unknown_german_labels_use_label_only_ai_and_cache_result(
    session, test_settings
) -> None:
    provider = FakeTranslationProvider()
    field = DetectedField(
        selector="#community",
        question="Welche Community bevorzugst du?",
        field_type="select",
        options=["Technologie", "Finanzdienstleistungen"],
    )
    interpreter = FieldInterpreter(session, test_settings, provider=provider)

    first = await interpreter.interpret(
        [field, field.model_copy(update={"selector": "#second-community"})]
    )
    second = await interpreter.interpret([field])

    assert provider.calls == 1
    assert first[0].translated_question == "Which office community would you prefer?"
    assert first[1].translated_question == first[0].translated_question
    assert first[0].translated_options == ["Technology", "Financial services"]
    assert second[0].translated_question == first[0].translated_question
    prompt = json.loads(provider.prompts[0])
    assert set(prompt) == {"allowed_canonical_keys", "fields"}
    assert set(prompt["fields"][0]) == {
        "input_index",
        "question",
        "options",
        "html_name",
        "html_id",
        "autocomplete",
        "placeholder",
        "section",
    }
    assert prompt["fields"][0]["question"] == field.question
    assert prompt["fields"][0]["options"] == field.options


@pytest.mark.asyncio
async def test_detector_captures_safe_structural_context_without_field_values() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <section aria-label="Profile information — address">
                    <h2>Address</h2>
                    <label for="street">Street Name</label>
                    <input id="street" name="candidateStreetName"
                      autocomplete="address-line1" placeholder="Street" value="Secret value">
                  </section>
                </form>
                """
            )

            field = (await detect_fields(page, page.locator("#application")))[0]

            assert field.question == "Street Name"
            assert field.html_name == "candidateStreetName"
            assert field.html_id == "street"
            assert field.autocomplete == "address-line1"
            assert field.placeholder == "Street"
            assert field.section == "Address"
            assert "Secret value" not in field.model_dump_json()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_english_unknown_identity_field_uses_constrained_classifier_context(
    session, test_settings
) -> None:
    provider = FakeTranslationProvider()
    field = DetectedField(
        selector="#identity",
        question="Identity detail",
        html_name="candidateDetail",
        section="Profile information",
        field_type="text",
    )

    await FieldInterpreter(session, test_settings, provider=provider).interpret([field])

    assert provider.calls == 1
    prompt = json.loads(provider.prompts[0])
    assert prompt["fields"][0]["html_name"] == "candidateDetail"
    assert prompt["fields"][0]["section"] == "Profile information"


@pytest.mark.asyncio
async def test_optional_field_can_be_explicitly_left_blank_in_live_form(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "deloitte_multilingual_form.html"
    job = Job(
        company="Deloitte",
        title="Cloud Software Engineer",
        description="Build cloud software",
        description_sha256=description_hash("Build cloud software"),
        application_url=fixture.as_uri(),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        form_action="final",
    )
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#save-answers",
        question="Meine Antworten für zukünftige Anwendungen speichern",
        translated_question="Save my answers for future Deloitte applications",
        field_type="checkbox",
        answer="Yes",
        required=False,
    )
    session.add(field)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        service = ApplicationService(session, test_settings, browser)

        updated = await service.update_field(field, None, skip=True)

        session.refresh(field)
        assert field.answer is None
        assert field.skipped is True
        assert not await page.locator("#save-answers").is_checked()
        assert updated.status == ApplicationStatus.READY_FOR_REVIEW
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_required_field_cannot_be_left_blank(session, test_settings) -> None:
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
        selector="#required",
        question="Required fact",
        field_type="text",
        required=True,
    )
    session.add(field)
    session.commit()

    service = ApplicationService(session, test_settings, BrowserManager(test_settings))
    with pytest.raises(ApplicationStateError, match="Required fields cannot be left blank"):
        await service.update_field(field, None, skip=True)


@pytest.mark.asyncio
async def test_live_application_answer_is_not_saved_without_connected_browser(
    session, test_settings
) -> None:
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
        status=ApplicationStatus.NEEDS_USER_INPUT,
        current_url="https://jobs.example.test/apply",
    )
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#city",
        question="City",
        field_type="text",
        required=True,
    )
    session.add(field)
    session.commit()

    with pytest.raises(FieldUpdateError, match="no longer connected") as caught:
        await ApplicationService(
            session, test_settings, BrowserManager(test_settings)
        ).update_field(field, "Berlin")

    assert caught.value.code == "employer_window_closed"
    session.refresh(field)
    assert field.answer is None


@pytest.mark.asyncio
async def test_preferred_start_date_rejects_a_past_date(session, test_settings) -> None:
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
        selector="#start-date",
        question="Preferred start date or notice period",
        translated_question="Preferred start date or notice period",
        field_key="preferred_start_date",
        field_type="text",
        required=True,
    )
    session.add(field)
    session.commit()

    with pytest.raises(ApplicationStateError, match="cannot be in the past"):
        await ApplicationService(
            session, test_settings, BrowserManager(test_settings)
        ).update_field(field, "01/01/1990")

    session.refresh(field)
    assert field.answer is None


@pytest.mark.asyncio
async def test_stored_employer_choice_rejects_an_invented_option(session, test_settings) -> None:
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
        selector="#office",
        question="Office",
        field_type="select",
        options=["Berlin", "Hamburg"],
        required=True,
    )
    session.add(field)
    session.commit()

    service = ApplicationService(session, test_settings, BrowserManager(test_settings))
    with pytest.raises(ApplicationStateError, match="available options"):
        await service.update_field(field, "Munich")


@pytest.mark.asyncio
async def test_live_combobox_rejects_unknown_choice_without_leaving_it_typed(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "generic_widget_form.html"
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture.as_uri(),
    )
    session.add(job)
    session.commit()
    application = Application(job_id=job.id, status=ApplicationStatus.NEEDS_USER_INPUT)
    session.add(application)
    session.commit()
    field = ApplicationField(
        application_id=application.id,
        selector="#language",
        question="Working language",
        field_type="combobox",
        options=[],
        required=True,
    )
    session.add(field)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        service = ApplicationService(session, test_settings, browser)

        with pytest.raises(FieldUpdateError, match="no longer available") as caught:
            await service.update_field(field, "Spanish")

        assert caught.value.field_id == field.id
        assert caught.value.code == "employer_rejected_answer"
        assert await page.locator("#language").input_value() == ""
        session.refresh(field)
        assert field.answer is None
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_combobox_fill_uses_its_own_choices_when_other_lists_are_visible() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <div class="widget">
                    <label for="university">University</label>
                    <input id="university" role="combobox">
                    <div role="listbox">
                      <div role="option">TU Berlin</div>
                    </div>
                  </div>
                  <div class="widget">
                    <label for="travel">Willingness to travel</label>
                    <input id="travel" role="combobox" aria-controls="missing-generated-id">
                    <div role="listbox">
                      <div role="option">0 - 25%</div>
                      <div role="option">25 - 50%</div>
                    </div>
                  </div>
                </form>
                <script>
                  document.querySelectorAll('[role="option"]').forEach(option => {
                    option.addEventListener('click', () => {
                      const widget = option.closest('.widget')
                      const input = widget.querySelector('[role="combobox"]')
                      input.value = option.textContent.trim()
                      input.dispatchEvent(new Event('change', { bubbles: true }))
                    })
                  })
                </script>
                """
            )
            field = DetectedField(
                selector="#travel",
                question="Willingness to travel",
                field_type="combobox",
                options=["0 - 25%", "25 - 50%"],
            )

            await fill_field(page, field, "25 - 50%")

            assert await page.locator("#travel").input_value() == "25 - 50%"
            assert await page.locator("#university").input_value() == ""
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_reactive_checkbox_replacement_is_rediscovered_and_verified(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "reactive_replacement_form.html"
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture.as_uri(),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        form_action="final",
    )
    session.add(application)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        form = await mark_application_form(page, page.locator("#application-form"))
        detected = await detect_fields(page, form)
        checkbox = next(item for item in detected if item.field_type == "checkbox")
        field = ApplicationField(
            application_id=application.id,
            selector=checkbox.selector,
            question=checkbox.question,
            field_type=checkbox.field_type,
            required=False,
        )
        session.add(field)
        session.commit()

        updated = await ApplicationService(
            session, test_settings, browser
        ).update_field(field, "Yes")

        session.refresh(field)
        assert field.answer == "Yes"
        assert await page.locator("#save-answers").is_checked()
        assert await page.locator(field.selector).count() == 1
        assert updated.status == ApplicationStatus.READY_FOR_REVIEW
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_field_update_reconciles_another_control_reset_by_the_portal(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "reactive_replacement_form.html"
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture.as_uri(),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        form_action="final",
    )
    session.add(application)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        form = await mark_application_form(page, page.locator("#application-form"))
        detected = await detect_fields(page, form)
        location = next(item for item in detected if item.question == "Preferred location")
        checkbox = next(item for item in detected if item.field_type == "checkbox")
        location_field = ApplicationField(
            application_id=application.id,
            selector=location.selector,
            question=location.question,
            field_type=location.field_type,
            answer="Berlin",
            source=AnswerSource.CANDIDATE_PROFILE,
            confidence=1.0,
            required=True,
        )
        checkbox_field = ApplicationField(
            application_id=application.id,
            selector=checkbox.selector,
            question=checkbox.question,
            field_type=checkbox.field_type,
            required=False,
        )
        session.add(location_field)
        session.add(checkbox_field)
        session.commit()

        updated = await ApplicationService(
            session, test_settings, browser
        ).update_field(checkbox_field, "Yes")

        session.refresh(location_field)
        session.refresh(checkbox_field)
        assert checkbox_field.answer == "Yes"
        assert location_field.answer is None
        assert location_field.resolution_message == (
            "The employer form no longer contains the saved value. Enter this field again."
        )
        assert updated.status == ApplicationStatus.NEEDS_USER_INPUT
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_visible_but_invalid_employer_value_is_not_persisted(
    session, test_settings
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "reactive_replacement_form.html"
    job = Job(
        company="Acme",
        title="Engineer",
        description="Build software",
        description_sha256=description_hash("Build software"),
        application_url=fixture.as_uri(),
    )
    session.add(job)
    session.commit()
    application = Application(
        job_id=job.id,
        status=ApplicationStatus.NEEDS_USER_INPUT,
        form_action="final",
    )
    session.add(application)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        page = await browser.open(application.id, fixture.as_uri())
        form = await mark_application_form(page, page.locator("#application-form"))
        postal = next(
            item for item in await detect_fields(page, form) if item.question == "Postal code"
        )
        field = ApplicationField(
            application_id=application.id,
            selector=postal.selector,
            question=postal.question,
            field_type=postal.field_type,
            required=True,
        )
        session.add(field)
        session.commit()

        with pytest.raises(FieldUpdateError, match="did not keep this value"):
            await ApplicationService(session, test_settings, browser).update_field(
                field, "10115"
            )

        session.refresh(field)
        assert await page.locator("#postal-code").input_value() == "10115"
        assert field.answer is None
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_review_field_can_be_focused_in_the_employer_window(session, test_settings) -> None:
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
        selector="#language",
        question="Working language",
        field_type="combobox",
    )
    session.add(field)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        fixture = Path(__file__).parent / "fixtures" / "generic_widget_form.html"
        page = await browser.open(application.id, fixture.as_uri())

        await ApplicationService(session, test_settings, browser).focus_field(field)

        assert await page.evaluate("document.activeElement.id") == "language"
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_multilingual_application_flow_persists_english_review_and_fills_original_form(
    session, test_settings, tmp_path
) -> None:
    ProfileService(session).upsert(
        CandidateProfileInput(
            first_name="Ada",
            last_name="Lovelace",
            phone="+49 30 123456",
            address="Musterstraße 42",
            date_of_birth=date(1990, 5, 17),
            preferred_locations=["Frankfurt"],
            consider_other_positions="Yes",
        )
    )
    resume_path = tmp_path / "internal-storage-id.pdf"
    resume_path.write_bytes(b"%PDF-1.4\n% multilingual fixture")
    resume = Resume(
        filename="Ada_Lovelace_CV.pdf",
        path=str(resume_path),
        label="Cloud resume",
        content_sha256="multilingual-fixture",
        is_default=True,
    )
    fixture = Path(__file__).parent / "fixtures" / "deloitte_multilingual_form.html"
    job = Job(
        company="Deloitte",
        title="Cloud Software Engineer",
        description="Build cloud software",
        description_sha256=description_hash("Build cloud software"),
        application_url=fixture.as_uri(),
    )
    session.add(resume)
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    try:
        service = ApplicationService(session, test_settings, browser)

        application = await service.start(job, resume)
        review = service.read(application)

        assert application.status == ApplicationStatus.NEEDS_USER_INPUT
        assert all(field.question != "Weitere Angaben" for field in review.fields)
        birth = next(field for field in review.fields if field.question == "Geburtsdatum")
        address = next(
            field for field in review.fields if field.question == "Straße und Hausnummer"
        )
        location = next(
            field for field in review.fields if field.question == "Standortwahl Priorität 1"
        )
        assert birth.translated_question == "Date of birth"
        assert birth.field_type == "date"
        assert birth.answer == "1990-05-17"
        assert address.translated_question == "Street and house number"
        assert address.answer == "Musterstraße 42"
        assert location.translated_question == "Preferred location — first choice"
        assert location.answer == "Frankfurt"
        page = browser.page(application.id)
        assert page is not None
        assert await page.locator("#birth-date").input_value() == "17.05.1990"
        assert await page.locator("#location").input_value() == "Frankfurt"
        assert (
            await page.locator("#resume").evaluate("element => element.files[0].name")
            == "Ada_Lovelace_CV.pdf"
        )
    finally:
        await browser.close()
