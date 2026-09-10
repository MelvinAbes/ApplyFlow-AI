import pytest
from playwright.async_api import async_playwright

from app.ats.generic import GenericATSAdapter
from app.automation.field_detector import detect_fields, mark_application_form
from app.automation.form_filler import fill_field
from app.automation.submission import (
    allow_manual_submission,
    classify_form_action,
    ensure_submission_guard,
    focus_form_action_control,
    guarded_advance,
    guarded_submit,
    inspect_form_action,
)


@pytest.mark.asyncio
async def test_autofill_change_handler_cannot_submit_before_approval() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application" onsubmit="window.submits=(window.submits||0)+1; event.preventDefault()">
                  <label for="authorization">Work authorization</label>
                  <select id="authorization" onchange="this.form.requestSubmit()">
                    <option value="">Choose</option><option>Yes</option><option>No</option>
                  </select>
                  <button type="submit">Submit application</button>
                </form>
                """
            )
            await ensure_submission_guard(page)
            form = await mark_application_form(page, page.locator("#application"))
            field = (await detect_fields(page, form))[0]
            await fill_field(page, field, "Yes")
            assert await page.evaluate("window.submits || 0") == 0
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_guarded_submit_uses_only_the_selected_application_form() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="newsletter" onsubmit="window.clicked='newsletter'; event.preventDefault()">
                  <label>Email <input name="email"></label><button type="submit">Subscribe</button>
                </form>
                <form id="application" onsubmit="window.clicked='application'; this.hidden=true; event.preventDefault()">
                  <label>First name <input name="first_name"></label>
                  <label>Resume <input type="file" name="resume"></label>
                  <textarea aria-label="Motivation"></textarea>
                  <button type="submit">Submit application</button>
                </form>
                """
            )
            await ensure_submission_guard(page)
            selected = await GenericATSAdapter().find_form(page)
            assert selected is not None
            assert await selected.get_attribute("id") == "application"
            form = await mark_application_form(page, selected)
            evidence = await guarded_submit(
                page, form, approved=True, confirmation_timeout_ms=1_000
            )
            assert await page.evaluate("window.clicked") == "application"
            assert evidence.confirmed
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_page_level_apply_action_is_safely_bound_to_selected_form() -> None:
    """Support portals whose action bar is a sibling of the native form."""

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <main id="application-shell">
                  <form id="application" onsubmit="window.clicked='application'; this.hidden=true; event.preventDefault()">
                    <label>First name <input name="first_name" value="Ada"></label>
                    <button type="button">My Documents</button>
                  </form>
                  <footer>
                    <button id="save" type="button">Save</button>
                    <button id="apply" type="button"
                      onclick="document.querySelector('#application').requestSubmit()">Apply</button>
                  </footer>
                </main>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))

            inspection = await inspect_form_action(form)
            assert inspection.action == "final"
            assert inspection.reason == "recognized"
            assert inspection.labels == ("Apply",)

            evidence = await guarded_submit(
                page, form, approved=True, confirmation_timeout_ms=1_000
            )
            assert evidence.confirmed
            assert await page.evaluate("window.clicked") == "application"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_unrelated_page_level_apply_action_is_not_adopted() -> None:
    """Never scan all the way to the page body for a convenient Apply CTA."""

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <main><form id="application"><label>Name <input name="name"></label></form></main>
                <aside><button type="button">Apply</button></aside>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))
            inspection = await inspect_form_action(form)

            assert inspection.action == "unknown"
            assert inspection.reason == "unbound_action_control"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_native_validation_failure_is_reported_as_not_sent() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application" onsubmit="window.submits=(window.submits||0)+1; event.preventDefault()">
                  <label for="birth-date">Date of birth</label>
                  <input id="birth-date" name="birth_date" required>
                  <button type="submit">Submit application</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))

            evidence = await guarded_submit(
                page, form, approved=True, confirmation_timeout_ms=1_000
            )

            assert evidence.confirmed is False
            assert evidence.attempted is False
            assert evidence.signal == "validation_failed"
            assert evidence.validation_errors == ("Date of birth",)
            assert await page.evaluate("window.submits || 0") == 0
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_manual_submission_requires_initial_approval_then_stays_available() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 800, "height": 500})
        try:
            await page.set_content(
                """
                <form id="application" onsubmit="window.submits=(window.submits||0)+1; event.preventDefault()">
                  <label for="name">Name</label><input id="name" value="Ada">
                  <div style="height:1200px"></div>
                  <button id="submit" type="submit">Submit application</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))
            await ensure_submission_guard(page)

            await page.locator("#submit").click()
            assert await page.evaluate("window.submits || 0") == 0

            await allow_manual_submission(page, form)
            await focus_form_action_control(page, form, "final")
            assert await page.evaluate("document.activeElement.id") == "submit"
            assert await page.locator("#submit").evaluate(
                "element => { const rect=element.getBoundingClientRect(); return rect.top>=0 && rect.bottom<=innerHeight; }"
            )

            await page.locator("#submit").click()
            assert await page.evaluate("window.submits || 0") == 1
            await page.locator("#submit").click()
            assert await page.evaluate("window.submits || 0") == 2
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_unconfirmed_approved_submit_leaves_form_available_for_manual_retry() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application" onsubmit="window.submits=(window.submits||0)+1; event.preventDefault()">
                  <label>Name <input name="name" value="Ada"></label>
                  <button id="submit" type="submit">Submit application</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))

            evidence = await guarded_submit(
                page, form, approved=True, confirmation_timeout_ms=250
            )
            assert evidence.confirmed is False
            assert evidence.attempted is True
            assert await page.evaluate("window.submits || 0") == 1

            await page.locator("#submit").click()
            assert await page.evaluate("window.submits || 0") == 2
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_approved_continue_does_not_release_later_submission() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application" onsubmit="
                  window.submits=(window.submits||0)+1;
                  event.preventDefault();
                  this.querySelector('button').textContent='Continue';
                  this.dataset.advanced='true';
                ">
                  <label>Name <input name="name" value="Ada"></label>
                  <button id="continue" type="submit">Next</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))
            await guarded_advance(page, form)
            assert await page.evaluate("window.submits || 0") == 1

            await page.locator("#continue").click()
            assert await page.evaluate("window.submits || 0") == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_next_button_is_never_treated_as_final_submission() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <label>First name <input name="first_name"></label>
                  <button type="submit">Next</button>
                </form>
                """
            )
            await ensure_submission_guard(page)
            form = await mark_application_form(page, page.locator("#application"))
            assert await classify_form_action(form) == "continue"
            with pytest.raises(RuntimeError, match="No submission control"):
                await guarded_submit(page, form, approved=True, confirmation_timeout_ms=500)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_common_german_submit_label_is_recognized() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <label>First name <input name="first_name"></label>
                  <button type="submit">Bewerbung einreichen</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))
            inspection = await inspect_form_action(form)
            assert inspection.action == "final"
            assert inspection.reason == "recognized"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_disabled_form_action_has_a_recoverable_diagnostic() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <label>First name <input name="first_name"></label>
                  <button id="submit" type="submit" disabled>Submit application</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))
            inspection = await inspect_form_action(form)
            assert inspection.action == "unknown"
            assert inspection.reason == "disabled_action_control"
            assert "disabled" in inspection.waiting_message()

            await page.locator("#submit").evaluate("element => { element.disabled = false; }")
            recovered = await inspect_form_action(form)
            assert recovered.action == "final"
            assert recovered.reason == "recognized"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_multiple_form_actions_remain_ambiguous() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.set_content(
                """
                <form id="application">
                  <label>First name <input name="first_name"></label>
                  <button type="button">Continue</button>
                  <button type="submit">Submit application</button>
                </form>
                """
            )
            form = await mark_application_form(page, page.locator("#application"))
            inspection = await inspect_form_action(form)
            assert inspection.action == "unknown"
            assert inspection.reason == "multiple_action_controls"
        finally:
            await browser.close()
