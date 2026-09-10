from playwright.async_api import Locator, Page

from app.ats.base import ExtractedJob, first_text
from app.ats.generic import GenericATSAdapter


class PersonioAdapter(GenericATSAdapter):
    def matches(self, url: str) -> bool:
        return "personio." in url.casefold() or "jobs.personio" in url.casefold()

    async def extract_job(self, page: Page) -> ExtractedJob:
        base = await super().extract_job(page)
        title = await first_text(page, ("[data-testid='job-title']", "h1")) or base.title
        location = (
            await first_text(page, ("[data-testid='job-location']", "[class*='location' i]"))
            or base.location
        )
        description = (
            await first_text(page, ("[data-testid='job-description']", "main", "article"))
            or base.description
        )
        return ExtractedJob(
            company=base.company,
            title=title,
            location=location,
            description=description,
            employment_type=None,
            source_url=page.url,
            application_url=page.url,
        )

    async def find_form(self, page: Page) -> Locator | None:
        form = page.locator("form[data-testid='application-form']")
        return form.first if await form.count() else await super().find_form(page)
