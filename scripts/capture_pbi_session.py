"""
capture_pbi_session.py — Save Power BI session for automated test runs.

Run once manually when setting up or when session expires:
    python scripts/capture_pbi_session.py

A browser window opens. Log in with Microsoft SSO.
Once the PBI report is loaded, press Enter here.
Session saved to playwright/.auth/pbi_session.json.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from config.settings import PBI_REPORT_URL_TRACK1, PBI_SESSION_FILE, AUTH_DIR


def capture():
    Path(AUTH_DIR).mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 60)
    print("  Power BI — Session Capture")
    print("=" * 60)
    print(f"  URL: {PBI_REPORT_URL_TRACK1}")
    print(f"  Saving to: {PBI_SESSION_FILE}")
    print("  A browser window will open. Log in with Microsoft SSO.")
    print("  When the PBI report is visible and loaded, come back here.")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.goto(PBI_REPORT_URL_TRACK1, timeout=90_000)
        input("\n  >>> Press ENTER after the PBI report is loaded: ")
        ctx.storage_state(path=PBI_SESSION_FILE)
        print(f"\n  Session saved: {PBI_SESSION_FILE}")
        browser.close()


if __name__ == "__main__":
    capture()
