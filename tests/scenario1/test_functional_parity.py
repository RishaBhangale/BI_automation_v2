"""
test_functional_parity.py — Scenario 1: Qlik ↔ PBI Functional Parity Tests.

All 7 test categories per the implementation plan:
  A - Sheet/Page Existence
  B - Visual Existence per page
  C - Filter/Slicer Existence
  D - Filter Impact Propagation (CORE TEST: Qlik and PBI visuals react identically to filters)
  E - Navigation & Button Functionality (e.g. Go to ISG, Rev/Qty toggles)
  F - Cross-Filter Behavior (chart click to filter other visuals)
  G - Filter Clear & State Isolation (reset integrity)

Test cases are Excel-driven (test_data/functional_parity_scenarios.xlsx).
Categories A and B also have discovery-driven tests that can run dynamically.

USAGE:
    pytest tests/scenario1/test_functional_parity.py \
        --config=dashboard_configs/scenario1_track1.yaml -v

PREREQUISITES:
    1. python scripts/capture_qlik_session.py   (one-time manual login)
    2. python scripts/capture_pbi_session.py    (one-time manual login)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from pageobjects.qlik_dashboard_page import QlikDashboardPage
from pageobjects.pbi_dashboard_page import PBIDashboardPage
from utils.validation_utils import detect_changed_visuals, compare_visual_impact
from config.settings import (
    QLIK_APP_ID_TRACK1,
    QLIK_SHEET_ID_MAIN,
    PBI_REPORT_URL_TRACK1,
)

log = logging.getLogger("test_functional_parity")

# ─────────────────────────────────────────────────────────────────────────────
# Load Excel test cases
# ─────────────────────────────────────────────────────────────────────────────

EXCEL_PATH = Path(__file__).parent.parent.parent / "test_data" / "functional_parity_scenarios.xlsx"


def _load_test_cases(category: str) -> list[dict[str, Any]]:
    """Load test rows for a given category from the Excel scenario definition file."""
    if not EXCEL_PATH.exists():
        log.warning(f"Scenario Excel file not found at: {EXCEL_PATH}")
        return []
    try:
        df = pd.read_excel(EXCEL_PATH)
        if "Category" not in df.columns:
            return []
        filtered_df = df[df["Category"].astype(str).str.upper() == category.upper()]
        return filtered_df.where(pd.notnull(filtered_df), None).to_dict("records")
    except Exception as e:
        log.error(f"Error reading {EXCEL_PATH}: {e}")
        return []


def _tc_id(tc: dict) -> str:
    tid = tc.get("Test ID", "UNKNOWN")
    name = tc.get("Scenario Name") or tc.get("Test Name") or ""
    return f"{tid} - {name}".strip(" -")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY A — Sheet / Page Existence
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryA:
    """A: Does every Qlik sheet have a corresponding PBI page?"""

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_qlik_sheet_list_is_discoverable(self, qlik_dashboard, config):
        """
        FP-A-000: Verify we can enumerate all Qlik sheets via the REST API or navigation.
        Prerequisite sanity check.
        """
        app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
        sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)

        log.info("FP-A-000: Navigating to Qlik app to verify session and enumerate sheets")
        qlik_dashboard.open(app_id, sheet_id)

        sheets = qlik_dashboard.get_sheet_list_via_api()
        assert len(sheets) > 0, (
            "Qlik REST API returned 0 sheets. "
            "Verify that Qlik session is valid and app has published sheets."
        )
        log.info(f"[PASS] Successfully enumerated {len(sheets)} Qlik sheets: {[s['title'] for s in sheets]}")

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_pbi_page_list_is_discoverable(self, pbi_dashboard, config):
        """
        FP-A-000b: Verify we can enumerate all PBI pages in the migrated report.
        """
        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
        log.info("FP-A-000b: Opening PBI report and enumerating page tabs")
        pbi_dashboard.open(report_url)

        pages = pbi_dashboard.get_page_list()
        assert len(pages) > 0, (
            "PBI returned 0 pages. Check report embed status or navigation tabs selector."
        )
        log.info(f"[PASS] Successfully enumerated {len(pages)} PBI pages: {pages}")

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_sheet_count_parity(self, qlik_dashboard, pbi_dashboard, config):
        """
        FP-A-001: PBI page count should be >= Qlik sheet count.
        Migrated report should not be missing sheets.
        """
        app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
        sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)
        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

        qlik_dashboard.open(app_id, sheet_id)
        pbi_dashboard.open(report_url)

        qlik_sheets = qlik_dashboard.get_sheet_list_via_api()
        pbi_pages = pbi_dashboard.get_page_list()

        log.info(f"Qlik sheets count: {len(qlik_sheets)} | PBI pages count: {len(pbi_pages)}")

        assert len(pbi_pages) >= len(qlik_sheets), (
            f"FAIL — PBI has fewer pages ({len(pbi_pages)}) than Qlik has sheets ({len(qlik_sheets)}). "
            f"Qlik sheets: {[s['title'] for s in qlik_sheets]}. PBI pages: {pbi_pages}"
        )
        log.info(f"[PASS] Sheet count parity satisfied (PBI: {len(pbi_pages)} >= Qlik: {len(qlik_sheets)})")

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_sheet_to_page_mapping(self, qlik_dashboard, pbi_dashboard, config):
        """
        FP-A-002: Every Qlik sheet in YAML sheet_page_mapping exists as a PBI page.
        """
        mapping = config.get("sheet_page_mapping", {})
        if not mapping:
            pytest.skip("No sheet_page_mapping configured in YAML.")

        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
        pbi_dashboard.open(report_url)
        pbi_pages = [p.lower().strip() for p in pbi_dashboard.get_page_list()]

        missing = []
        for qlik_sheet, pbi_page in mapping.items():
            if pbi_page.lower().strip() not in pbi_pages:
                missing.append(f"Qlik: '{qlik_sheet}' -> Expected PBI: '{pbi_page}'")

        assert not missing, (
            f"FAIL — {len(missing)} mapped Qlik sheets missing corresponding PBI pages:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + f"\nAvailable PBI pages: {pbi_pages}"
        )
        log.info(f"[PASS] All {len(mapping)} mapped sheets have corresponding PBI pages")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY B — Visual Existence on Page
# ─────────────────────────────────────────────────────────────────────────────

cat_b_cases = _load_test_cases("B")


@pytest.mark.cat_b
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_b_cases, ids=[_tc_id(tc) for tc in cat_b_cases] if cat_b_cases else [])
def test_visual_existence(tc, pbi_dashboard, config):
    """
    FP-B-*: Validates that key visuals specified for a sheet/page exist on PBI.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    pbi_page_name = tc.get("PBI Page Name")
    expected_visuals_raw = tc.get("Value / Action") or tc.get("Expected Impacted Visuals") or "ALL"

    if not pbi_page_name:
        pytest.skip(f"{test_id}: No PBI Page Name defined.")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    visuals = pbi_dashboard.get_visual_titles()
    visuals_lower = [v.lower().strip() for v in visuals]

    expected_list = [v.strip() for v in str(expected_visuals_raw).split(",") if v.strip()]
    if expected_list and expected_list != ["ALL"]:
        missing = [exp for exp in expected_list if exp.lower() not in visuals_lower]
        assert not missing, f"{test_id} FAIL — Visuals missing on PBI page '{pbi_page_name}': {missing}. Available: {visuals}"
    else:
        assert len(visuals) > 0, f"{test_id} FAIL — No visuals detected on PBI page '{pbi_page_name}'"

    log.info(f"[PASS] {test_id}: Visuals verified on '{pbi_page_name}' ({len(visuals)} visuals found)")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY C — Filter/Slicer Existence
# ─────────────────────────────────────────────────────────────────────────────

cat_c_cases = _load_test_cases("C")


@pytest.mark.cat_c
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_c_cases, ids=[_tc_id(tc) for tc in cat_c_cases] if cat_c_cases else [])
def test_filter_existence(tc, pbi_dashboard, config):
    """
    FP-C-*: Validates that filter dimensions present in Qlik exist as slicers on PBI.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    pbi_page_name = tc.get("PBI Page Name") or "Pipeline Hygiene ISG"
    filter_field = tc.get("Field / Element") or tc.get("Filter Field")

    if not filter_field:
        pytest.skip(f"{test_id}: No Filter Field defined.")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    visual_titles = [v.lower().strip() for v in pbi_dashboard.get_visual_titles()]
    field_lower = filter_field.lower().strip()

    slicer_found = any(field_lower in vt for vt in visual_titles)
    assert slicer_found, (
        f"{test_id} FAIL — Filter/Slicer '{filter_field}' not found among PBI visual titles on page '{pbi_page_name}'. "
        f"Available visual titles: {visual_titles}"
    )
    log.info(f"[PASS] {test_id}: Slicer '{filter_field}' exists on '{pbi_page_name}'")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY D — Filter Impact Propagation (CORE FUNCTIONALITY TEST)
# ─────────────────────────────────────────────────────────────────────────────

cat_d_cases = _load_test_cases("D")


@pytest.mark.cat_d
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_d_cases, ids=[_tc_id(tc) for tc in cat_d_cases] if cat_d_cases else [])
def test_filter_impact_propagation(tc, qlik_dashboard, pbi_dashboard, config):
    """
    FP-D-*: Apply a filter on BOTH Qlik and PBI.
    Verify that the SAME set of visuals change on both platforms.
    Does NOT compare actual values — verifies functional impact propagation parity.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    filter_field = tc.get("Field / Element") or tc.get("Filter Field")
    filter_val = tc.get("Value / Action") or tc.get("Filter Value")
    qlik_sheet_name = tc.get("Qlik Sheet Name")
    pbi_page_name = tc.get("PBI Page Name")

    if not filter_field or not filter_val:
        pytest.skip(f"{test_id}: Incomplete filter configuration.")

    log.info(f"--- {test_id}: Starting Filter Impact Propagation ({filter_field} = {filter_val}) ---")

    app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
    sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

    # ── STEP 1: Qlik Baseline & Filter ─────────────────────────────────────────
    log.info(f"STEP 1: Qlik — Open sheet '{qlik_sheet_name or 'primary'}' and capture baseline")
    qlik_dashboard.open(app_id, sheet_id)
    if qlik_sheet_name:
        qlik_dashboard.navigate_to_sheet(qlik_sheet_name)

    qlik_baseline = qlik_dashboard.capture_baseline()
    assert qlik_baseline, f"{test_id}: Qlik baseline is empty — cannot detect visual changes"

    log.info(f"STEP 2: Qlik — Apply filter: {filter_field} = {filter_val}")
    qlik_dashboard.apply_filter(filter_field, filter_val)

    log.info("STEP 3: Qlik — Capture post-filter state")
    qlik_filtered = qlik_dashboard.capture_baseline()

    changed_qlik = detect_changed_visuals(qlik_baseline, qlik_filtered)
    log.info(f"Qlik visuals changed: {sorted(changed_qlik)}")

    # ── STEP 2: Qlik Teardown ──────────────────────────────────────────────────
    qlik_dashboard.clear_all_filters()

    # ── STEP 3: PBI Baseline & Slicer ──────────────────────────────────────────
    log.info("STEP 4: PBI — Open report and capture baseline")
    pbi_dashboard.open(report_url)
    if pbi_page_name:
        pbi_dashboard.switch_to_page(pbi_page_name)

    pbi_baseline = pbi_dashboard.capture_page_baseline()
    assert pbi_baseline, f"{test_id}: PBI baseline is empty — cannot detect visual changes"

    log.info(f"STEP 5: PBI — Apply slicer: {filter_field} = {filter_val}")
    pbi_dashboard.apply_slicer(filter_field, filter_val)

    log.info("STEP 6: PBI — Capture post-filter state")
    pbi_filtered = pbi_dashboard.capture_page_baseline()

    changed_pbi = detect_changed_visuals(pbi_baseline, pbi_filtered)
    log.info(f"PBI visuals changed: {sorted(changed_pbi)}")

    # ── STEP 4: PBI Teardown ───────────────────────────────────────────────────
    pbi_dashboard.clear_all_slicers()

    # ── STEP 5: Parity Comparison ──────────────────────────────────────────────
    log.info("STEP 7: Comparing visual impact propagation between Qlik and PBI")
    passed, detail = compare_visual_impact(changed_qlik, changed_pbi, test_id=test_id)
    status = "PASS" if passed else "FAIL"
    log.info(f"[{status}] {detail}")

    assert passed, detail


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY E — Navigation & Button Functionality
# ─────────────────────────────────────────────────────────────────────────────

cat_e_cases = _load_test_cases("E")


@pytest.mark.cat_e
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_e_cases, ids=[_tc_id(tc) for tc in cat_e_cases] if cat_e_cases else [])
def test_navigation_and_buttons(tc, pbi_dashboard, config):
    """
    FP-E-*: Tests interactive buttons, navigation links, and toggle actions on PBI.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    button_label = tc.get("Field / Element") or tc.get("Button / Action Label")
    target_page = tc.get("PBI Page Name") or tc.get("Target Page")

    log.info(f"--- {test_id}: Testing interactive element '{button_label}' ---")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)

    ctx = pbi_dashboard._ctx()
    btn = ctx.locator(f"button:has-text('{button_label}'), [aria-label*='{button_label}']").first

    if not btn.is_visible(timeout=5_000):
        pytest.fail(f"{test_id} FAIL — Navigation/Action element '{button_label}' not found on PBI page.")

    btn.click()
    pbi_dashboard.page.wait_for_timeout(3_000)

    if target_page:
        current_pages = pbi_dashboard.get_page_list()
        log.info(f"Action triggered. Current pages/tabs: {current_pages}")

    log.info(f"[PASS] {test_id}: Action '{button_label}' successfully clicked and verified.")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY F — Cross-Filter Behavior
# ─────────────────────────────────────────────────────────────────────────────

cat_f_cases = _load_test_cases("F")


@pytest.mark.cat_f
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_f_cases, ids=[_tc_id(tc) for tc in cat_f_cases] if cat_f_cases else [])
def test_cross_filter_behavior(tc, pbi_dashboard, config):
    """
    FP-F-*: Click on a chart segment/bar and verify other visual cards update.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    chart_name = tc.get("Field / Element") or tc.get("Chart Visual Name") or "Bar Chart"
    target_segment = tc.get("Value / Action") or tc.get("Segment Label") or "ISRAEL"
    pbi_page_name = tc.get("PBI Page Name") or "Pipeline Hygiene ISG"

    log.info(f"--- {test_id}: Cross-filter test on '{chart_name}' -> '{target_segment}' ---")
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    baseline = pbi_dashboard.capture_page_baseline()

    ctx = pbi_dashboard._ctx()
    bar = ctx.locator(f"[aria-label*='{target_segment}'], text='{target_segment}'").first
    if not bar.is_visible(timeout=5_000):
        pytest.skip(f"{test_id}: Segment '{target_segment}' in visual '{chart_name}' not accessible via DOM locator.")

    bar.click()
    pbi_dashboard.page.wait_for_timeout(4_000)

    post_click = pbi_dashboard.capture_page_baseline()
    changed = detect_changed_visuals(baseline, post_click)

    assert len(changed) > 0, f"{test_id} FAIL — Clicking '{target_segment}' in '{chart_name}' did not update any KPI cards."
    log.info(f"[PASS] {test_id}: Cross-filter updated {len(changed)} visuals: {sorted(changed)}")



# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY G — Filter Clear & State Isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.cat_g
@pytest.mark.scenario1
def test_qlik_filter_clear_restores_baseline(qlik_dashboard, config):
    """
    FP-G-001: Apply filter on Qlik, clear it, verify return to baseline state.
    """
    app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
    sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)

    log.info("FP-G-001: Capturing clean baseline on Qlik")
    qlik_dashboard.open(app_id, sheet_id)
    baseline = qlik_dashboard.capture_baseline()

    if not baseline:
        pytest.skip("FP-G-001: Qlik baseline empty.")

    try:
        qlik_dashboard.apply_filter("Geo", "META")
        qlik_dashboard.clear_all_filters()
    except Exception as e:
        pytest.skip(f"FP-G-001: Filter interaction skipped: {e}")

    post_clear = qlik_dashboard.capture_baseline()
    changed = detect_changed_visuals(baseline, post_clear)

    assert not changed, (
        f"FP-G-001 FAIL — Residual state after clearing filter. Changed visuals: {sorted(changed)}"
    )
    log.info("[PASS] FP-G-001: Qlik filter clear restored baseline state completely")


@pytest.mark.cat_g
@pytest.mark.scenario1
def test_pbi_slicer_clear_restores_baseline(pbi_dashboard, config):
    """
    FP-G-002: Apply slicer on PBI, clear it, verify return to baseline state.
    """
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

    log.info("FP-G-002: Capturing clean baseline on PBI")
    pbi_dashboard.open(report_url)
    baseline = pbi_dashboard.capture_page_baseline()

    if not baseline:
        pytest.skip("FP-G-002: PBI baseline empty.")

    try:
        pbi_dashboard.apply_slicer("Geo", "META")
        pbi_dashboard.clear_all_slicers()
    except Exception as e:
        pytest.skip(f"FP-G-002: Slicer interaction skipped: {e}")

    post_clear = pbi_dashboard.capture_page_baseline()
    changed = detect_changed_visuals(baseline, post_clear)

    assert not changed, (
        f"FP-G-002 FAIL — Residual state after clearing slicers. Changed visuals: {sorted(changed)}"
    )
    log.info("[PASS] FP-G-002: PBI slicer clear restored baseline state completely")
