"""
test_number_matching.py — Scenario 2: Number Matching (PBI Visuals vs. Source CSV Truth).

Executes data validation tests defined in test_data/number_matching_scenarios.xlsx:
  1. Switches to target Power BI report page using the warm persistent browser session
  2. Applies slicer filters defined in the test case
  3. Computes ground-truth aggregated number from CSV source datasets via CSVEngine
  4. Extracts displayed value from target Power BI visual (cards, tables, charts)
  5. Compares extracted value against source truth within tolerance
  6. Restores slicers to baseline state
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from utils.csv_engine import get_csv_engine
from utils.validation_utils import compare_single_value, parse_number
from config.settings import PBI_REPORT_URL_TRACK2
from utils.logger import get_logger

log = get_logger("test_number_matching")

EXCEL_PATH = Path(__file__).parent.parent.parent / "test_data" / "number_matching_scenarios.xlsx"


def _load_number_matching_cases() -> list[dict[str, Any]]:
    """Load test cases from the number matching scenario Excel definition."""
    if not EXCEL_PATH.exists():
        log.warning(f"Scenario Excel file not found: {EXCEL_PATH}")
        return []
    try:
        df = pd.read_excel(EXCEL_PATH)
        return df.where(pd.notnull(df), None).to_dict("records")
    except Exception as e:
        log.error(f"Error reading {EXCEL_PATH}: {e}")
        return []


def _tc_id(tc: dict) -> str:
    tid = tc.get("Test ID", "UNKNOWN")
    name = tc.get("Scenario Name") or "Test"
    return f"{tid} - {name}"


cases = _load_number_matching_cases()


@pytest.fixture(scope="module")
def csv_engine(config):
    """Module-scoped CSVEngine pre-loaded with dataset."""
    return get_csv_engine(config=config)


@pytest.mark.scenario2
@pytest.mark.parametrize("tc", cases, ids=[_tc_id(tc) for tc in cases] if cases else [])
def test_pbi_visual_matches_csv_source(tc, pbi_dashboard, csv_engine, config):
    """
    Assert that the Power BI visual value matches the aggregated CSV ground truth.
    """
    test_id = tc.get("Test ID", "UNKNOWN")
    scenario_name = tc.get("Scenario Name", "Unnamed")
    pbi_page_name = tc.get("PBI Page Name")
    visual_target = tc.get("Visual Target")
    measure_name = tc.get("Measure Name") or "Revenue USD @ Actual Rate"
    cal_filter = tc.get("Calendar Filter")
    geo_filter = tc.get("GEO Filter")
    add_filters = tc.get("Additional Filters")
    tolerance = float(tc.get("Tolerance", 0.01) or 0.01)

    log.info(f"--- Starting {test_id}: {scenario_name} ---")

    # 1. Build query filter dictionary for CSVEngine first
    csv_filters: Dict[str, Any] = {}
    if measure_name:
        csv_filters["Measure"] = measure_name

    # Parse Additional Filters
    past_due_flag = None
    seven_days_flag = None
    missing_pn_flag = None

    if add_filters and str(add_filters).lower() != "nan":
        add_str = str(add_filters)
        if "Past Due=-1" in add_str or "Past Due" in add_str:
            past_due_flag = True
            csv_filters["Past Due"] = -1
        if "Days To Close<=7" in add_str or "7 Days" in add_str or "7 Day" in add_str:
            seven_days_flag = True
            csv_filters["Days To Close"] = "<= 7"
        if "Missing PN" in add_str:
            missing_pn_flag = True

        # Extract explicit key-value pairs (e.g. Business Group=IDG; Open Pipe Flag=Past Open)
        for part in add_str.split(";"):
            part = part.strip()
            if "=" in part:
                if any(op in part for op in ("<=", ">=", "!=", "<", ">")):
                    continue
                k, v = part.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean not in ("Past Due", "Days To Close", "Missing PN"):
                    if v_clean.lower() in ("*", "all", "none", "any"):
                        csv_filters[k_clean] = None
                    else:
                        csv_filters[k_clean] = v_clean

    # Track 2 Pipeline Hygiene is IDG-scoped: if 'isg' not in (pbi_page_name or '').lower(), set Business Group = 'IDG'
    # Respect explicit Business Group if already specified in Additional Filters
    if "Business Group" not in csv_filters:
        if "isg" not in (pbi_page_name or "").lower():
            csv_filters["Business Group"] = "IDG"

    # Set csv_filters['Open Pipe Flag']:
    # - 'Past Open' when past_due_flag is True or 'overdue' in (pbi_page_name or '').lower()
    # - 'To Go' when '7 days' in (pbi_page_name or '').lower() or 'missing pn' in (pbi_page_name or '').lower()
    # Respect explicit Open Pipe Flag if already specified in Additional Filters
    if "Open Pipe Flag" not in csv_filters:
        if past_due_flag is True or "overdue" in (pbi_page_name or "").lower():
            csv_filters["Open Pipe Flag"] = "Past Open"
        elif "7 days" in (pbi_page_name or "").lower() or "missing pn" in (pbi_page_name or "").lower():
            csv_filters["Open Pipe Flag"] = "To Go"

    # Parse Calendar Filter
    if cal_filter and "=" in str(cal_filter):
        k, v = str(cal_filter).split("=", 1)
        csv_filters[k.strip()] = v.strip()

    # Parse GEO Filter
    geo_category_values = None
    if geo_filter and "=" in str(geo_filter):
        k, v = str(geo_filter).split("=", 1)
        regions = [r.strip() for r in v.split(",") if r.strip()]
        csv_filters[k.strip()] = regions
        geo_category_values = regions

    # 2. Compute expected scalar ground truth from CSV datasets immediately
    clean_csv_filters = {k: v for k, v in csv_filters.items() if v is not None}
    log.info(f"Computing CSV ground truth with filters: {clean_csv_filters}")
    expected_value = csv_engine.get_aggregated_sum(
        measure=measure_name,
        filters=clean_csv_filters,
        past_due=past_due_flag,
        seven_days_only=None if "Days To Close" in clean_csv_filters else seven_days_flag,
        missing_pn_only=missing_pn_flag,
    )
    log.info(f"CSV Ground Truth Expected: {expected_value:,.2f}")

    # 3. Navigate to target report URL & page
    report_url = config.get("pbi", {}).get("report_url", PBI_REPORT_URL_TRACK2)
    pbi_dashboard.open(report_url)

    if pbi_page_name:
        log.info(f"Switching to page: {pbi_page_name}")
        pbi_dashboard.switch_to_page(pbi_page_name)

    # 4. Reset any previous slicers to start from clean state FIRST
    pbi_dashboard.clear_all_slicers()

    # 5. Set metric toggle (Quantity vs Revenue)
    if measure_name:
        pbi_dashboard.set_metric_toggle(measure_name)

    # 6. Apply slicers on PBI defensively
    if cal_filter and "=" in str(cal_filter):
        k, v = str(cal_filter).split("=", 1)
        log.info(f"Applying slicer: {k.strip()} = {v.strip()}")
        try:
            pbi_dashboard.apply_slicer(k.strip(), v.strip(), target_visual=visual_target)
        except Exception as e:
            log.warning(f"Could not apply PBI slicer {k.strip()} = {v.strip()}: {e}")

    if geo_filter and "=" in str(geo_filter):
        k, v = str(geo_filter).split("=", 1)
        log.info(f"Applying slicer: {k.strip()} = {v.strip()}")
        try:
            pbi_dashboard.apply_slicer(k.strip(), v.strip(), target_visual=visual_target)
        except Exception as e:
            log.warning(f"Could not apply PBI slicer {k.strip()} = {v.strip()}: {e}")

    # 7. Extract displayed value from Power BI
    log.info(f"Extracting visual value for target: '{visual_target}', categories: {geo_category_values}")
    pbi_raw = pbi_dashboard.extract_visual_value(
        visual_target=visual_target or measure_name,
        category_filter=geo_category_values,
    )
    if not pbi_raw and measure_name:
        pbi_raw = pbi_dashboard.extract_visual_value(measure_name, category_filter=geo_category_values)
    if not pbi_raw:
        baseline = pbi_dashboard.capture_page_baseline()
        for b_title, b_val in baseline.items():
            if b_val and parse_number(b_val) is not None:
                log.info(f"Fallback extracted value from card '{b_title}': '{b_val}'")
                pbi_raw = b_val
                break

    log.info(f"PBI Extracted Raw Value: {pbi_raw!r}")

    # 8. Teardown: clear slicers
    pbi_dashboard.clear_all_slicers()

    # 8. Compare and assert
    assert pbi_raw is not None, (
        f"{test_id} FAIL — Could not extract visual value for target '{visual_target}' "
        f"on page '{pbi_page_name}'"
    )

    passed, detail = compare_single_value(
        pbi_raw=pbi_raw,
        source_value=expected_value,
        tolerance=tolerance,
        label=f"{test_id} - {scenario_name}",
    )

    status = "PASS" if passed else "FAIL"
    log.info(f"[{status}] {detail}")

    assert passed, f"{test_id} FAILED: {detail}"
