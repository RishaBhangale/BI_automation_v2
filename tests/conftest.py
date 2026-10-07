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

import base64
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Generator, Dict, List, Optional

import pytest
import yaml
from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from pageobjects.qlik_dashboard_page import QlikDashboardPage
from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.logger import get_logger
from utils.report_generator import TestResult, generate_report
from config.settings import (
    BROWSER_PROFILE_DIR,
    QLIK_SESSION_FILE,
    PBI_SESSION_FILE,
    BROWSER_WIDTH,
    BROWSER_HEIGHT,
    REPORT_DIR,
)

log = get_logger("conftest")

# ═══════════════════════════════════════════════════════════════════════════════
# Dashboard report state (session-level, populated by pytest hooks)
# ═══════════════════════════════════════════════════════════════════════════════
DASHBOARD_RESULTS: List[TestResult] = []
_dash_test_start_times: Dict[str, float] = {}
_dash_log_records: Dict[str, list] = {}
_current_dashboard_config: Optional[dict] = None


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
    global _current_dashboard_config
    _current_dashboard_config = cfg
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


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: track test start times
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _dash_test_start_times[item.nodeid] = datetime.now().timestamp()
    yield


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: capture log records during each test
# ═══════════════════════════════════════════════════════════════════════════════

class _DashListLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    handler = _DashListLogHandler()
    handler.setLevel(logging.DEBUG)

    target_loggers = [
        logging.getLogger("test_functional_parity"),
        logging.getLogger("test_number_matching"),
        logging.getLogger("qlik_dashboard_page"),
        logging.getLogger("pbi_dashboard_page"),
        logging.getLogger("conftest"),
        logging.getLogger("validation_utils"),
        logging.getLogger("csv_engine"),
    ]
    for lg in target_loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)

    yield

    for lg in target_loggers:
        lg.removeHandler(handler)

    _dash_log_records[item.nodeid] = handler.records


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers: Step parser
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_steps_from_logs(log_records: list) -> list[dict]:
    """
    Parse STEP marker log lines into structured step dicts.
    Looks for:
        STEP 1: ... or Step 1: ...
        STEP1_START|... and STEP1_END
    """
    steps = []
    current = None

    technical_keywords = [
        "iframe", "flat-DOM", "PTW", "selector failed", "trying fallback",
        "trying outer page", "DOM diagnostic", "reset attempt complete",
        "no clear action taken", "slicer dropdown expanded", "popup items ready",
        "Listbox heights", "Active popup", "Slicer item clicked via selector",
        "closed after Escape", "did not close via Escape", "Force-clicked",
        "already at All", "no clear option in dropdown"
    ]

    for record in log_records:
        msg   = record.getMessage()
        level = record.levelname
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        step_match  = re.match(r"(?:STEP|Step)\s*(\d+(?:\.\d+)?|\d+)[\s:_|\-]+(.+)", msg)
        start_match = re.match(r"STEP(\d+(?:\.\d+)?|\d+)_START\|(.+)", msg)
        end_match   = re.match(r"STEP(\d+(?:\.\d+)?|\d+)_END", msg)

        if start_match:
            current = {
                "step_no": len(steps) + 1,
                "title": start_match.group(2).strip(),
                "lines": [],
                "failed": False,
            }
            steps.append(current)
            continue

        if step_match:
            current = {
                "step_no": len(steps) + 1,
                "title": step_match.group(2).strip(),
                "lines": [],
                "failed": False,
            }
            steps.append(current)
            current["lines"].append((level, ts, msg))
            continue

        if end_match:
            current = None
            continue

        if current is not None:
            is_fail = (
                level in ("ERROR", "CRITICAL")
                or msg.startswith("FAIL")
                or "AssertionError" in msg
            )
            if is_fail:
                current["failed"] = True

            if level == "DEBUG":
                continue

            if any(kw in msg for kw in technical_keywords) and not is_fail:
                continue

            current["lines"].append((level, ts, msg))
        else:
            if not steps:
                current = {
                    "step_no": 1,
                    "title": "Execution & Verification",
                    "lines": [],
                    "failed": False,
                }
                steps.append(current)
            if level != "DEBUG":
                current["lines"].append((level, ts, msg))

    return steps


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: build TestResult after each test completes
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report  = outcome.get_result()

    if report.when != "call":
        return

    start    = _dash_test_start_times.get(item.nodeid, datetime.now().timestamp())
    duration = datetime.now().timestamp() - start

    test_outcome = report.outcome          # "passed" | "failed" | "skipped"
    log_records  = _dash_log_records.get(item.nodeid, [])
    steps        = _parse_steps_from_logs(log_records)

    dash_name = (
        _current_dashboard_config.get("description")
        or _current_dashboard_config.get("name")
        or "Pipeline Hygiene"
        if _current_dashboard_config else "Pipeline Hygiene"
    )

    raw_name = item.name
    param_match = re.search(r'\[(?:chromium-|firefox-|webkit-)?(.*?)\]', raw_name)
    if param_match:
        param_str = param_match.group(1).strip()
        if " - " in param_str:
            parts = param_str.split(" - ", 1)
            tc_id = parts[0].strip()
            clean_test_title = parts[1].strip()
        elif param_str.startswith("FP-") or param_str.startswith("NM-") or param_str.startswith("TC-"):
            tc_id = param_str.strip()
            clean_test_title = item.name.split('[')[0].replace('test_', '').replace('_', ' ').title()
        else:
            tc_id = f"TC-{len(DASHBOARD_RESULTS) + 1:03d}"
            clean_test_title = param_str
    elif "test_qlik_sheet_list_is_discoverable" in item.name:
        tc_id = "FP-A-000"
        clean_test_title = "Dynamic Qlik Sheet Discovery"
    elif "test_pbi_page_list_is_discoverable" in item.name:
        tc_id = "FP-A-000b"
        clean_test_title = "Dynamic PBI Page Discovery"
    elif "test_sheet_count_parity" in item.name:
        tc_id = "FP-A-001"
        clean_test_title = "Sheet/Page Existence Parity"
    else:
        tc_id = f"TC-{len(DASHBOARD_RESULTS) + 1:03d}"
        clean_test_title = item.name.replace('test_', '').replace('_', ' ').title()

    readable_name = f"{clean_test_title} — {dash_name}"

    # Determine group
    if "FP-A" in tc_id:
        group = "CATEGORY A — DISCOVERY & PARITY"
    elif "FP-B" in tc_id:
        group = "CATEGORY B — VISUAL EXISTENCE"
    elif "FP-C" in tc_id:
        group = "CATEGORY C — FILTER / SLICER EXISTENCE"
    elif "FP-D" in tc_id:
        group = "CATEGORY D — FILTER IMPACT PROPAGATION"
    elif "FP-E" in tc_id:
        group = "CATEGORY E — NAVIGATION & BUTTONS"
    elif "FP-F" in tc_id:
        group = "CATEGORY F — CROSS-FILTER BEHAVIOR"
    elif "FP-G" in tc_id:
        group = "CATEGORY G — FILTER CLEAR & STATE ISOLATION"
    elif "NM-" in tc_id:
        group = "SCENARIO 2 — NUMBER MATCHING"
    else:
        group = "GENERAL VALIDATION"

    # Capture error text + mark last step failed
    error_text = ""
    if report.failed:
        longrepr = str(report.longrepr) if report.longrepr else ""
        error_text = longrepr.strip()

        if steps:
            last_step = steps[-1]
            if not last_step.get("failed"):
                last_step["failed"] = True
                now_str = datetime.now().strftime("%H:%M:%S")
                error_lines = longrepr.split("\n")
                summary_line = "Test failed"
                for line in error_lines:
                    if line.startswith("E   AssertionError:"):
                        summary_line = line.replace("E   AssertionError:", "").strip()
                        break
                    elif "assert" in line.lower() and "E " in line:
                        summary_line = line.replace("E   ", "").strip()
                        break
                last_step["lines"].append(("FAIL", now_str, summary_line))

    # Screenshot on failure
    screenshot_b64 = ""
    if report.failed:
        pbi = item.funcargs.get("pbi_dashboard") or item.funcargs.get("pbi_page")
        qlik = item.funcargs.get("qlik_dashboard") or item.funcargs.get("qlik_page")
        page_obj = getattr(pbi, "page", pbi) or getattr(qlik, "page", qlik)
        if page_obj and hasattr(page_obj, "screenshot"):
            try:
                png_bytes = page_obj.screenshot(full_page=False)
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception as e:
                log.warning(f"Could not capture failure screenshot: {e}")

    DASHBOARD_RESULTS.append(TestResult(
        tc_id          = tc_id,
        name           = readable_name,
        outcome        = test_outcome,
        duration       = duration,
        error_text     = error_text,
        screenshot_b64 = screenshot_b64,
        steps          = steps,
        group          = group,
    ))


# ═══════════════════════════════════════════════════════════════════════════════
# Report hook: generate HTML report at session end
# ═══════════════════════════════════════════════════════════════════════════════

def pytest_sessionfinish(session, exitstatus):
    if not DASHBOARD_RESULTS:
        return

    dash_name = (
        _current_dashboard_config.get("description")
        or _current_dashboard_config.get("name")
        or "Pipeline Hygiene"
        if _current_dashboard_config else "Pipeline Hygiene"
    )
    dash_url = (
        _current_dashboard_config.get("pbi", {}).get("report_url", "")
        if _current_dashboard_config else ""
    )
    config_file = Path(session.config.getoption("--config") or "scenario1_track1.yaml").name

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dash_slug   = (dash_name or "dashboard").lower().replace(" ", "_")[:30]
    report_name = f"dashboard_validation_{dash_slug}_{timestamp}.html"

    reports_dir = Path(REPORT_DIR)
    html_reports_dir = reports_dir / "HTML_reports"
    html_reports_dir.mkdir(parents=True, exist_ok=True)

    output_path = html_reports_dir / report_name
    latest_html = html_reports_dir / "dashboard_validation_latest.html"
    root_latest_html = reports_dir / "dashboard_validation_latest.html"

    try:
        generate_report(
            results          = DASHBOARD_RESULTS,
            output_path      = str(output_path),
            project          = "Lenovo Qlik ↔ Power BI Migration",
            environment      = "Production / Qlik Cloud & PBI Service",
            release          = "Track 1: Functional Parity",
            suite            = "Qlik Cloud ↔ Power BI Governance Suite",
            base_url         = dash_url or "https://5wnjr4er56khtqe.in.qlikcloud.com",
            browser          = "Google Chrome (Headed)",
            viewport         = f"{BROWSER_WIDTH} × {BROWSER_HEIGHT}",
            executed_by      = "qe.automation",
            test_data_source = config_file,
        )

        # Maintain latest copies
        shutil.copy2(str(output_path), str(latest_html))
        shutil.copy2(str(output_path), str(root_latest_html))
        log.info(f"Dashboard validation HTML report saved → {output_path}")
        log.info(f"Latest report link                     → {latest_html}")

        # Export structured JSON results
        json_results = []
        for r in DASHBOARD_RESULTS:
            dur_mins, dur_secs = divmod(int(r.duration), 60)
            dur_display = f"{dur_mins}m {dur_secs:02d}s" if dur_mins > 0 else f"{int(r.duration)}s"
            json_results.append({
                "tc_id": r.tc_id,
                "name": r.name,
                "status": r.outcome,
                "duration": dur_display,
                "duration_secs": round(r.duration, 2),
                "error_text": r.error_text,
                "steps": r.steps,
                "group": r.group,
                "screenshot_b64": r.screenshot_b64,
            })

        latest_json = html_reports_dir / "dashboard_validation_latest.json"
        root_latest_json = reports_dir / "dashboard_validation_latest.json"
        json_data = json.dumps(json_results, indent=2, ensure_ascii=False)
        latest_json.write_text(json_data, encoding="utf-8")
        root_latest_json.write_text(json_data, encoding="utf-8")
        log.info(f"JSON execution results exported        → {latest_json}")

    except Exception as e:
        log.error(f"Failed to generate dashboard HTML report: {e}")

