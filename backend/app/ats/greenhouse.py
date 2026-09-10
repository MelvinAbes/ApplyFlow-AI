from playwright.async_api import Locator, Page

from app.ats.base import ExtractedJob, first_text
from app.ats.generic import GenericATSAdapter


class GreenhouseAdapter(GenericATSAdapter):
    def matches(self, url: str) -> bool:
        lowered = url.casefold()
        return "greenhouse.io" in lowered or "boards.greenhouse" in lowered

    async def extract_job(self, page: Page) -> ExtractedJob:
        base = await super().extract_job(page)
        title = await first_text(page, (".job__title h1", "h1.app-title", "h1")) or base.title
        company = await first_text(page, (".company-name", "#header .company-name")) or base.company
        location = await first_text(page, (".location", ".job__location")) or base.location
        description = (
            await first_text(page, ("#content", ".job__description", "main")) or base.description
        )
        return ExtractedJob(
            company=company,
            title=title,
            location=location,
            description=description,
            employment_type=None,
            source_url=page.url,
            application_url=page.url,
        )

    async def find_form(self, page: Page) -> Locator | None:
        form = page.locator("form#application_form, form#application-form")
        return form.first if await form.count() else await super().find_form(page)
