"""
diagnose_pbi_dom.py — One-off helper to see what the PBI report DOM really looks like.

Run when get_page_list() returns 0 pages (or after PBI UI changes):
    python scripts/diagnose_pbi_dom.py

Uses the saved PBI session. Prints the page list it found and writes a screenshot
and the full HTML into logs/ so the page-tab selector can be pinned precisely.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from config.settings import PBI_REPORT_URL_TRACK1, PBI_SESSION_FILE
from pageobjects.pbi_dashboard_page import PBIDashboardPage


def main():
    if not Path(PBI_SESSION_FILE).exists():
        sys.exit(f"No session at {PBI_SESSION_FILE}. Run scripts/capture_pbi_session.py first.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome", args=["--start-maximized"])
        ctx = browser.new_context(storage_state=PBI_SESSION_FILE, no_viewport=True)
        page = ctx.new_page()
        pbi = PBIDashboardPage(page)
        pbi.open(PBI_REPORT_URL_TRACK1)
        names = pbi.get_page_list()
        print(f"\nPages found: {len(names)}")
        for n in names:
            print(f"  - {n}")
        pbi.dump_diagnostics("pbi_diag_manual")   # always dump, even on success
        input("\nPress ENTER to close the browser... ")
        browser.close()


if __name__ == "__main__":
    main()