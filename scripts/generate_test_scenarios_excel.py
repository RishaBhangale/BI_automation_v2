"""
generate_test_scenarios_excel.py — Generates test_data/functional_parity_scenarios.xlsx

Streamlined 8-column schema:
  1. Test ID           - Unique identifier (e.g. FP-D-001)
  2. Category          - Test category (A through G)
  3. Scenario Name     - Clear readable description
  4. Qlik Sheet Name   - Plain-English sheet title (e.g. 'Pipeline Hygiene IDG')
  5. PBI Page Name     - Corresponding Power BI page name
  6. Field / Element   - Filter dimension, button label, or chart visual name
  7. Value / Action    - Filter value to select, or click segment
  8. Notes             - Business context / verification criteria
"""
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
TARGET_FILE = PROJECT_ROOT / "test_data" / "functional_parity_scenarios.xlsx"

scenarios = [
    # ── Category A: Sheet / Page Existence ──────────────────────────────────────────
    {
        "Test ID": "FP-A-001",
        "Category": "A",
        "Scenario Name": "Sheet Existence: Pipeline Hygiene IDG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene IDG",
        "Field / Element": "Sheet Tab",
        "Value / Action": "Exists",
        "Notes": "Verify sheet exists as PBI page"
    },
    {
        "Test ID": "FP-A-002",
        "Category": "A",
        "Scenario Name": "Sheet Existence: Pipeline Hygiene ISG",
        "Qlik Sheet Name": "Pipeline Hygiene ISG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Sheet Tab",
        "Value / Action": "Exists",
        "Notes": "Verify ISG sheet exists as PBI page"
    },
    {
        "Test ID": "FP-A-003",
        "Category": "A",
        "Scenario Name": "Sheet Existence: Overdue Opportunities",
        "Qlik Sheet Name": "Overdue Opportunities",
        "PBI Page Name": "Overdue Opportunities ISG",
        "Field / Element": "Sheet Tab",
        "Value / Action": "Exists",
        "Notes": "Verify Overdue Opps exists as PBI page"
    },
    {
        "Test ID": "FP-A-004",
        "Category": "A",
        "Scenario Name": "Sheet Count Parity",
        "Qlik Sheet Name": "ALL",
        "PBI Page Name": "ALL",
        "Field / Element": "Sheet Catalog",
        "Value / Action": "Count >= 21",
        "Notes": "PBI page count >= Qlik sheet count"
    },

    # ── Category B: Visual Existence on Page ───────────────────────────────────────
    {
        "Test ID": "FP-B-001",
        "Category": "B",
        "Scenario Name": "Visual Existence: Pipeline Hygiene IDG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Cards, bar charts and slicers present"
    },
    {
        "Test ID": "FP-B-002",
        "Category": "B",
        "Scenario Name": "Visual Existence: Overdue Opportunities",
        "Qlik Sheet Name": "Overdue Opportunities",
        "PBI Page Name": "Overdue Opportunities ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Table and cards present"
    },
    {
        "Test ID": "FP-B-003",
        "Category": "B",
        "Scenario Name": "Visual Existence: 7 Days Opportunities",
        "Qlik Sheet Name": "7 Days Opportunities",
        "PBI Page Name": "7 Days Opportunities ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Table and cards present"
    },
    {
        "Test ID": "FP-B-004",
        "Category": "B",
        "Scenario Name": "Visual Existence: Missing PN Opportunities",
        "Qlik Sheet Name": "Missing PN Opportunities",
        "PBI Page Name": "Missing PN Opportunities ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Verify visuals present"
    },
    {
        "Test ID": "FP-B-005",
        "Category": "B",
        "Scenario Name": "Visual Existence: Overloaded Contracts",
        "Qlik Sheet Name": "Overloaded Contracts",
        "PBI Page Name": "Overloaded Contracts ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Verify visuals present"
    },
    {
        "Test ID": "FP-B-006",
        "Category": "B",
        "Scenario Name": "Visual Existence: Next QTR Unweighted",
        "Qlik Sheet Name": "Next QTR Opportunities Unweighted",
        "PBI Page Name": "Next QTR Opp Unweighted ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Verify visuals present"
    },
    {
        "Test ID": "FP-B-007",
        "Category": "B",
        "Scenario Name": "Visual Existence: Next QTR Weighted",
        "Qlik Sheet Name": "Next QTR Opportunities Weighted",
        "PBI Page Name": "Next QTR Opp Weighted ISG",
        "Field / Element": "Visuals",
        "Value / Action": "ALL",
        "Notes": "Verify visuals present"
    },
    {
        "Test ID": "FP-B-008",
        "Category": "B",
        "Scenario Name": "KPI Card Count Parity per Page",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "KPI Cards",
        "Value / Action": "Count Parity",
        "Notes": "PBI card count >= Qlik card count per sheet"
    },

    # ── Category C: Filter / Slicer Existence ─────────────────────────────────────
    {
        "Test ID": "FP-C-001",
        "Category": "C",
        "Scenario Name": "Filter Existence: Geo",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo",
        "Value / Action": "Exists",
        "Notes": "Slicer 'Geo' exists on PBI page"
    },
    {
        "Test ID": "FP-C-002",
        "Category": "C",
        "Scenario Name": "Filter Existence: IDG/ISG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "IDG/ISG",
        "Value / Action": "Exists",
        "Notes": "Slicer 'IDG/ISG' exists on PBI page"
    },
    {
        "Test ID": "FP-C-003",
        "Category": "C",
        "Scenario Name": "Filter Existence: Fiscal Quarter",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Fiscal Quarter",
        "Value / Action": "Exists",
        "Notes": "Date / Fiscal period filter exists"
    },
    {
        "Test ID": "FP-C-004",
        "Category": "C",
        "Scenario Name": "Filter Existence: Country Name",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Country Name",
        "Value / Action": "Exists",
        "Notes": "Country filter exists on PBI page"
    },
    {
        "Test ID": "FP-C-005",
        "Category": "C",
        "Scenario Name": "Filter Existence: Business Group",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Business Group",
        "Value / Action": "Exists",
        "Notes": "Business Group filter exists on PBI page"
    },

    # ── Category D: Filter Impact Propagation (CORE FUNCTIONALITY) ───────────────
    {
        "Test ID": "FP-D-001",
        "Category": "D",
        "Scenario Name": "Geo Filter Impact: META",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo",
        "Value / Action": "META",
        "Notes": "All cards & bar charts change from baseline on both platforms"
    },
    {
        "Test ID": "FP-D-002",
        "Category": "D",
        "Scenario Name": "Geo Filter Impact: EUROPE",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo",
        "Value / Action": "EUROPE",
        "Notes": "Europe filter propagation"
    },
    {
        "Test ID": "FP-D-003",
        "Category": "D",
        "Scenario Name": "IDG/ISG Filter Impact: IDG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "IDG/ISG",
        "Value / Action": "IDG",
        "Notes": "IDG slice impact propagation"
    },
    {
        "Test ID": "FP-D-004",
        "Category": "D",
        "Scenario Name": "IDG/ISG Filter Impact: ISG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "IDG/ISG",
        "Value / Action": "ISG",
        "Notes": "ISG slice impact propagation"
    },
    {
        "Test ID": "FP-D-005",
        "Category": "D",
        "Scenario Name": "Business Group Filter Impact: SSG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Business Group",
        "Value / Action": "SSG",
        "Notes": "Business Group propagation"
    },
    {
        "Test ID": "FP-D-006",
        "Category": "D",
        "Scenario Name": "Fiscal Quarter Filter Impact: Q227",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Fiscal Quarter",
        "Value / Action": "Q227",
        "Notes": "Fiscal quarter filtering"
    },
    {
        "Test ID": "FP-D-007",
        "Category": "D",
        "Scenario Name": "Compound Filter: Geo + IDG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo; IDG/ISG",
        "Value / Action": "META; IDG",
        "Notes": "Dual filter compound impact"
    },
    {
        "Test ID": "FP-D-008",
        "Category": "D",
        "Scenario Name": "Compound Filter: Geo + Business Group",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo; Business Group",
        "Value / Action": "EUROPE; ISG",
        "Notes": "Compound multi-filter impact"
    },
    {
        "Test ID": "FP-D-009",
        "Category": "D",
        "Scenario Name": "Compound Filter: Three Dimensions",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo; IDG/ISG; Business Group",
        "Value / Action": "META; IDG; SSG",
        "Notes": "Triple filter compound impact"
    },

    # ── Category E: Navigation & Button Functionality ──────────────────────────────
    {
        "Test ID": "FP-E-001",
        "Category": "E",
        "Scenario Name": "Action Button: Go to ISG",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Go to ISG",
        "Value / Action": "Click",
        "Notes": "Navigates from summary to ISG page"
    },
    {
        "Test ID": "FP-E-002",
        "Category": "E",
        "Scenario Name": "Toggle: IDG Selection",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "IDG",
        "Value / Action": "Toggle",
        "Notes": "Switches visual measures to IDG"
    },
    {
        "Test ID": "FP-E-003",
        "Category": "E",
        "Scenario Name": "Toggle: Round Trip State Restoration",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "IDG",
        "Value / Action": "Toggle Roundtrip",
        "Notes": "Values match original state after toggle on/off"
    },
    {
        "Test ID": "FP-E-004",
        "Category": "E",
        "Scenario Name": "Tab Navigation Accessibility",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "ALL",
        "Field / Element": "Page Tabs",
        "Value / Action": "Click Each",
        "Notes": "All tabs load without 404 or blank canvas"
    },

    # ── Category F: Cross-Filter Behavior ──────────────────────────────────────────
    {
        "Test ID": "FP-F-001",
        "Category": "F",
        "Scenario Name": "Bar Chart Segment Click -> KPI Card Update: ISRAEL",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Unweighted Revenue by Country",
        "Value / Action": "ISRAEL",
        "Notes": "Associative selection updates cards"
    },
    {
        "Test ID": "FP-F-002",
        "Category": "F",
        "Scenario Name": "Bar Chart Segment Click: NORDICS",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Unweighted Revenue by Country",
        "Value / Action": "NORDICS",
        "Notes": "Cross-chart filtering on Nordics segment"
    },
    {
        "Test ID": "FP-F-003",
        "Category": "F",
        "Scenario Name": "Bar Chart Segment Click: UKI",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Unweighted Revenue of 7 Day Opps",
        "Value / Action": "UKI",
        "Notes": "Cross-filtering from 7 Day Opps chart"
    },
    {
        "Test ID": "FP-F-004",
        "Category": "F",
        "Scenario Name": "Segment Deselection Restores State",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Unweighted Revenue by Country",
        "Value / Action": "Deselect",
        "Notes": "Un-selecting restores baseline"
    },

    # ── Category G: Filter Clear & State Isolation ─────────────────────────────────
    {
        "Test ID": "FP-G-001",
        "Category": "G",
        "Scenario Name": "Single Filter Reset Restores Baseline",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Geo",
        "Value / Action": "Clear",
        "Notes": "Filter clear restores initial values"
    },
    {
        "Test ID": "FP-G-002",
        "Category": "G",
        "Scenario Name": "Multi-Filter Clear All Restores Baseline",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Toolbar Clear All",
        "Value / Action": "Clear All",
        "Notes": "Full clear button resets completely"
    },
    {
        "Test ID": "FP-G-003",
        "Category": "G",
        "Scenario Name": "Test Case Teardown Isolation",
        "Qlik Sheet Name": "Pipeline Hygiene IDG",
        "PBI Page Name": "Pipeline Hygiene ISG",
        "Field / Element": "Sequential Run",
        "Value / Action": "Verify Isolation",
        "Notes": "No filter bleed between test cases"
    },
]


def main():
    TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(scenarios)
    df.to_excel(TARGET_FILE, index=False)
    print(f"Successfully generated {len(df)} test scenarios (8-column schema) at:\n  {TARGET_FILE}")


if __name__ == "__main__":
    main()
