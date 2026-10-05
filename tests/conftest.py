"""
conftest.py — Pytest fixtures for the Lenovo Qlik/PBI Validation Framework.

Session Architecture:
  - ONE browser instance per pytest session (shared across all tests)
  - ONE Qlik browser context (loaded with saved Qlik session cookies)
  - ONE PBI browser context (loaded with saved PBI session cookies)
  - Each test gets a FRESH PAGE (tab) from the context → no state leakage between tests

This means:
  - SSO login happens EXACTLY ONCE (at session capture time, not during tests)
  - Tests start instantly — no login flow per test
  - Context closes cleanly after all tests finish

PREREQUISITES:
  python scripts/capture_qlik_session.py   ← run once manually
  python scripts/capture_pbi_session.py    ← run once manually
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from pageobjects.qlik_dashboard_page import QlikDashboardPage
from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.logger import get_logger
from config.settings import (
    QLIK_SESSION_FILE,
    PBI_SESSION_FILE,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
)

log = get_logger("conftest")


# ─────────────────────────────────────────────────────────────────────────────
# CLI Options
# ─────────────────────────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--config",
        action="store",
        default="dashboard_configs/scenario1_track1.yaml",
        help="Path to the scenario YAML config file",
    )
    parser.addoption(
        "--qlik-only",
        action="store_true",
        default=False,
        help="Run only Qlik tests, skipping Power BI interactions when PBI report is down",
    )


# ─────────────────────────────────────────────────────────────────────────────
# YAML Config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def config(request) -> dict:
    """Load and return the scenario YAML config."""
    config_path = Path(request.config.getoption("--config"))
    if not config_path.exists():
        pytest.skip(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    log.info(f"Loaded config: {config_path} (scenario={cfg.get('scenario')}, track={cfg.get('track')})")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Browser (session-scoped)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def _playwright():
    """Session-scoped Playwright instance."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser_instance(_playwright):
    """Session-scoped Chrome browser — shared across all tests."""
    browser = _playwright.chromium.launch(
        headless=False,
        channel="chrome",
        args=["--start-maximized"],
    )
    log.info("Browser launched")
    yield browser
    browser.close()
    log.info("Browser closed")


# ─────────────────────────────────────────────────────────────────────────────
# Browser Contexts (session-scoped — one per platform)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def qlik_browser_context(browser_instance) -> BrowserContext:
    """
    Qlik browser context pre-loaded with saved session cookies.
    Shared across all Qlik tests. Avoids repeated login.
    """
    session_file = Path(QLIK_SESSION_FILE)
    if not session_file.exists():
        pytest.skip(
            f"Qlik session not found: {session_file}\n"
            "Run: python scripts/capture_qlik_session.py"
        )

    ctx = browser_instance.new_context(
        storage_state=str(session_file),
        viewport={"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT},
        no_viewport=True,
    )
    log.info(f"Qlik context created from session: {session_file}")
    yield ctx
    ctx.close()


@pytest.fixture(scope="session")
def pbi_browser_context(request, browser_instance) -> BrowserContext:
    """
    PBI browser context pre-loaded with saved session cookies.
    Shared across all PBI tests. Avoids repeated login.
    """
    if request.config.getoption("--qlik-only"):
        pytest.skip("Power BI tests skipped (--qlik-only active)")

    session_file = Path(PBI_SESSION_FILE)
    if not session_file.exists():
        pytest.skip(
            f"PBI session not found: {session_file}\n"
            "Run: python scripts/capture_pbi_session.py"
        )

    ctx = browser_instance.new_context(
        storage_state=str(session_file),
        viewport={"width": BROWSER_WIDTH, "height": BROWSER_HEIGHT},
        no_viewport=True,
    )
    log.info(f"PBI context created from session: {session_file}")
    yield ctx
    ctx.close()


# ─────────────────────────────────────────────────────────────────────────────
# Pages (function-scoped — fresh tab per test)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def qlik_page(qlik_browser_context) -> Page:
    """Fresh Qlik tab for each test. Closed after the test completes."""
    page = qlik_browser_context.new_page()
    yield page
    page.close()


@pytest.fixture(scope="function")
def pbi_page(pbi_browser_context) -> Page:
    """Fresh PBI tab for each test. Closed after the test completes."""
    page = pbi_browser_context.new_page()
    yield page
    page.close()


# ─────────────────────────────────────────────────────────────────────────────
# Page Objects (function-scoped)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def qlik_dashboard(qlik_page) -> QlikDashboardPage:
    """QlikDashboardPage bound to the test's Qlik page."""
    return QlikDashboardPage(qlik_page)


@pytest.fixture(scope="function")
def pbi_dashboard(pbi_page) -> PBIDashboardPage:
    """PBIDashboardPage bound to the test's PBI page."""
    return PBIDashboardPage(pbi_page)
