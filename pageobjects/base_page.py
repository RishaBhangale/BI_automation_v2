from __future__ import annotations
from playwright.sync_api import Page
from utils.logger import get_logger
import os
from datetime import datetime


class BasePage:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.log = get_logger(self.__class__.__name__)

    def goto(self, url: str, timeout: int = 90_000) -> None:
        self.log.info(f"Navigating to {url}")
        self.page.goto(url, timeout=timeout)

    def get_title(self) -> str:
        return self.page.title()

    def get_url(self) -> str:
        return self.page.url

    def capture_screenshot(self, name: str = "screenshot") -> str:
        os.makedirs("screenshots", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"screenshots/{name}_{ts}.png"
        self.page.screenshot(path=path)
        self.log.info(f"Screenshot: {path}")
        return path
