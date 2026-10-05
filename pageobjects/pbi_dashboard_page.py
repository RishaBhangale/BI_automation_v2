"""
pbi_dashboard_page.py — Power BI Page Object for Lenovo Qlik/PBI Validation.

Focused on what Scenario 1 needs:
  - Open an org PBI report using a saved session (no re-login)
  - Navigate between report pages
  - List all visual titles on a page
  - Capture KPI card baseline values
  - Apply slicers and measure their impact

PBI org reports (app.powerbi.com/groups/...) embed the report inside an <iframe>.
All interactions go through the FrameLocator context.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from playwright.sync_api import Page, FrameLocator, TimeoutError as PwTimeoutError

from pageobjects.base_page import BasePage
from utils.logger import get_logger
from config.settings import (
    PBI_RENDER_TIMEOUT,
    PBI_PAGE_SWITCH_WAIT,
    QLIK_FILTER_WAIT,
)

log = get_logger("pbi_dashboard_page")

# PBI embeds the report in an iframe. These selectors locate it.
_IFRAME_SEL = (
    "iframe[title*='Power BI'], "
    "iframe[name*='PowerBI'], "
    "iframe[src*='powerbi.com'], "
    "iframe[src*='report-iframe']"
)


class PBIDashboardPage(BasePage):
    """
    Page Object for Power BI org reports (app.powerbi.com/groups/...).

    The report runs inside an <iframe>. All locators must go through
    self._ctx() which returns the FrameLocator (or the page if no iframe).
    """

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self._frame: Optional[FrameLocator] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Open & Frame
    # ─────────────────────────────────────────────────────────────────────────

    def open(self, report_url: str) -> None:
        """Navigate to the PBI report using the saved browser session."""
        log.info(f"Opening PBI report: {report_url}")
        self.page.goto(report_url, timeout=PBI_RENDER_TIMEOUT)
        self._wait_for_pbi_load()
        log.info("PBI report loaded")

    def _get_frame(self) -> Optional[FrameLocator]:
        """Detect and cache the PBI iframe FrameLocator."""
        if self._frame:
            return self._frame
        try:
            self.page.wait_for_selector(_IFRAME_SEL, timeout=PBI_RENDER_TIMEOUT)
            self._frame = self.page.frame_locator(_IFRAME_SEL)
            log.info("PBI iframe detected")
        except PwTimeoutError:
            log.info("No PBI iframe — using page directly (Publish-to-Web mode)")
            self._frame = None
        return self._frame

    def _ctx(self):
        """Return FrameLocator for iframe mode, or page for non-iframe mode."""
        f = self._get_frame()
        return f if f else self.page

    def _wait_for_pbi_load(self) -> None:
        """Wait for PBI to finish rendering the report canvas."""
        self.page.wait_for_timeout(5_000)  # initial settle
        try:
            # Page tabs appearing = report is rendered
            self._ctx().locator(
                "button[aria-label][class*='pagination'], "
                "[role='tab'], "
                "[class*='page-navigation']"
            ).first.wait_for(state="visible", timeout=PBI_RENDER_TIMEOUT)
        except Exception:
            pass
        self.page.wait_for_timeout(2_000)

    # ─────────────────────────────────────────────────────────────────────────
    # Page Navigation
    # ─────────────────────────────────────────────────────────────────────────

    def get_page_list(self) -> list[str]:
        """
        Return the list of all page names visible in the PBI report tab bar.

        PBI renders page tabs as buttons or list items at the bottom of the report.
        """
        log.info("Getting PBI page list")
        ctx = self._ctx()
        names = []

        page_tab_selectors = [
            "button[aria-label][class*='paginationButton']",
            "[role='tab'][class*='navigation']",
            "li[role='tab']",
            "[class*='pageNavigation'] button",
        ]

        for sel in page_tab_selectors:
            els = ctx.locator(sel).all()
            if not els:
                continue
            for el in els:
                try:
                    name = el.get_attribute("aria-label") or el.inner_text().strip()
                    if name and name not in names:
                        names.append(name)
                except Exception:
                    pass
            if names:
                break

        log.info(f"PBI pages found ({len(names)}): {names}")
        return names

    def switch_to_page(self, page_name: str) -> None:
        """
        Click the PBI page tab with the given name.

        Args:
            page_name: Exact page name as it appears in the PBI tab bar.
        """
        log.info(f"Switching to PBI page: '{page_name}'")
        ctx = self._ctx()

        try:
            # Try role-based lookup first
            tab = ctx.get_by_role("tab", name=page_name)
            if not tab.count():
                tab = ctx.locator(
                    f"[aria-label='{page_name}'], "
                    f"button:has-text('{page_name}')"
                )
            tab.first.click(timeout=15_000)
            self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
            log.info(f"Switched to: '{page_name}'")
        except PwTimeoutError:
            self.capture_screenshot("pbi_page_nav_fail")
            raise RuntimeError(
                f"PBI page '{page_name}' not found. "
                "Run get_page_list() to see available pages and update the mapping."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Visual Discovery
    # ─────────────────────────────────────────────────────────────────────────

    def get_visual_titles(self) -> list[str]:
        """
        Return titles of all visuals on the current PBI page.

        PBI exposes visual regions via aria-label on [role='region'] elements.
        """
        log.info("Getting PBI visual titles")
        ctx = self._ctx()
        titles = []

        selectors = [
            "[role='region'][aria-label]",
            "[class*='visualTitle'] span",
        ]

        for sel in selectors:
            els = ctx.locator(sel).all()
            for el in els:
                try:
                    label = el.get_attribute("aria-label") or el.inner_text().strip()
                    if label and label not in titles:
                        titles.append(label)
                except Exception:
                    pass

        log.info(f"Found {len(titles)} visuals")
        return titles

    # ─────────────────────────────────────────────────────────────────────────
    # Baseline Capture
    # ─────────────────────────────────────────────────────────────────────────

    def capture_page_baseline(self) -> dict[str, str | None]:
        """
        Snapshot the displayed KPI values on the current PBI page.

        Returns: {visual_title: displayed_value_string}

        Captures from [role='region'] elements (visual containers) that have
        accessible text values. KPI cards are most reliably captured this way.
        Charts (bar, line, pie) are typically not captured here.
        """
        log.info("Capturing PBI page baseline")
        ctx = self._ctx()
        results = {}

        try:
            regions = ctx.locator("[role='region'][aria-label]").all()
            for region in regions:
                try:
                    title = region.get_attribute("aria-label", timeout=2_000)
                    if not title:
                        continue

                    # Try to find the primary numeric value within this region
                    value = None
                    for val_sel in [
                        "[class*='value'][class*='kpi']",
                        "[class*='kpiValue']",
                        "[class*='visualValue']",
                        "span[class*='value']",
                    ]:
                        el = region.locator(val_sel).first
                        if el.count():
                            try:
                                value = el.inner_text(timeout=1_500).strip() or None
                                break
                            except Exception:
                                pass

                    results[title] = value
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"PBI baseline capture error: {e}")

        log.info(f"PBI baseline: {len(results)} visuals captured")
        return results

    # ─────────────────────────────────────────────────────────────────────────
    # Slicer Interaction
    # ─────────────────────────────────────────────────────────────────────────

    def apply_slicer(self, slicer_name: str, value: str) -> None:
        """
        Apply a slicer on the current PBI page.

        Finds the slicer by its visual title (aria-label of the region),
        interacts with the dropdown/list inside it, and selects the value.

        Args:
            slicer_name: The slicer visual title as it appears in PBI
            value:       The value to select
        """
        log.info(f"Applying PBI slicer: {slicer_name} = {value}")
        ctx = self._ctx()

        try:
            # Find the slicer region
            slicer_region = ctx.locator(
                f"[role='region'][aria-label*='{slicer_name}']"
            ).first

            if not slicer_region.count():
                raise RuntimeError(
                    f"Slicer region '{slicer_name}' not found. "
                    "Check get_visual_titles() to see actual visual names."
                )

            # Click the slicer to expand it
            slicer_region.click(timeout=10_000)
            self.page.wait_for_timeout(500)

            # Find and click the value
            val_el = ctx.locator(
                f"[aria-label='{value}'], [title='{value}']"
            ).first
            if not val_el.count():
                val_el = ctx.get_by_text(value, exact=True).first

            val_el.click(timeout=10_000)
            self.page.wait_for_timeout(QLIK_FILTER_WAIT)
            log.info(f"PBI slicer applied: {slicer_name} = {value}")

        except PwTimeoutError:
            self.capture_screenshot("pbi_slicer_fail")
            raise RuntimeError(
                f"Could not apply PBI slicer '{slicer_name}' = '{value}'. "
                "Slicer or value not found on this page."
            )

    def clear_all_slicers(self) -> None:
        """
        Attempt to clear all slicers on the current PBI page.

        Tries 'Reset to default' button. Logs a warning if not found.
        """
        log.info("Clearing all PBI slicers")
        ctx = self._ctx()

        reset_selectors = [
            "[aria-label*='Reset to default']",
            "[title*='Reset to default']",
            "button:has-text('Reset')",
        ]
        for sel in reset_selectors:
            try:
                btn = ctx.locator(sel).first
                if btn.is_visible(timeout=2_500):
                    btn.click()
                    self.page.wait_for_timeout(2_000)
                    log.info("PBI slicers cleared")
                    return
            except Exception:
                pass

        log.warning("PBI reset button not found — slicers may not have been cleared")
