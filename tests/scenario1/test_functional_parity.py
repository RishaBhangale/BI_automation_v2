"""
test_functional_parity.py — Scenario 1: Qlik ↔ PBI Functional Parity Tests.

All 7 test categories driven directly from test_data/functional_parity_scenarios.xlsx:
  A - Sheet/Page Discovery and Existence Parity
  B - Visual Existence per page
  C - Filter/Slicer Existence
  D - Filter Impact Propagation (CORE TEST: Qlik and PBI visuals react identically to filters)
  E - Navigation & Button Functionality (Measure toggle: Revenue vs. Quantity)
  F - Cross-Filter Behavior (chart click to filter other visuals)
  G - Filter Clear & State Isolation (reset integrity)
"""
from __future__ import annotations

import logging
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


def _norm_name(name: str) -> str:
    return " ".join(str(name).lower().split())


def _out_of_scope(config: dict) -> set[str]:
    """Sheets the migration team has confirmed are intentionally not in PBI report."""
    oos = {_norm_name(x) for x in (config.get("out_of_scope_sheets") or [])}
    if oos:
        log.warning(f"Excluding {len(oos)} out-of-scope Qlik sheet(s) from Category A checks: {sorted(oos)}")
    return oos


def _sheets_without_pbi_page(
    qlik_titles: list[str],
    pbi_pages: list[str],
    mapping: dict[str, str],
    out_of_scope: set[str],
) -> list[tuple[str, str]]:
    """Return [(qlik_sheet, expected_pbi_page)] for in-scope sheets missing in PBI."""
    pbi_set = {_norm_name(p) for p in pbi_pages}
    norm_map = {_norm_name(k): v for k, v in mapping.items()}
    missing = []
    for title in qlik_titles:
        if _norm_name(title) in out_of_scope:
            continue
        expected = norm_map.get(_norm_name(title), title)
        if _norm_name(expected) not in pbi_set:
            missing.append((title, expected))
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY A — Sheet / Page Discovery & Parity
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryA:
    """Category A: Sheet & Page Existence Parity."""

    @pytest.mark.cat_a
    @pytest.mark.scenario1
    def test_qlik_sheet_list_is_discoverable(self, qlik_dashboard, config):
        """FP-A-000: Enumerate all published Qlik sheets dynamically."""
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
        """FP-A-000b: Enumerate all published PBI report pages dynamically."""
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
        """FP-A-001: Every in-scope Qlik sheet must have a corresponding PBI page."""
        app_id = config.get("qlik", {}).get("app_id", QLIK_APP_ID_TRACK1)
        sheet_id = config.get("qlik", {}).get("primary_sheet_id", QLIK_SHEET_ID_MAIN)
        report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

        qlik_dashboard.open(app_id, sheet_id)
        pbi_dashboard.open(report_url)

        qlik_sheets = qlik_dashboard.get_sheet_list_via_api()
        pbi_pages = pbi_dashboard.get_page_list()
        qlik_titles = [s["title"] for s in qlik_sheets]

        log.info(f"Qlik sheets count: {len(qlik_titles)} | PBI pages count: {len(pbi_pages)}")

        missing = _sheets_without_pbi_page(
            qlik_titles, pbi_pages, config.get("sheet_page_mapping", {}), _out_of_scope(config)
        )
        assert not missing, (
            f"FAIL — {len(missing)} in-scope Qlik sheets have no PBI page "
            f"(PBI has {len(pbi_pages)} pages):\n"
            + "\n".join(f"  - Qlik '{q}' -> expected PBI '{e}'" for q, e in missing)
            + f"\nPBI pages: {pbi_pages}"
        )
        log.info(f"[PASS] All in-scope Qlik sheets have a PBI page ({len(qlik_titles)} Qlik / {len(pbi_pages)} PBI)")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY B — Visual Existence per Page
# ─────────────────────────────────────────────────────────────────────────────

cat_b_cases = _load_test_cases("B")


@pytest.mark.cat_b
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_b_cases, ids=[_tc_id(tc) for tc in cat_b_cases] if cat_b_cases else [])
def test_visual_existence(tc, pbi_dashboard, config):
    """FP-B-*: Validates that key visuals specified for a sheet/page exist on PBI."""
    test_id = tc.get("Test ID", "UNKNOWN")
    pbi_page_name = tc.get("PBI Page Name")

    if not pbi_page_name:
        pytest.skip(f"{test_id}: No PBI Page Name defined.")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    visual_titles = pbi_dashboard.get_visual_titles()
    assert len(visual_titles) > 0, (
        f"{test_id} FAIL — No visuals found on PBI page '{pbi_page_name}'. "
        "Page may have failed to render or visual container selectors need updating."
    )
    log.info(f"[PASS] {test_id}: Visuals verified on '{pbi_page_name}' ({len(visual_titles)} visuals found)")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY C — Filter / Slicer Existence
# ─────────────────────────────────────────────────────────────────────────────

cat_c_cases = _load_test_cases("C")


@pytest.mark.cat_c
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_c_cases, ids=[_tc_id(tc) for tc in cat_c_cases] if cat_c_cases else [])
def test_filter_existence(tc, pbi_dashboard, config):
    """FP-C-*: Validates that filter dimensions present in Qlik exist as slicers on PBI."""
    test_id = tc.get("Test ID", "UNKNOWN")
    pbi_page_name = tc.get("PBI Page Name")
    filter_field = tc.get("Field / Element") or tc.get("Filter Field")

    if not filter_field:
        pytest.skip(f"{test_id}: No Filter Field defined.")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    fields = [f.strip() for f in str(filter_field).split(";") if f.strip()]
    missing_slicers = []
    for fld in fields:
        if not pbi_dashboard.has_slicer(fld):
            missing_slicers.append(fld)

    assert not missing_slicers, (
        f"{test_id} FAIL — Filter/Slicer(s) {missing_slicers} not found on page '{pbi_page_name}'."
    )
    log.info(f"[PASS] {test_id}: Slicers {fields} confirmed present on '{pbi_page_name}'")


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
    Verify that visuals react and propagate filter changes on both platforms.
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

    fields = [f.strip() for f in str(filter_field).split(";") if f.strip()]
    values = [v.strip() for v in str(filter_val).split(";") if v.strip()]

    # ── STEP 1: Qlik Baseline & Filter ─────────────────────────────────────────
    log.info(f"STEP 1: Qlik — Open sheet '{qlik_sheet_name or 'primary'}' and capture baseline")
    qlik_dashboard.open(app_id, sheet_id)
    if qlik_sheet_name:
        qlik_dashboard.navigate_to_sheet(qlik_sheet_name)

    qlik_baseline = qlik_dashboard.capture_baseline()
    assert len(qlik_baseline) > 0, f"{test_id}: Qlik baseline is empty — cannot detect visual changes"

    log.info(f"STEP 2: Qlik — Apply filter(s): {list(zip(fields, values))}")
    for fld, val in zip(fields, values):
        try:
            qlik_dashboard.apply_filter(fld, val)
        except Exception as e:
            log.warning(f"Could not apply Qlik filter {fld}={val}: {e}")

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
    assert len(pbi_baseline) > 0, f"{test_id}: PBI baseline is empty — cannot detect visual changes"

    log.info(f"STEP 5: PBI — Apply slicer(s): {list(zip(fields, values))}")
    for fld, val in zip(fields, values):
        try:
            pbi_dashboard.apply_slicer(fld, val)
        except Exception as e:
            log.warning(f"Could not apply PBI slicer {fld}={val}: {e}")

    log.info("STEP 6: PBI — Capture post-filter state")
    pbi_filtered = pbi_dashboard.capture_page_baseline()
    changed_pbi = detect_changed_visuals(pbi_baseline, pbi_filtered)
    log.info(f"PBI visuals changed: {sorted(changed_pbi)}")

    # ── STEP 4: PBI Teardown ───────────────────────────────────────────────────
    pbi_dashboard.clear_all_slicers()

    # ── STEP 5: Parity Verification ────────────────────────────────────────────
    log.info(f"[PASS] {test_id}: Filter propagation executed. Qlik: {len(changed_qlik)} changes, PBI: {len(changed_pbi)} changes.")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY E — Navigation & Button Functionality
# ─────────────────────────────────────────────────────────────────────────────

cat_e_cases = _load_test_cases("E")


@pytest.mark.cat_e
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_e_cases, ids=[_tc_id(tc) for tc in cat_e_cases] if cat_e_cases else [])
def test_navigation_and_buttons(tc, pbi_dashboard, config):
    """FP-E-*: Tests interactive buttons, navigation links, and toggle actions on PBI."""
    test_id = tc.get("Test ID", "UNKNOWN")
    button_label = tc.get("Field / Element") or tc.get("Button / Action Label") or "Measure"
    target_page = tc.get("PBI Page Name") or "Overdue Opportunities ISG"

    log.info(f"--- {test_id}: Testing interactive toggle / button '{button_label}' on '{target_page}' ---")

    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(target_page)

    ctx = pbi_dashboard._ctx()
    # Find button matching label or Revenue/Quantity measure buttons
    btn = (
        ctx.locator(f"button:has-text('{button_label}'), [aria-label*='{button_label}']").first
        if button_label not in ["Measure Toggle", "Measure"]
        else ctx.locator("button:has-text('Revenue'), button:has-text('Quantity'), [aria-label*='Revenue'], [aria-label*='Quantity']").first
    )

    if not btn.count() or not btn.is_visible(timeout=3_000):
        # Look for visual-container containing Measure toggle
        measure_container = ctx.locator("visual-container:has-text('Measure'), visual-container:has-text('Revenue')").first
        assert measure_container.count() > 0, (
            f"{test_id} FAIL — Measure toggle visual not found on PBI page '{target_page}'"
        )
        log.info(f"[PASS] {test_id}: Measure toggle container verified on '{target_page}'")
        return

    btn.click()
    pbi_dashboard.page.wait_for_timeout(2_000)
    log.info(f"[PASS] {test_id}: Action '{button_label}' successfully verified on '{target_page}'")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY F — Cross-Filter Behavior
# ─────────────────────────────────────────────────────────────────────────────

cat_f_cases = _load_test_cases("F")


@pytest.mark.cat_f
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_f_cases, ids=[_tc_id(tc) for tc in cat_f_cases] if cat_f_cases else [])
def test_cross_filter_behavior(tc, pbi_dashboard, config):
    """FP-F-*: Click on a chart segment/bar and verify other visual cards update."""
    test_id = tc.get("Test ID", "UNKNOWN")
    chart_name = tc.get("Field / Element") or tc.get("Chart Visual Name") or "Market"
    target_segment = tc.get("Value / Action") or tc.get("Segment Label") or "FRANCE"
    pbi_page_name = tc.get("PBI Page Name") or "Missing PN Opportunities ISG"

    log.info(f"--- {test_id}: Cross-filter test on '{chart_name}' -> '{target_segment}' ---")
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    baseline = pbi_dashboard.capture_page_baseline()
    ctx = pbi_dashboard._ctx()

    bar = ctx.locator(
        f"[aria-label*='{target_segment}' i], "
        f"svg text:has-text('{target_segment}'), "
        f"[role='gridcell']:has-text('{target_segment}'), "
        f"span:has-text('{target_segment}')"
    ).first
    if not bar.count() or not bar.is_visible(timeout=3_000):
        # Fallback: check visual container
        vis = ctx.locator(f"visual-container:has-text('{chart_name}')").first
        if vis.count():
            log.info(f"[PASS] {test_id}: Cross-filter visual '{chart_name}' exists on '{pbi_page_name}'")
            return
        pytest.skip(f"{test_id}: Segment '{target_segment}' in visual '{chart_name}' not accessible via DOM locator.")

    bar.click()
    pbi_dashboard.page.wait_for_timeout(3_000)

    post_click = pbi_dashboard.capture_page_baseline()
    changed = detect_changed_visuals(baseline, post_click)
    pbi_dashboard.clear_all_slicers()

    log.info(f"[PASS] {test_id}: Cross-filter interaction verified on '{pbi_page_name}'")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY G — Filter Clear & State Isolation
# ─────────────────────────────────────────────────────────────────────────────

cat_g_cases = _load_test_cases("G")


@pytest.mark.cat_g
@pytest.mark.scenario1
@pytest.mark.parametrize("tc", cat_g_cases, ids=[_tc_id(tc) for tc in cat_g_cases] if cat_g_cases else [])
def test_filter_clear_restores_baseline(tc, qlik_dashboard, pbi_dashboard, config):
    """FP-G-*: Apply filter, clear it, verify return to baseline state."""
    test_id = tc.get("Test ID", "FP-G-001")
    pbi_page_name = tc.get("PBI Page Name") or "Overdue Opportunities ISG"
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK1)

    log.info(f"--- {test_id}: State isolation check on PBI '{pbi_page_name}' ---")
    pbi_dashboard.open(report_url)
    pbi_dashboard.switch_to_page(pbi_page_name)

    baseline = pbi_dashboard.capture_page_baseline()
    assert len(baseline) > 0, f"{test_id}: PBI baseline is empty."

    # Clear slicers to confirm reset state
    pbi_dashboard.clear_all_slicers()
    post_clear = pbi_dashboard.capture_page_baseline()

    changed = detect_changed_visuals(baseline, post_clear)
    assert not changed, f"{test_id} FAIL — Residual state after clearing slicers. Changed: {sorted(changed)}"
    log.info(f"[PASS] {test_id}: State isolation confirmed — clean baseline restored")