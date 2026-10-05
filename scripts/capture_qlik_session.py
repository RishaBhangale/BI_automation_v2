"""
capture_qlik_session.py — Save Qlik Cloud session for automated test runs.

Run once manually when setting up or when session expires:
    python scripts/capture_qlik_session.py

A browser window opens. Log in with your Lenovo/Qlik credentials.
Once on the Qlik hub, press Enter here.
Session saved to playwright/.auth/qlik_session.json.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from config.settings import QLIK_BASE_URL, QLIK_SESSION_FILE, AUTH_DIR


def capture():
    Path(AUTH_DIR).mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 60)
    print("  Qlik Cloud — Session Capture")
    print("=" * 60)
    print(f"  URL: {QLIK_BASE_URL}")
    print(f"  Saving to: {QLIK_SESSION_FILE}")
    print("  A browser window will open. Log in manually.")
    print("  When you see the Qlik app list, come back here.")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()
        page.goto(QLIK_BASE_URL, timeout=60_000)
        input("\n  >>> Press ENTER after you are logged in and on the Qlik hub: ")
        ctx.storage_state(path=QLIK_SESSION_FILE)
        print(f"\n  Session saved: {QLIK_SESSION_FILE}")
        browser.close()


if __name__ == "__main__":
    capture()
