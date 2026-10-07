"""
conftest.py — Pytest fixtures for the Lenovo Qlik/PBI Validation Framework.

Architecture:
  - ONE persistent browser context per pytest session (stored at playwright/.auth/browser_profile).
  - Preserves all SSO cookies, tokens, IndexedDB, and active logins permanently across test runs.
  - In-line SSO auto-prompting: if login is required, tests pause headed for up to 60s for user login.
  - Warm page fixtures: Power BI and Qlik load ONCE per session.
  - Tests reuse the warm pages and only switch tabs or reset slicers (~1-2s per test).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

import pytest
import yaml
from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from pageobjects.qlik_dashboard_page import QlikDashboardPage
from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.logger import get_logger
from config.settings import (
    BROWSER_PROFILE_DIR,
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
        help="Run only Qlik tests, skipping Power BI interactions",
    )
    parser.addoption(
        "--pbi-only",
        action="store_true",
        default=False,
        help="Run only Power BI tests, skipping Qlik interactions",
    )


# ─────────────────────────────────────────────────────────────────────────────
# YAML Config (session-scoped)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def config(request) -> dict:
    """Load and return the scenario YAML config with smart default resolution."""
    config_opt = request.config.getoption("--config")
    cli_args = " ".join(request.config.args)

    # Automatically select scenario2 config if executing scenario2 tests without explicit override
    if config_opt == "dashboard_configs/scenario1_track1.yaml" and "scenario2" in cli_args:
        config_path = Path("dashboard_configs/scenario2_track2.yaml")
    else:
        config_path = Path(config_opt)

    if not config_path.exists():
        pytest.fail(
            f"Config file not found: '{config_path}'. "
            "Please check the path. For Track 2, use: --config=dashboard_configs/scenario2_track2.yaml"
        )
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    log.info(f"Loaded config: {config_path} (scenario={cfg.get('scenario')}, track={cfg.get('track')})")
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Persistent Browser Context (session-scoped)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def _playwright() -> Generator[Playwright, None, None]:
    """Session-scoped Playwright instance."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def persistent_browser_context(_playwright) -> Generator[BrowserContext, None, None]:
    """
    Session-scoped persistent browser context using BROWSER_PROFILE_DIR.
    Retains SSO sessions across runs without needing repeated capture scripts.
    """
    profile_dir = Path(BROWSER_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Launching persistent browser context from {profile_dir}")
    ctx = _playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=False,
        args=["--start-maximized"],
        no_viewport=True,
    )

    # If the profile directory was just created or empty, seed cookies from legacy session files
    for session_file in [QLIK_SESSION_FILE, PBI_SESSION_FILE]:
        sf = Path(session_file)
        if sf.exists():
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                cookies = data.get("cookies", [])
                if cookies:
                    ctx.add_cookies(cookies)
                    log.info(f"Seeded {len(cookies)} cookies into profile from {sf.name}")
            except Exception as e:
                log.warning(f"Could not seed cookies from {sf}: {e}")

    yield ctx
    ctx.close()
    log.info("Persistent browser context closed")


# Backward compatibility aliases
@pytest.fixture(scope="session")
def browser_instance(persistent_browser_context) -> BrowserContext:
    return persistent_browser_context


@pytest.fixture(scope="session")
def qlik_browser_context(persistent_browser_context) -> BrowserContext:
    return persistent_browser_context


@pytest.fixture(scope="session")
def pbi_browser_context(persistent_browser_context) -> BrowserContext:
    return persistent_browser_context


# ─────────────────────────────────────────────────────────────────────────────
# Warm Pages (session-scoped — loaded once per test run)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def warm_pbi_page(request, persistent_browser_context, config) -> Page:
    """
    Session-scoped warm Power BI page.
    Navigates to the report URL ONCE at session start.
    All tests share this warm tab, avoiding repeated 15s cold boots.
    """
    if request.config.getoption("--qlik-only"):
        pytest.skip("Power BI tests skipped (--qlik-only active)")

    report_url = config.get("pbi", {}).get("report_url")
    if not report_url:
        pytest.skip("No PBI report URL configured")

    # Find dedicated PBI page or allocate an unclaimed/new tab
    page = None
    for p in persistent_browser_context.pages:
        if getattr(p, "_platform", None) == "pbi":
            page = p
            break
        elif getattr(p, "_platform", None) is None and page is None:
            page = p

    if page is None:
        page = persistent_browser_context.new_page()

    page._platform = "pbi"
    dashboard = PBIDashboardPage(page)
    dashboard.open(report_url)
    return page


@pytest.fixture(scope="session")
def warm_qlik_page(request, persistent_browser_context, config) -> Page:
    """
    Session-scoped warm Qlik Cloud page.
    Navigates to the app/sheet ONCE at session start.
    """
    if request.config.getoption("--pbi-only"):
        pytest.skip("Qlik tests skipped (--pbi-only active)")

    app_id = config.get("qlik", {}).get("app_id")
    sheet_id = config.get("qlik", {}).get("primary_sheet_id")
    if not app_id or not sheet_id:
        pytest.skip("No Qlik app_id or primary_sheet_id configured")

    # Find dedicated Qlik page or allocate an unclaimed/new tab
    page = None
    for p in persistent_browser_context.pages:
        if getattr(p, "_platform", None) == "qlik":
            page = p
            break
        elif getattr(p, "_platform", None) is None and page is None:
            page = p

    if page is None:
        page = persistent_browser_context.new_page()

    page._platform = "qlik"
    dashboard = QlikDashboardPage(page)
    dashboard.open(app_id, sheet_id)
    return page


# ─────────────────────────────────────────────────────────────────────────────
# Page Objects & Pages (function-scoped — bound to the warm session pages)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def pbi_dashboard(warm_pbi_page) -> PBIDashboardPage:
    """PBIDashboardPage bound to the warm PBI page."""
    return PBIDashboardPage(warm_pbi_page)


@pytest.fixture(scope="function")
def qlik_dashboard(warm_qlik_page) -> QlikDashboardPage:
    """QlikDashboardPage bound to the warm Qlik page."""
    return QlikDashboardPage(warm_qlik_page)


@pytest.fixture(scope="function")
def pbi_page(warm_pbi_page) -> Page:
    """Direct reference to the warm PBI page."""
    return warm_pbi_page


@pytest.fixture(scope="function")
def qlik_page(warm_qlik_page) -> Page:
    """Direct reference to the warm Qlik page."""
    return warm_qlik_page
