from abc import ABC, abstractmethod
from dataclasses import dataclass

from playwright.async_api import Locator, Page

from app.automation.field_detector import DetectedField


@dataclass(slots=True)
class ExtractedJob:
    company: str
    title: str
    location: str | None
    description: str
    employment_type: str | None
    source_url: str
    application_url: str
    external_job_id: str | None = None


class ATSAdapter(ABC):
    @abstractmethod
    def matches(self, url: str) -> bool: ...

    @abstractmethod
    async def extract_job(self, page: Page) -> ExtractedJob: ...

    @abstractmethod
    async def find_form(self, page: Page) -> Locator | None: ...

    async def open_application(self, page: Page) -> bool:
        """Navigate from a job page to its application form when a safe CTA is available."""
        return False

    async def fill_application(self, page: Page, fields: list[DetectedField]) -> None:
        # Core deterministic filler handles the fields. Override only for ATS-specific widgets.
        return None


async def first_text(page: Page, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count():
            text = (await locator.first.inner_text()).strip()
            if text:
                return text
    return None
