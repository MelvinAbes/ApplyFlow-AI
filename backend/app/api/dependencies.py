from app.automation.browser import BrowserManager
from app.config import get_settings

browser_manager = BrowserManager(get_settings())


def get_browser_manager() -> BrowserManager:
    return browser_manager
