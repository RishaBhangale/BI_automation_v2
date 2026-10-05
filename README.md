# Lenovo Qlik ↔ Power BI Validation Framework

Autonomous validation suite verifying functional parity and migration fidelity from **Qlik Cloud** to **Power BI**.

---

## 🎯 Architecture Overview (Scenario 1)

In Scenario 1, the framework verifies **Functional Parity**:
- **Behavioral Impact Propagation**: When a user applies a filter (e.g. `Geo = META` or `IDG/ISG = IDG`), the **exact same set of visuals** (KPI cards, bar charts) must react and update across both Qlik and Power BI.
- **Visual & Structural Existence**: Every sheet in Qlik must have a corresponding page in Power BI, and interactive controls (buttons, toggles, slicers) must be wired up.
- **Zero Raw Data Dependency**: Validation operates directly against the live Qlik and Power BI instances without needing backend database access.

```
┌────────────────────────────────────────────────────────┐
│                   pytest Runner                        │
│        tests/scenario1/test_functional_parity.py       │
│      (39 scenarios driven by Excel test definition)    │
└───────────────────┬────────────────────┬───────────────┘
                    │                    │
         ┌──────────▼─────────┐ ┌────────▼───────────┐
         │ QlikDashboardPage  │ │ PBIDashboardPage   │
         │ (enigma.js Engine  │ │ (FrameLocator DOM  │
         │  API + UI clicks)  │ │  slicer/card sync) │
         └──────────┬─────────┘ └────────┬───────────┘
                    │                    │
                    └──────────┬─────────┘
                               │
                ┌──────────────▼─────────────┐
                │    validation_utils.py     │
                │  compare_visual_impact()   │
                │  detect_changed_visuals()  │
                └────────────────────────────┘
```

---

## 📁 Project Structure

```
Lenovo Qlik/
├── config/
│   └── settings.py                     # Central URLs, timeouts & environment config
├── dashboard_configs/
│   ├── scenario1_track1.yaml           # Track 1 config (Qlik 6f0756c3 ↔ PBI 2321717b)
│   └── scenario2_track2.yaml           # Track 2 config (Qlik 37681c3d ↔ PBI b6da800f)
├── pageobjects/
│   ├── base_page.py                    # Shared Playwright wrapper
│   ├── qlik_dashboard_page.py          # Qlik Cloud POM (enigma.js WebSocket + UI interaction)
│   └── pbi_dashboard_page.py           # Power BI POM (iframe context + slicer/card extraction)
├── playwright/
│   └── .auth/
│       ├── qlik_session.json           # Captured Qlik browser cookies (gitignored)
│       └── pbi_session.json            # Captured PBI browser cookies (gitignored)
├── scripts/
│   ├── capture_qlik_session.py         # One-shot manual headed login for Qlik
│   ├── capture_pbi_session.py          # One-shot manual headed login for Power BI
│   └── generate_test_scenarios_excel.py# Populates functional_parity_scenarios.xlsx
├── test_data/
│   └── functional_parity_scenarios.xlsx# 39 Excel-driven functional test definitions
├── tests/
│   ├── conftest.py                     # Browser & session-shared context fixtures
│   └── scenario1/
│       └── test_functional_parity.py   # Test suite covering Categories A through G
├── utils/
│   ├── logger.py                       # Thread-safe logging to logs/framework.log
│   └── validation_utils.py             # Delta detection & impact parity comparison
├── pytest.ini                          # Pytest markers and configuration
└── requirements.txt
```

---

## 🚀 Step-by-Step Execution Guide

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### Step 2: Perform One-Time Manual Login Captures

Because Qlik Cloud and Microsoft Power BI enforce enterprise SSO / MFA, login cannot and should not be automated with brittle headless scripts. Run the capture scripts once to save authenticated session states:

```bash
# 1. Capture Qlik Cloud Session
python scripts/capture_qlik_session.py
# -> A visible browser opens. Complete login on Qlik. Press ENTER in terminal when on the hub.
# -> Saved to playwright/.auth/qlik_session.json

# 2. Capture Power BI Session
python scripts/capture_pbi_session.py
# -> Complete Microsoft SSO. Press ENTER in terminal when report is loaded.
# -> Saved to playwright/.auth/pbi_session.json
```

*Note: Sessions typically remain valid for 8–24 hours. Re-run these scripts if tests report session redirects.*

---

### Step 3: Run Functional Parity Tests

```bash
# Run all Scenario 1 functional parity tests:
pytest tests/scenario1/test_functional_parity.py --config=dashboard_configs/scenario1_track1.yaml -v

# Run Category A tests only (Sheet existence & count parity):
pytest tests/scenario1/test_functional_parity.py -m cat_a -v

# Run Category D tests only (Filter Impact Propagation - core functionality):
pytest tests/scenario1/test_functional_parity.py -m cat_d -v
```

---

## 🧪 Test Categories Defined

| Category | Count | Scope |
|---|:---:|---|
| **A: Sheet / Page Existence** | 4 | Verifies all Qlik sheets exist as pages in PBI report |
| **B: Visual Existence** | 8 | Verifies visuals (KPI cards, bar charts) exist on migrated pages |
| **C: Filter / Slicer Existence** | 5 | Verifies filter dimensions (Geo, IDG/ISG, Market) exist as slicers |
| **D: Filter Impact Propagation** | 9 | Core test: applies filter on both; verifies same visuals update |
| **E: Navigation & Actions** | 6 | Interactive buttons ('Go to ISG', Revenue/Quantity toggles) |
| **F: Cross-Filtering** | 5 | Clicking a bar chart slice filters related cards/visuals |
| **G: Teardown & Reset** | 2 | Clearing filters restores original baseline without state leakage |
