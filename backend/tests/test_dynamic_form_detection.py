from pathlib import Path

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.ats.generic import GenericATSAdapter
from app.automation.browser import BrowserManager
from app.models import Job
from app.models.enums import ApplicationStatus
from app.services.application_service import ApplicationService


@pytest.mark.asyncio
async def test_delayed_form_is_detected_and_passive_recaptcha_is_ignored(
    session, test_settings
) -> None:
    fixture_url = (Path(__file__).parent / "fixtures" / "delayed_application_form.html").as_uri()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        page = await browser.open("delayed-form", fixture_url)

        assert await page.locator("form").count() == 0
        assert not await browser.has_captcha(page)
        located = await service._locate_application_form(page, allow_navigation=True)

        assert located is not None
        selected_page, adapter, form = located
        assert selected_page is page
        assert type(adapter).__name__ == "GenericATSAdapter"
        assert await form.get_attribute("id") == "delayed-application"
        assert not await browser.has_captcha(page)
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_visible_recaptcha_still_blocks_user_action(test_settings) -> None:
    browser = BrowserManager(test_settings)
    try:
        context = await browser.context()
        page = await context.new_page()
        await page.set_content(
            """
            <form><label>Name <input name="name"></label></form>
            <iframe
              title="reCAPTCHA verification challenge"
              src="https://www.recaptcha.net/recaptcha/api2/bframe"
              style="width: 400px; height: 580px"
            ></iframe>
            """
        )

        assert await browser.has_captcha(page)
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_application_popup_becomes_the_tracked_page(session, test_settings) -> None:
    fixture_url = (Path(__file__).parent / "fixtures" / "popup_job_page.html").as_uri()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        job_page = await browser.open("popup-application", fixture_url)

        located = await service._locate_application_form(
            job_page,
            allow_navigation=True,
            application_key="popup-application",
            expected_title="Cloud Software Engineer (m/w/d)",
        )

        assert located is not None
        application_page, _, form = located
        assert application_page is not job_page
        assert browser.page("popup-application") is application_page
        assert application_page.url.endswith("delayed_application_form.html")
        assert await form.get_attribute("id") == "delayed-application"
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_existing_same_site_form_tab_is_adopted_without_another_click(
    session, test_settings
) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    job_url = (fixtures / "popup_job_page.html").as_uri()
    form_url = (fixtures / "delayed_application_form.html").as_uri()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        job_page = await browser.open("manual-tab", job_url)
        context = await browser.context()
        application_page = await context.new_page()
        await application_page.goto(form_url, wait_until="domcontentloaded")
        await application_page.locator("#delayed-application").wait_for()

        located = await service._locate_application_form(
            job_page,
            allow_navigation=True,
            application_key="manual-tab",
            expected_title="Cloud Software Engineer (m/w/d)",
        )

        assert located is not None
        selected_page, _, _ = located
        assert selected_page is application_page
        assert browser.page("manual-tab") is application_page
        assert await job_page.evaluate("window.applyClicks || 0") == 0
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_new_scan_does_not_adopt_a_stale_application_tab(session, test_settings) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    job_url = (fixtures / "popup_job_page.html").as_uri()
    form_url = (fixtures / "delayed_application_form.html").as_uri()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        job_page = await browser.open("fresh-scan", job_url)
        context = await browser.context()
        stale_page = await context.new_page()
        await stale_page.goto(form_url, wait_until="domcontentloaded")
        await stale_page.locator("#delayed-application").wait_for()

        located = await service._locate_application_form(
            job_page,
            allow_navigation=True,
            application_key="fresh-scan",
            expected_title="Cloud Software Engineer (m/w/d)",
            reuse_existing_tabs=False,
        )

        assert located is not None
        selected_page, _, _ = located
        assert selected_page is not stale_page
        assert browser.page("fresh-scan") is selected_page
        assert await job_page.evaluate("window.applyClicks || 0") == 1
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_popup_is_recovered_when_apply_click_reports_a_timeout(
    session, test_settings, monkeypatch
) -> None:
    fixture_url = (Path(__file__).parent / "fixtures" / "popup_job_page.html").as_uri()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    original_open = GenericATSAdapter.open_application

    async def timeout_after_click(self, page):
        assert await original_open(self, page)
        raise PlaywrightTimeoutError("simulated portal navigation timeout")

    monkeypatch.setattr(GenericATSAdapter, "open_application", timeout_after_click)
    try:
        job_page = await browser.open("timeout-popup", fixture_url)

        located = await service._locate_application_form(
            job_page,
            allow_navigation=True,
            application_key="timeout-popup",
            expected_title="Cloud Software Engineer (m/w/d)",
            reuse_existing_tabs=False,
        )

        assert located is not None
        application_page, _, form = located
        assert application_page is not job_page
        assert browser.page("timeout-popup") is application_page
        assert await form.get_attribute("id") == "delayed-application"
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_application_start_retries_a_transient_pre_scan_timeout(
    session, test_settings, monkeypatch
) -> None:
    fixture_url = (
        Path(__file__).parent / "fixtures" / "delayed_application_form.html"
    ).as_uri()
    job = Job(
        company="Acme",
        title="Cloud Software Engineer (m/w/d)",
        description="Cloud role",
        description_sha256="transient-timeout-fixture",
        source_url=fixture_url,
        application_url=fixture_url,
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    original_process_page = service._process_page
    attempts = 0

    async def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PlaywrightTimeoutError("simulated transient scan timeout")
        return await original_process_page(*args, **kwargs)

    monkeypatch.setattr(service, "_process_page", fail_once)
    try:
        application = await service.start(job, resume=None)

        assert attempts == 2
        assert application.status == ApplicationStatus.NEEDS_USER_INPUT
        assert len(service.read(application).fields) == 3
    finally:
        await browser.close()
