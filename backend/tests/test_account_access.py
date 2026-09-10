from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.automation.browser import BrowserManager
from app.models import Job
from app.models.enums import ApplicationStatus
from app.services.application_service import ApplicationService


@pytest.mark.asyncio
async def test_account_gate_detection_requires_real_auth_context(test_settings) -> None:
    browser = BrowserManager(test_settings)
    try:
        context = await browser.context()
        page = await context.new_page()

        await page.set_content(
            """
            <title>Software Engineer</title>
            <nav><a href="/login">Sign in</a></nav>
            <main><h1>Software Engineer</h1><p>Build useful products.</p></main>
            """
        )
        assert await browser.has_login(page) is False

        await page.set_content(
            """
            <title>Bewerberkonto registrieren</title>
            <main>
              <h1>Konto erstellen, um sich zu bewerben</h1>
              <form action="/registrieren">
                <label>E-Mail <input type="email" name="email"></label>
                <button>Registrieren</button>
              </form>
            </main>
            """
        )
        assert await browser.has_login(page) is True
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_login_page_with_captcha_is_reported_as_account_access(
    session, test_settings
) -> None:
    login_url = (
        Path(__file__).parent / "fixtures" / "account_login_with_captcha.html"
    ).as_uri()
    job = Job(
        company="Acme",
        title="Platform Engineer",
        description="Build reliable systems",
        description_sha256="login-captcha-fixture",
        source_url=login_url,
        application_url=login_url,
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        application = await service.start(job, resume=None)

        assert application.status == ApplicationStatus.WAITING_FOR_USER
        assert application.waiting_reason == "login"
        login_page = browser.page(application.id)
        assert login_page is not None
        assert await browser.has_login(login_page)
        assert await browser.has_captcha(login_page)
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_completed_account_tab_replaces_stale_login_captcha(
    session, test_settings
) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    login_url = (fixtures / "account_login_with_captcha.html").as_uri()
    application_url = (fixtures / "authenticated_application_form.html").as_uri()
    job = Job(
        company="Acme",
        title="Platform Engineer",
        description="Build reliable systems",
        description_sha256="completed-account-handoff-fixture",
        source_url=login_url,
        application_url=login_url,
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        application = await service.start(job, resume=None)
        stale_login_page = browser.page(application.id)
        assert stale_login_page is not None
        assert application.waiting_reason == "login"

        # Simulate a draft created before login+CAPTCHA pages were classified as one account
        # access step. The stale tab remains open while the portal launches the signed-in
        # application in a separate tab.
        application.waiting_reason = "captcha"
        application.error_message = "Manual CAPTCHA completion required."
        session.add(application)
        session.commit()
        session.refresh(application)
        context = await browser.context()
        signed_in_page = await context.new_page()
        await signed_in_page.goto(application_url, wait_until="domcontentloaded")

        continued = await service.continue_after_user_action(application)

        assert continued.status == ApplicationStatus.NEEDS_USER_INPUT
        assert continued.waiting_reason is None
        assert browser.page(application.id) is signed_in_page
        assert continued.current_url.endswith("authenticated_application_form.html")
        assert await browser.has_captcha(stale_login_page)
        assert service.read(continued).fields
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_account_gate_popup_is_adopted_and_can_resume_application(
    session, test_settings
) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    job_url = (fixtures / "popup_login_job_page.html").as_uri()
    application_url = (fixtures / "application_form.html").as_uri()
    job = Job(
        company="Acme",
        title="Platform Engineer",
        description="Build reliable systems",
        description_sha256="account-gate-fixture",
        source_url=job_url,
        application_url=job_url,
    )
    session.add(job)
    session.commit()
    browser = BrowserManager(test_settings)
    service = ApplicationService(session, test_settings, browser)
    try:
        application = await service.start(job, resume=None)

        assert application.status == ApplicationStatus.WAITING_FOR_USER
        assert application.waiting_reason == "login"
        login_page = browser.page(application.id)
        assert login_page is not None
        assert login_page.url.endswith("account_login.html")

        await login_page.goto(application_url, wait_until="domcontentloaded")
        continued = await service.continue_after_user_action(application)

        assert continued.status == ApplicationStatus.NEEDS_USER_INPUT
        assert service.read(continued).fields
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_portal_session_cookie_survives_managed_browser_restart(test_settings) -> None:
    expires = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    first = BrowserManager(test_settings)
    first_context = await first.context()
    await first_context.add_cookies(
        [
            {
                "name": "portal_session",
                "value": "local-test-session",
                "url": "https://careers.example",
                "expires": expires,
            }
        ]
    )
    await first.close()

    second = BrowserManager(test_settings)
    try:
        second_context = await second.context()
        cookies = await second_context.cookies("https://careers.example")

        assert any(
            cookie["name"] == "portal_session" and cookie["value"] == "local-test-session"
            for cookie in cookies
        )
    finally:
        await second.close()
