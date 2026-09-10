from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from app.automation.field_detector import detect_fields, mark_application_form
from app.automation.field_mapper import map_field
from app.automation.form_filler import fill_field


@pytest.mark.asyncio
async def test_external_resume_widget_is_detected_and_confirmed(tmp_path: Path) -> None:
    """A nested profile form may use a sibling document-upload widget."""

    resume_path = tmp_path / "Candidate_CV.pdf"
    resume_path.write_bytes(b"test resume")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <main id="application-shell">
                  <form id="profile-form">
                    <label>First name <input name="first_name" value="Ada"></label>
                    <button type="button">Profile Information</button>
                  </form>
                  <section id="documents">
                    <div class="upload-widget">
                      <label id="resume-label">Resume / CV</label>
                      <span>Upload a Resume Required</span>
                      <input id="resume" name="candidateDocument" type="file"
                        aria-labelledby="resume-label" hidden required>
                      <button type="button" onclick="document.querySelector('#resume').click()">
                        Upload a Resume
                      </button>
                      <output id="resume-filename"></output>
                    </div>
                    <div class="upload-widget">
                      <label id="cover-label">Cover Letter</label>
                      <input id="cover-letter" name="supportingDocument" type="file"
                        aria-labelledby="cover-label" hidden>
                      <button type="button" onclick="document.querySelector('#cover-letter').click()">
                        Attach a Cover Letter
                      </button>
                    </div>
                  </section>
                  <footer><button type="button">Apply</button></footer>
                </main>
                <script>
                  document.querySelector('#resume').addEventListener('change', event => {
                    document.querySelector('#resume-filename').textContent = event.target.files[0]?.name || '';
                  });
                </script>
                """
            )
            form = await mark_application_form(page, page.locator("#profile-form"))

            fields = await detect_fields(page, form)
            resume_fields = [field for field in fields if field.html_id == "resume"]

            assert len(resume_fields) == 1
            resume_field = resume_fields[0]
            assert resume_field.field_type == "file"
            assert resume_field.required is True
            assert map_field(resume_field.question)[0] == "resume"
            assert all(field.html_id != "cover-letter" for field in fields)

            result = await fill_field(
                page,
                resume_field,
                "Selected resume",
                upload_path=resume_path,
                upload_name="Candidate_CV.pdf",
            )

            assert result is not None
            assert await page.locator("#resume-filename").inner_text() == "Candidate_CV.pdf"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_replaced_external_resume_input_is_rediscovered_globally(tmp_path: Path) -> None:
    """A portal can replace the sibling upload input after the form was scanned."""

    resume_path = tmp_path / "Candidate_CV.pdf"
    resume_path.write_bytes(b"test resume")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="profile-form"><input name="first_name"></form>
                <section class="upload-widget">
                  <label id="resume-label">Resume / CV</label>
                  <input id="resume" type="file" aria-labelledby="resume-label" hidden required>
                  <output id="resume-filename"></output>
                </section>
                <script>
                  const replacement = document.querySelector('#resume').cloneNode();
                  replacement.id = 'replacement-resume';
                  replacement.addEventListener('change', event => {
                    document.querySelector('#resume-filename').textContent =
                      event.target.files[0]?.name || '';
                  });
                  document.querySelector('#resume').replaceWith(replacement);
                </script>
                """
            )
            form = await mark_application_form(page, page.locator("#profile-form"))

            # Scan against an input that the site then replaces before filling.
            await page.evaluate(
                """() => {
                  const replacement = document.querySelector('#replacement-resume');
                  const original = replacement.cloneNode();
                  original.id = 'resume';
                  replacement.replaceWith(original);
                }"""
            )
            fields = await detect_fields(page, form)
            resume_field = next(field for field in fields if field.html_id == "resume")
            await page.evaluate(
                """() => {
                  const original = document.querySelector('#resume');
                  const replacement = original.cloneNode();
                  replacement.id = 'replacement-resume';
                  replacement.addEventListener('change', event => {
                    document.querySelector('#resume-filename').textContent =
                      event.target.files[0]?.name || '';
                  });
                  original.replaceWith(replacement);
                }"""
            )

            await fill_field(
                page,
                resume_field,
                "Selected resume",
                upload_path=resume_path,
                upload_name="Candidate_CV.pdf",
            )

            assert await page.locator("#resume-filename").inner_text() == "Candidate_CV.pdf"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_external_resume_button_that_creates_its_file_input_is_supported(
    tmp_path: Path,
) -> None:
    """Some portals expose a CV button first and create the native picker on click."""

    resume_path = tmp_path / "Candidate_CV.pdf"
    resume_path.write_bytes(b"test resume")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="profile-form"><input name="first_name"></form>
                <section class="upload-widget">
                  <div class="requiredFieldWrapper">
                    <span>Resume / CV</span>
                    <span>Upload a Resume</span>
                    <div role="button" class="hidden-label" style="display:none">
                      Upload a Resume
                    </div>
                    <span id="resume-upload" role="button"
                      aria-label="Resume / CV Upload a Resume Opens a dialog"
                      style="display:inline-block;width:20px;height:20px"></span>
                  </div>
                  <output id="resume-filename"></output>
                </section>
                <section class="upload-widget">
                  <span>Cover Letter</span>
                  <button type="button">Attach a Cover Letter</button>
                </section>
                <script>
                  document.querySelector('#resume-upload').addEventListener('click', () => {
                    setTimeout(() => {
                      const wrapper = document.createElement('div');
                      const label = document.createElement('span');
                      label.id = 'local-upload-label';
                      label.textContent = 'Upload from Device';
                      const source = document.createElement('input');
                      source.type = 'file';
                      source.setAttribute('aria-labelledby', label.id);
                      source.style.cssText =
                        'position:absolute;z-index:100;width:164px;height:27px;opacity:0;';
                      source.addEventListener('change', event => {
                        document.querySelector('#resume-filename').textContent =
                          event.target.files[0]?.name || '';
                      });
                      wrapper.append(label);
                      wrapper.append(source);
                      document.querySelector('.upload-widget').append(wrapper);
                    }, 1800);
                  });
                </script>
                """
            )
            form = await mark_application_form(page, page.locator("#profile-form"))
            fields = await detect_fields(page, form)

            resume_fields = [field for field in fields if field.field_type == "file"]
            assert len(resume_fields) == 1
            resume_field = resume_fields[0]
            assert resume_field.required is True
            assert map_field(resume_field.question)[0] == "resume"

            await fill_field(
                page,
                resume_field,
                "Selected resume",
                upload_path=resume_path,
                upload_name="Candidate_CV.pdf",
            )

            assert await page.locator("#resume-filename").inner_text() == "Candidate_CV.pdf"
        finally:
            await browser.close()
