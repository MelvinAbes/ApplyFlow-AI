import pytest

from app.automation.browser import BrowserManager, UnsafeNavigationError, same_site


@pytest.mark.asyncio
async def test_private_network_navigation_is_blocked(test_settings) -> None:
    browser = BrowserManager(test_settings)
    with pytest.raises(UnsafeNavigationError, match="Private"):
        await browser._validate_navigation_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(UnsafeNavigationError, match="Private"):
        await browser._validate_navigation_url("http://127.0.0.1:9000/internal")


@pytest.mark.asyncio
async def test_configured_local_demo_and_headless_fixtures_are_allowed(test_settings) -> None:
    browser = BrowserManager(test_settings)
    await browser._validate_navigation_url("http://127.0.0.1:8000/demo/application")
    await browser._validate_navigation_url("file:///tmp/application-fixture.html")


def test_same_site_allows_employer_subdomains_without_cross_company_adoption() -> None:
    assert same_site("https://job.deloitte.com/role", "https://de-job.deloitte.com/apply")
    assert not same_site("https://jobs.example.com/role", "https://apply.evil.com/form")
    assert same_site("https://jobs.company.co.uk/role", "https://apply.company.co.uk/form")
    assert not same_site("https://jobs.company.co.uk/role", "https://apply.other.co.uk/form")
