import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.config import Settings

CAPTCHA_IFRAME_SELECTOR = "iframe[src*='recaptcha'], iframe[src*='hcaptcha']"
CAPTCHA_CONTAINER_SELECTOR = "[class*='captcha' i], [id*='captcha' i]"
AUTOMATION_SHIELD_ID = "jaa-automation-interaction-shield"
CAPTCHA_CHALLENGE_TEXT = (
    "verify you are human",
    "complete the captcha",
    "i'm not a robot",
    "i am not a robot",
    "ich bin kein roboter",
    "bestätigen sie, dass sie ein mensch sind",
    "sicherheitsüberprüfung abschließen",
)
COMPOUND_PUBLIC_SUFFIXES = {
    "ac.uk",
    "co.in",
    "co.jp",
    "co.kr",
    "co.nz",
    "co.uk",
    "co.za",
    "com.ar",
    "com.au",
    "com.br",
    "com.cn",
    "com.hk",
    "com.mx",
    "com.sg",
    "com.tr",
    "net.au",
    "org.au",
    "org.uk",
}


def same_site(first_url: str, second_url: str) -> bool:
    first = urlsplit(first_url)
    second = urlsplit(second_url)
    if first.scheme == "file" or second.scheme == "file":
        return first.scheme == second.scheme == "file"
    if not first.hostname or not second.hostname:
        return False

    def site_domain(hostname: str) -> str:
        lowered = hostname.rstrip(".").casefold()
        try:
            ipaddress.ip_address(lowered)
            return lowered
        except ValueError:
            pass
        labels = lowered.split(".")
        if len(labels) <= 2:
            return lowered
        last_two = ".".join(labels[-2:])
        if last_two in COMPOUND_PUBLIC_SUFFIXES and len(labels) >= 3:
            return ".".join(labels[-3:])
        return last_two

    return site_domain(first.hostname) == site_domain(second.hostname)


class UnsafeNavigationError(ValueError):
    pass


class BrowserManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._pages: dict[str, Page] = {}
        self._guarded_pages: set[Page] = set()

    async def context(self) -> BrowserContext:
        if self._context is None:
            self.settings.ensure_data_directories()
            self._playwright = await async_playwright().start()
            # A fixed emulated viewport can be taller than the visible headed Chrome window,
            # leaving bottom-of-form controls technically present but impossible for the user
            # to see or reach. Headless tests keep a deterministic viewport; the real browser
            # instead follows its maximized OS window.
            viewport = (
                {"width": 1440, "height": 1000}
                if self.settings.browser_headless
                else None
            )
            args = [] if self.settings.browser_headless else ["--start-maximized"]
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.settings.browser_profile_dir,
                headless=self.settings.browser_headless,
                slow_mo=self.settings.browser_slow_mo,
                viewport=viewport,
                args=args,
            )
        return self._context

    async def open(self, key: str, url: str) -> Page:
        await self._validate_navigation_url(url)
        context = await self.context()
        await self.forget(key)
        page = await context.new_page()
        await self._install_navigation_guard(page)
        # Track the page before navigation. If a slow employer page times out after rendering
        # useful content, the application service can inspect and recover it instead of losing
        # the only page reference and persisting an empty failed draft.
        self._pages[key] = page
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await self._validate_navigation_url(page.url)
        return page

    def page(self, key: str) -> Page | None:
        page = self._pages.get(key)
        return page if page and not page.is_closed() else None

    def open_pages(self) -> list[Page]:
        if self._context is None:
            return []
        return [page for page in self._context.pages if not page.is_closed()]

    async def adopt(self, key: str, page: Page) -> None:
        if page.is_closed() or self._context is None or page.context is not self._context:
            raise ValueError("Only an open page from the managed browser can be adopted")
        await self._install_navigation_guard(page)
        await self._validate_navigation_url(page.url)
        self._pages[key] = page

    async def forget(self, key: str, close: bool = True) -> None:
        page = self._pages.pop(key, None)
        if close and page and not page.is_closed():
            await page.close()
        if page:
            self._guarded_pages.discard(page)

    @asynccontextmanager
    async def protect_from_manual_input(self, page: Page):
        """Keep incidental clicks or scrolling from racing browser automation."""
        shield_id = AUTOMATION_SHIELD_ID
        await page.evaluate(
            """shieldId => {
              document.getElementById(shieldId)?.remove();
              const shield = document.createElement('div');
              shield.id = shieldId;
              shield.setAttribute('role', 'status');
              shield.setAttribute('aria-live', 'polite');
              Object.assign(shield.style, {
                position: 'fixed', inset: '0', zIndex: '2147483647',
                display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
                paddingTop: '18px', background: 'rgba(255,255,255,0.08)',
                cursor: 'progress', pointerEvents: 'all'
              });
              const message = document.createElement('div');
              message.textContent = 'Job Assistant is reading and filling this form. Please wait…';
              Object.assign(message.style, {
                padding: '10px 16px', borderRadius: '999px',
                background: '#17372c', color: '#fff',
                font: '600 14px system-ui, sans-serif',
                boxShadow: '0 6px 24px rgba(0,0,0,0.2)'
              });
              shield.append(message);
              document.documentElement.append(shield);
            }""",
            shield_id,
        )
        try:
            yield
        finally:
            if not page.is_closed():
                try:
                    await page.evaluate(
                        "shieldId => document.getElementById(shieldId)?.remove()", shield_id
                    )
                except Exception:
                    pass

    async def has_captcha(self, page: Page) -> bool:
        frames = page.locator(CAPTCHA_IFRAME_SELECTOR)
        for index in range(await frames.count()):
            frame = frames.nth(index)
            source = (await frame.get_attribute("src") or "").casefold()
            title = (await frame.get_attribute("title") or "").casefold()
            is_challenge_frame = "bframe" in source or "challenge" in title
            if "size=invisible" in source and not is_challenge_frame:
                continue
            if await frame.is_visible():
                return True

        containers = page.locator(CAPTCHA_CONTAINER_SELECTOR)
        for index in range(await containers.count()):
            container = containers.nth(index)
            if await container.evaluate(
                r"""element => {
                  const visible = candidate => {
                    const style = getComputedStyle(candidate);
                    const rect = candidate.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                      Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
                  };
                  if (!visible(element)) return false;
                  const identity = `${element.id || ''} ${element.className || ''}`.toLowerCase();
                  if (identity.includes('grecaptcha-badge')) return false;
                  const text = (element.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
                  if (/verify you are human|complete the captcha|i'?m not a robot|ich bin kein roboter/.test(text)) {
                    return true;
                  }
                  const controls = Array.from(element.querySelectorAll(
                    'button, input:not([type=hidden]), [role=checkbox]'
                  ));
                  if (controls.some(visible)) return true;
                  return Array.from(element.querySelectorAll('iframe')).some(frame => {
                    const source = (frame.getAttribute('src') || '').toLowerCase();
                    return visible(frame) && !source.includes('size=invisible');
                  });
                }"""
            ):
                return True
        content = (await page.locator("body").inner_text()).casefold()
        return any(marker in content for marker in CAPTCHA_CHALLENGE_TEXT)

    async def has_login(self, page: Page) -> bool:
        """Detect a portal account gate without mistaking a header sign-in link for one."""

        async def has_visible(selector: str) -> bool:
            matches = page.locator(selector)
            for index in range(await matches.count()):
                if await matches.nth(index).is_visible():
                    return True
            return False

        if await has_visible(
            "input[type='password'], input[autocomplete='current-password'], "
            "input[autocomplete='new-password'], input[autocomplete='one-time-code']"
        ):
            return True

        has_application_form = await has_visible(
            "input[type='file'], textarea, form[action*='apply' i], "
            "form[action*='application' i]"
        )
        if has_application_form:
            return False

        content = " ".join((await page.locator("body").inner_text()).casefold().split())
        title = (await page.title()).casefold()
        path = urlsplit(page.url).path.casefold()
        account_markers = (
            "sign in",
            "log in",
            "login",
            "sign up",
            "create account",
            "create a profile",
            "register",
            "candidate account",
            "applicant account",
            "verify your email",
            "verification code",
            "one-time code",
            "anmelden",
            "einloggen",
            "registrieren",
            "konto erstellen",
            "profil erstellen",
            "bewerberkonto",
            "e-mail bestätigen",
            "bestätigungscode",
        )
        has_account_language = any(marker in content for marker in account_markers)
        if not has_account_language:
            return False

        has_identity_input = await has_visible(
            "input[type='email'], input[autocomplete='email'], input[autocomplete='username'], "
            "input[name*='email' i], input[name*='user' i]"
        )
        has_auth_form = await has_visible(
            "form[action*='login' i], form[action*='signin' i], form[action*='sign-in' i], "
            "form[action*='register' i], form[action*='signup' i], form[action*='account' i]"
        )
        route_or_title_is_auth = any(
            marker in path or marker in title
            for marker in (
                "login",
                "log in",
                "signin",
                "sign in",
                "sign-in",
                "signup",
                "sign-up",
                "register",
                "account",
                "verify",
                "verification",
                "anmelden",
                "registrieren",
                "bestätig",
            )
        )
        explicit_apply_gate = any(
            marker in content
            for marker in (
                "sign in to apply",
                "log in to apply",
                "create an account to apply",
                "register to apply",
                "zum bewerben anmelden",
                "konto erstellen, um sich zu bewerben",
            )
        )
        return has_identity_input or has_auth_form or route_or_title_is_auth or explicit_apply_gate

    async def capture_failure(self, page: Page, key: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_key = "".join(char for char in key if char.isalnum() or char in "-_")[:80]
        path = self.settings.screenshot_dir / f"{timestamp}-{safe_key}.png"
        self.settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=path, full_page=True)
        return path

    async def close(self) -> None:
        self._pages.clear()
        self._guarded_pages.clear()
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        self._context = None
        self._playwright = None

    async def _install_navigation_guard(self, page: Page) -> None:
        if page in self._guarded_pages:
            return

        async def guard_top_level_navigation(route) -> None:
            request = route.request
            if request.is_navigation_request() and request.frame == page.main_frame:
                try:
                    await self._validate_navigation_url(request.url)
                except UnsafeNavigationError:
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await page.route("**/*", guard_top_level_navigation)
        self._guarded_pages.add(page)

    async def _validate_navigation_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme == "file" and self.settings.browser_headless:
            # Local fixture navigation is reserved for automated development tests.
            return
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeNavigationError("Only public HTTP(S) application URLs are allowed")

        allowed_local = urlsplit(self.settings.app_base_url)
        if parsed.hostname == allowed_local.hostname and (
            parsed.port or _default_port(parsed.scheme)
        ) == (allowed_local.port or _default_port(allowed_local.scheme)):
            return

        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise UnsafeNavigationError(
                    "Private, local, and metadata network targets are blocked"
                )
            return

        port = parsed.port or _default_port(parsed.scheme)
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            port,
            type=socket.SOCK_STREAM,
        )
        resolved = {ipaddress.ip_address(item[4][0]) for item in addresses}
        if not resolved or any(not address.is_global for address in resolved):
            raise UnsafeNavigationError(
                "The URL resolves to a private or non-public network target"
            )


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80
