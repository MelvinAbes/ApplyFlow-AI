from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.ats.base import ATSAdapter, ExtractedJob, first_text


class GenericATSAdapter(ATSAdapter):
    def matches(self, url: str) -> bool:
        return True

    async def extract_job(self, page: Page) -> ExtractedJob:
        title = await first_text(page, ("h1", "[itemprop='title']", "title")) or "Untitled role"
        company = (
            await first_text(
                page, ("[itemprop='hiringOrganization']", ".company", "[class*='company' i]")
            )
            or (await page.title()).split("-")[-1].strip()
            or "Unknown company"
        )
        location = await first_text(
            page, ("[itemprop='jobLocation']", ".location", "[class*='location' i]")
        )
        description = (
            await first_text(
                page,
                (
                    "[itemprop='description']",
                    "#job-description",
                    ".job-description",
                    "main",
                    "article",
                    "body",
                ),
            )
            or ""
        )
        return ExtractedJob(
            company=company[:300],
            title=title[:300],
            location=location,
            description=description,
            employment_type=None,
            source_url=page.url,
            application_url=page.url,
        )

    async def find_form(self, page: Page) -> Locator | None:
        forms = page.locator("form")
        best: tuple[int, Locator] | None = None
        for index in range(await forms.count()):
            form = forms.nth(index)
            score = await form.evaluate(
                """element => {
                  if (element.offsetParent === null) return -1000;
                  const text = (element.innerText || '').toLowerCase();
                  const action = (element.getAttribute('action') || '').toLowerCase();
                  const controls = element.querySelectorAll('input:not([type=hidden]), textarea, select');
                  let score = controls.length * 2;
                  if (element.querySelector('input[type=file]')) score += 40;
                  if (element.querySelector('textarea')) score += 8;
                  if (/apply|application|bewerb/.test(action)) score += 20;
                  if (/resume|curriculum|lebenslauf|cover letter|motivation/.test(text)) score += 20;
                  if (/first name|last name|vorname|nachname|linkedin|work authorization/.test(text)) score += 15;
                  if (/submit application|send application|bewerbung absenden/.test(text)) score += 20;
                  if (/next|continue|weiter|fortfahren/.test(text)) score += 15;
                  if (/newsletter|subscribe|search|kontaktformular|contact us/.test(text)) score -= 50;
                  if (controls.length <= 2) score -= 15;
                  return score;
                }"""
            )
            if best is None or score > best[0]:
                best = (score, form)
        return best[1] if best and best[0] >= 5 else None

    async def open_application(self, page: Page) -> bool:
        selectors = (
            "a:has-text('Apply now')",
            "a:has-text('Start application')",
            "a:has-text('Jetzt bewerben')",
            "a:has-text('Bewerben')",
            "button:has-text('Apply now')",
            "button:has-text('Start application')",
            "button:has-text('Jetzt bewerben')",
        )
        for selector in selectors:
            candidates = page.locator(selector)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                if await candidate.is_visible() and not await candidate.evaluate(
                    "element => Boolean(element.closest('form'))"
                ):
                    try:
                        # The caller owns form discovery across both the current page and new
                        # tabs. Do not let Playwright's implicit navigation wait turn a
                        # successful target=_blank/SPA click into a fatal timeout.
                        await candidate.click(no_wait_after=True, timeout=5_000)
                    except PlaywrightTimeoutError:
                        # A portal can detach the CTA or start a navigation after receiving the
                        # click. Treat the action as attempted and let bounded form discovery
                        # determine whether it succeeded.
                        return True
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    except Exception:
                        # A modal or SPA form may appear without a document navigation.
                        pass
                    return True
        return False
