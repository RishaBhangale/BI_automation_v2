"""
settings.py — Global configuration for the Lenovo Qlik/PBI Validation Framework.

All credentials come from environment variables (set in .env — never hardcoded).

Track 1 = Scenario 1 (Functional Parity): Qlik 6f0756c3 <-> PBI 2321717b
Track 2 = Scenario 2 (Number Matching):   Qlik 37681c3d <-> PBI b6da800f
"""
from __future__ import annotations
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

# Project root
PROJECT_ROOT = Path(__file__).parent.parent

# Timeouts (milliseconds)
PBI_RENDER_TIMEOUT   = 90_000
PBI_PAGE_SWITCH_WAIT = 5_000
PBI_IFRAME_PROBE_TIMEOUT = 8_000    # how long to look for a report <iframe> before assuming none exists
PBI_PAGELIST_TIMEOUT     = 30_000   # how long to keep polling for page tabs to render
QLIK_RENDER_TIMEOUT  = 60_000
QLIK_FILTER_WAIT     = 4_000
DEFAULT_TIMEOUT      = 30_000
NAVIGATION_TIMEOUT   = 90_000
ELEMENT_TIMEOUT      = 15_000

# Browser
BROWSER_CHANNEL = "chrome"
HEADLESS        = False
BROWSER_WIDTH   = 1600
BROWSER_HEIGHT  = 900
SLOW_MO         = 0

# Output paths
SCREENSHOT_DIR = str(PROJECT_ROOT / "screenshots")
LOG_DIR        = str(PROJECT_ROOT / "logs")
REPORT_DIR     = str(PROJECT_ROOT / "reports")
AUTH_DIR       = str(PROJECT_ROOT / "playwright" / ".auth")

# Persistent Browser Profile (stores SSO sessions, IndexedDB, cookies across runs)
BROWSER_PROFILE_DIR = str(PROJECT_ROOT / "playwright" / ".auth" / "browser_profile")

# Legacy session snapshot files (fallback / seed)
QLIK_SESSION_FILE = str(PROJECT_ROOT / "playwright" / ".auth" / "qlik_session.json")
PBI_SESSION_FILE  = str(PROJECT_ROOT / "playwright" / ".auth" / "pbi_session.json")

# Qlik Cloud
QLIK_TENANT   = "5wnjr4er56khtqe.in.qlikcloud.com"
QLIK_BASE_URL = f"https://{QLIK_TENANT}"

# Track 1 - Scenario 1
QLIK_APP_ID_TRACK1  = "6f0756c3-f251-45d9-b569-868b8191798e"
QLIK_SHEET_ID_MAIN  = "ec6922ca-7f1b-4766-9066-38074d8849ca"

# Track 2 - Scenario 2
QLIK_APP_ID_TRACK2  = "37681c3d-4b65-46a0-9a69-d60544c8c1c2"

# enigma.js (injected into Qlik page for Engine API access)
ENIGMA_JS_URL     = "https://unpkg.com/enigma.js@2.14.0/enigma.min.js"
ENIGMA_SCHEMA_URL = "https://unpkg.com/enigma.js@2.14.0/schemas/12.170.2.json"

# Power BI
PBI_BASE_URL  = "https://app.powerbi.com"
PBI_TENANT_ID = "b5af2451-e21b-4aa2-b4b5-dc5907908dd8"

# Track 1 - Scenario 1
PBI_REPORT_ID_TRACK1  = "2321717b-8815-48c1-8065-0b8747ca83d7"
PBI_REPORT_URL_TRACK1 = (
    "https://app.powerbi.com/groups/me/reports/2321717b-8815-48c1-8065-0b8747ca83d7"
    "/1523bb40960e2dbc7823?ctid=b5af2451-e21b-4aa2-b4b5-dc5907908dd8&experience=power-bi"
)

# Track 2 - Scenario 2
PBI_REPORT_ID_TRACK2  = "b6da800f-28c2-4f6c-ad9b-b28869e54da3"
PBI_REPORT_URL_TRACK2 = (
    "https://app.powerbi.com/groups/me/reports/b6da800f-28c2-4f6c-ad9b-b28869e54da3"
    "/p20?ctid=b5af2451-e21b-4aa2-b4b5-dc5907908dd8&experience=power-bi"
)

# Credentials (from .env)
SSO_USERNAME  = os.getenv("SSO_USERNAME", "")
SSO_PASSWORD  = os.getenv("SSO_PASSWORD", "")
QLIK_USERNAME = os.getenv("QLIK_USERNAME", "")
QLIK_PASSWORD = os.getenv("QLIK_PASSWORD", "")

# CSV data path (Scenario 2)
SCENARIO2_CSV_DIR = str(
    PROJECT_ROOT / "Pipeline Hygiene - Subset for Auto Track Scenario2 "
)