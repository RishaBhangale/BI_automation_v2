"""
pbi_dashboard_page.py — Power BI Page Object for Lenovo Qlik/PBI Validation.

Focused on what Scenario 1 needs:
  - Open an org PBI report using a saved session (no re-login)
  - Navigate between report pages
  - List all visual titles on a page
  - Capture KPI card baseline values
  - Apply slicers and measure their impact

PBI org reports (app.powerbi.com/groups/...) are usually rendered directly in the
page DOM (no iframe); embedded/publish-to-web reports use an <iframe>. All
interactions go through self._ctx(), which handles both cases.
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
    PBI_IFRAME_PROBE_TIMEOUT,
    PBI_PAGELIST_TIMEOUT,
    QLIK_FILTER_WAIT,
    LOG_DIR,
)

log = get_logger("pbi_dashboard_page")

# PBI may embed the report in an iframe (embedded / publish-to-web scenarios).
# In the normal Power BI service (app.powerbi.com/groups/...) there is usually
# NO such iframe and the report lives in the top-level page DOM.
_IFRAME_SEL = (
    "iframe[title*='Power BI'], "
    "iframe[name*='PowerBI'], "
    "iframe[src*='powerbi.com'], "
    "iframe[src*='report-iframe']"
)

# Any of these visible = the report canvas has started rendering.
_READY_SEL = (
    "visual-container, "
    "[class*='visualContainer'], "
    "[class*='visual-container'], "
    "[role='region'][aria-label]"
)

# Page-tab selectors, tried in order (most specific first).
_PAGE_TAB_SELECTORS = [
    "button[aria-label][class*='paginationButton']",
    "[class*='pageNavigation'] [role='tab']",
    "[class*='pageNavigation'] button",
    "[class*='page-navigation'] [role='tab']",
    "[class*='page-navigation'] button",
    "[aria-label*='page navigation' i] [role='tab']",
    "[aria-label*='page navigation' i] button",
    "[role='tablist'] [role='tab']",
    "li[role='tab']",
    "[role='tab']",
    "[aria-label='Pages' i] [role='listitem']",
    "[aria-label='Pages' i] button",
]

# Names that are chrome/controls or other platform tabs, never PBI report pages.
_NOT_PAGE_NAMES = {
    "pages", "page navigation", "previous page", "next page", "show pages", "hide pages",
    "sheets", "bookmarks", "stories",
}

_LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.windows.net")


class PBIDashboardPage(BasePage):
    """
    Page Object for Power BI org reports (app.powerbi.com/groups/...).

    self._ctx() returns a FrameLocator if the report is inside an iframe,
    otherwise the Page itself (report rendered in the main DOM).
    """

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self._frame: Optional[FrameLocator] = None
        self._frame_checked = False
        self._report_url: str = ""

    # ─────────────────────────────────────────────────────────────────────────
    # Open & Frame
    # ─────────────────────────────────────────────────────────────────────────

    def open(self, report_url: str) -> None:
        """Navigate to the PBI report using the saved browser session or reuse warm page."""
        current_url = self.page.url
        if "powerbi.com" in current_url and "/reports/" in current_url:
            try:
                if self._ctx().locator(_READY_SEL).first.is_visible(timeout=1_500):
                    log.info("PBI report already open and canvas active — reusing warm page")
                    return
            except Exception:
                pass

        log.info(f"Opening PBI report: {report_url}")
        self._report_url = report_url
        self._frame = None
        self._frame_checked = False          # new navigation -> re-detect iframe
        self.page.goto(report_url, timeout=PBI_RENDER_TIMEOUT)
        self._assert_not_login_page()
        self._wait_for_pbi_load()
        self._assert_not_login_page()
        log.info("PBI report loaded")

    def _assert_not_login_page(self) -> None:
        """Handle SSO redirect: pause for user login in headed browser instead of crashing."""
        url = self.page.url.lower()
        if any(host in url for host in _LOGIN_HOSTS):
            self.capture_screenshot("pbi_sso_required")
            log.warning(
                f"[AUTH REQUIRED] Power BI redirected to login page ({self.page.url[:80]}...). "
                "Please complete SSO in the open browser window. Waiting up to 60s..."
            )
            try:
                self.page.wait_for_url(
                    lambda u: not any(host in u.lower() for host in _LOGIN_HOSTS) and "powerbi.com" in u.lower(),
                    timeout=60_000,
                )
                log.info("SSO completed! Report loading...")
                self._wait_for_pbi_load()
            except Exception:
                raise RuntimeError(
                    f"Power BI redirected to login page ({self.page.url[:80]}...) "
                    "and SSO was not completed within 60s."
                )

    def _get_frame(self) -> Optional[FrameLocator]:
        """Detect (once per navigation) whether the report is inside an iframe."""
        if self._frame_checked:
            return self._frame
        try:
            self.page.wait_for_selector(_IFRAME_SEL, timeout=PBI_IFRAME_PROBE_TIMEOUT)
            self._frame = self.page.frame_locator(_IFRAME_SEL).first
            log.info("PBI iframe detected")
        except PwTimeoutError:
            log.info("No PBI iframe - using main page DOM")
            self._frame = None
        self._frame_checked = True
        return self._frame

    def _ctx(self):
        """Return FrameLocator for iframe mode, or page for non-iframe mode."""
        f = self._get_frame()
        return f if f else self.page

    def _wait_for_pbi_load(self) -> None:
        """Wait for PBI to start rendering the report canvas."""
        self.page.wait_for_timeout(3_000)  # initial settle
        try:
            self._ctx().locator(_READY_SEL).first.wait_for(
                state="visible", timeout=PBI_RENDER_TIMEOUT
            )
        except PwTimeoutError:
            log.warning(
                "No report visuals became visible within "
                f"{PBI_RENDER_TIMEOUT // 1000}s - continuing anyway"
            )
            self.capture_screenshot("pbi_load_timeout")
        self.page.wait_for_timeout(2_000)

    # ─────────────────────────────────────────────────────────────────────────
    # Page Navigation
    # ─────────────────────────────────────────────────────────────────────────

    def _collect_page_names(self, ctx) -> list[str]:
        """One snapshot pass over the page-tab selectors; first selector that yields names wins."""
        for sel in _PAGE_TAB_SELECTORS:
            try:
                els = ctx.locator(sel).all()
            except Exception:
                continue
            names: list[str] = []
            for el in els:
                try:
                    name = (el.get_attribute("aria-label") or el.inner_text() or "").strip()
                except Exception:
                    continue
                if name and name.lower() not in _NOT_PAGE_NAMES and name not in names:
                    names.append(name)
            if names:
                log.debug(f"Page tabs matched selector: {sel}")
                return names
        return []

    def _try_open_pages_pane(self, ctx) -> None:
        """If the left 'Pages' pane is collapsed, try to expand it (best effort)."""
        try:
            btn = ctx.get_by_role("button", name=re.compile(r"^(show )?pages$", re.I)).first
            if btn.is_visible(timeout=1_500):
                log.info("Expanding PBI Pages pane")
                btn.click(timeout=3_000)
                self.page.wait_for_timeout(1_000)
        except Exception:
            pass

    def get_page_list(self) -> list[str]:
        """
        Return the names of all pages in the PBI report.

        Polls (up to PBI_PAGELIST_TIMEOUT) because page tabs render after the
        visuals. If nothing is found, dumps DOM diagnostics to logs/ so the
        selector can be corrected from real markup.
        """
        log.info("Getting PBI page list")
        if "powerbi.com" not in self.page.url.lower() and self._report_url:
            log.warning(
                f"PBIDashboardPage.get_page_list() invoked while on non-PBI URL ({self.page.url}). "
                f"Re-navigating to {self._report_url}..."
            )
            self.open(self._report_url)

        ctx = self._ctx()
        deadline = time.monotonic() + PBI_PAGELIST_TIMEOUT / 1000
        tried_pane = False
        names: list[str] = []

        while True:
            names = self._collect_page_names(ctx)
            if names or time.monotonic() >= deadline:
                break
            if not tried_pane:
                self._try_open_pages_pane(ctx)
                tried_pane = True
            self.page.wait_for_timeout(1_500)

        if not names:
            self.dump_diagnostics("pbi_page_list_empty")

        log.info(f"PBI pages found ({len(names)}): {names}")
        return names

    def dump_diagnostics(self, tag: str = "pbi_diag") -> None:
        """Save screenshot + full HTML + a summary of candidate nav elements to help fix selectors."""
        import os
        from datetime import datetime

        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.capture_screenshot(tag)
            html_path = os.path.join(LOG_DIR, f"{tag}_{ts}.html")
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(self.page.content())
            log.warning(f"[DIAG] url={self.page.url[:120]}")
            log.warning(f"[DIAG] title={self.page.title()!r}")
            log.warning(f"[DIAG] iframes on page={self.page.locator('iframe').count()}")
            log.warning(f"[DIAG] full HTML saved: {html_path}")
            summary = self.page.evaluate(
                """() => {
                    const out = [];
                    const q = "[role='tab'],[role='tablist'],[role='listitem'],[role='region'],"
                            + "button[aria-label],[class*='age' i][class*='av' i]";
                    document.querySelectorAll(q).forEach(e => {
                        if (out.length < 60) out.push([
                            e.tagName.toLowerCase(),
                            e.getAttribute('role') || '',
                            (e.getAttribute('aria-label') || e.innerText || '').trim().slice(0, 60),
                            (e.className && e.className.toString ? e.className.toString() : '').slice(0, 70),
                        ].join(' | '));
                    });
                    return out;
                }"""
            )
            for row in summary:
                log.warning(f"[DIAG] {row}")
        except Exception as e:  # diagnostics must never mask the real failure
            log.warning(f"[DIAG] could not collect diagnostics: {e}")

    def switch_to_page(self, page_name: str) -> None:
        """
        Click the PBI page tab with the given name.

        Args:
            page_name: Exact or partial page name as it appears in the PBI tab bar.
        """
        log.info(f"Switching to PBI page: '{page_name}'")
        ctx = self._ctx()

        # 1. Clean any hanging modal or backdrop if present
        try:
            backdrop = self.page.locator(".cdk-overlay-backdrop, [role='dialog']").first
            if backdrop.count() > 0 and backdrop.is_visible(timeout=500):
                log.info("Dismissing hanging overlay before page switch")
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(500)
        except Exception:
            pass

        # 2. Check if already on this page
        try:
            active_tab = ctx.locator(
                f"[role='tab'][aria-label*='{page_name}' i][aria-selected='true'], "
                f"[role='tab'][aria-label*='{page_name} Selected' i], "
                f"button[data-testid='pages-navigation-list-items'][aria-selected='true']:has-text('{page_name}')"
            ).first
            if active_tab.count() > 0 and active_tab.is_visible(timeout=1_000):
                log.info(f"Already on page '{page_name}' — skipping navigation click")
                return
        except Exception:
            pass

        try:
            # 3. Locate tab element (exact match first, then partial match)
            tab = ctx.locator(
                f"button:text-is('{page_name}'), "
                f"button[aria-label='{page_name}'], "
                f"[role='tab'][aria-label='{page_name}'], "
                f"button[data-testid='pages-navigation-list-items'][aria-label*='{page_name}' i], "
                f"button[aria-label*='{page_name}' i], "
                f"[role='tab'][aria-label*='{page_name}' i], "
                f"button:has-text('{page_name}')"
            ).first

            if not tab.count():
                self._try_open_pages_pane(ctx)
                tab = ctx.locator(
                    f"button[data-testid='pages-navigation-list-items'][aria-label*='{page_name}' i], "
                    f"button[aria-label*='{page_name}' i], "
                    f"[role='tab'][aria-label*='{page_name}' i], "
                    f"button:has-text('{page_name}')"
                ).first

            if tab.count() > 0:
                tab.scroll_into_view_if_needed()
                try:
                    tab.click(timeout=5_000)
                except Exception:
                    tab.dispatch_event("click")
                self.page.wait_for_timeout(PBI_PAGE_SWITCH_WAIT)
                log.info(f"Switched to: '{page_name}'")
                return
            else:
                raise RuntimeError(f"Tab element for '{page_name}' not found")
        except Exception as e:
            self.capture_screenshot("pbi_page_nav_fail")
            raise RuntimeError(
                f"PBI page '{page_name}' not found ({e}). "
                "Run get_page_list() to see available pages and update the mapping."
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Visual Discovery
    # ─────────────────────────────────────────────────────────────────────────

    def get_visual_titles(self) -> list[str]:
        """
        Return titles of all data visuals on the current PBI page.
        """
        log.info("Getting PBI visual titles")
        titles = []
        ignored_titles = {"power bi report", "legend", "navigation", "visuals", "filters", "pages"}

        eval_script = """
            () => {
                const titles = [];
                const ignored = new Set(["power bi report", "legend", "navigation", "visuals", "filters", "pages"]);
                const vcs = document.querySelectorAll("visual-container, [data-automation-type='visualContainer']");
                let idx = 0;
                for (const vc of vcs) {
                    idx++;
                    const isBtnSlicer = vc.querySelector(".buttonSlicerVisual, [class*='buttonSlicer']") !== null;
                    const innerVc = vc.querySelector(".visualContainer");
                    let title = null;
                    if (innerVc && innerVc.getAttribute("aria-label")) {
                        title = innerVc.getAttribute("aria-label").trim();
                    }
                    if (!title) {
                        const lbl = vc.querySelector("p[id*='visualsLabel']");
                        if (lbl) title = lbl.textContent.trim();
                    }
                    if (!title) {
                        const h = vc.querySelector("[class*='visualTitle'], [class*='header-title'], h3");
                        if (h && !h.textContent.toLowerCase().includes("press enter to explore")) {
                            title = h.textContent.trim();
                        }
                    }
                    if (!title) {
                        const roledesc = innerVc ? innerVc.getAttribute("aria-roledescription") : "";
                        if (roledesc && roledesc.toLowerCase().includes("table")) {
                            const ths = Array.from(vc.querySelectorAll("[role='columnheader']")).slice(0, 3).map(e => e.textContent.trim());
                            title = ths.length > 0 ? `Table: ${ths.join(', ')}` : "Table Visual";
                        } else if (roledesc && !isBtnSlicer) {
                            title = `${roledesc} ${idx}`;
                        }
                    }
                    if (title) {
                        if (title.toLowerCase().startsWith("press enter to explore data")) {
                            title = title.substring("press enter to explore data".length).trim();
                        }
                        title = title.trim();
                        if (title && !ignored.has(title.toLowerCase()) && !titles.includes(title)) {
                            titles.push(title);
                        }
                    }
                }
                return titles;
            }
        """
        try:
            titles = self.page.evaluate(eval_script) or []
        except Exception as e:
            log.warning(f"Error evaluating PBI visual titles via JS: {e}")

        if not titles:
            ctx = self._ctx()
            selectors = [
                "[data-automation-type='visualContainer'] .visualContainer[aria-label]",
                "visual-container .visualContainer[aria-label]",
                "p[id*='visualsLabel']",
                "[class*='visualTitle'] span",
                "visual-container h3",
            ]
            for sel in selectors:
                try:
                    for el in ctx.locator(sel).all():
                        t = el.get_attribute("aria-label") or el.inner_text().strip()
                        if t.lower().startswith("press enter to explore data"):
                            t = t[len("press enter to explore data"):].strip()
                        if t and t.lower() not in ignored_titles and t not in titles:
                            titles.append(t)
                except Exception:
                    pass

        log.info(f"PBI visual titles found ({len(titles)}): {titles}")
        return titles

    # ─────────────────────────────────────────────────────────────────────────
    # Baseline Capture
    # ─────────────────────────────────────────────────────────────────────────

    def capture_page_baseline(self) -> dict[str, str | None]:
        """
        Snapshot displayed visual titles and data states on the current PBI page.

        Returns: {visual_title: displayed_value_or_data_summary}

        Captures real visual containers (KPIs, bar/line/pie charts, tables)
        and extracts data representations (KPI values, chart data labels, bar/slice values, table cells).
        """
        log.info("Capturing PBI page baseline")
        eval_script = """
            () => {
                const results = {};
                const ignored = new Set(["power bi report", "legend", "navigation", "visuals", "filters", "pages"]);
                const vcs = document.querySelectorAll("visual-container, [data-automation-type='visualContainer']");
                let idx = 0;
                for (const vc of vcs) {
                    idx++;
                    const isBtnSlicer = vc.querySelector(".buttonSlicerVisual, [class*='buttonSlicer']") !== null;
                    const innerVc = vc.querySelector(".visualContainer");
                    let title = null;
                    if (innerVc && innerVc.getAttribute("aria-label")) {
                        title = innerVc.getAttribute("aria-label").trim();
                    }
                    if (!title) {
                        const lbl = vc.querySelector("p[id*='visualsLabel']");
                        if (lbl) title = lbl.textContent.trim();
                    }
                    if (!title) {
                        const h = vc.querySelector("[class*='visualTitle'], [class*='header-title'], h3");
                        if (h && !h.textContent.toLowerCase().includes("press enter to explore")) {
                            title = h.textContent.trim();
                        }
                    }
                    if (!title) {
                        const roledesc = innerVc ? innerVc.getAttribute("aria-roledescription") : "";
                        if (roledesc && roledesc.toLowerCase().includes("table")) {
                            const ths = Array.from(vc.querySelectorAll("[role='columnheader']")).slice(0, 3).map(e => e.textContent.trim());
                            title = ths.length > 0 ? `Table: ${ths.join(', ')}` : "Table Visual";
                        } else if (roledesc && !isBtnSlicer) {
                            title = `${roledesc} ${idx}`;
                        } else if (isBtnSlicer) {
                            title = `Button Slicer ${idx}`;
                        } else {
                            title = `Visual_${idx}`;
                        }
                    }

                    if (title.toLowerCase().startsWith("press enter to explore data")) {
                        title = title.substring("press enter to explore data".length).trim();
                    }
                    title = title.trim();
                    if (!title || ignored.has(title.toLowerCase())) continue;

                    // 1. KPI single value
                    let val = null;
                    const kpi = vc.querySelector("[class*='kpiValue'], [class*='visualValue'], [class*='value']");
                    if (kpi && kpi.textContent.trim()) {
                        val = kpi.textContent.trim();
                    }
                    // 2. Chart data labels
                    if (!val) {
                        const labels = Array.from(vc.querySelectorAll("text.label, tspan.label-tspan, .label-container text"))
                            .map(e => e.textContent.trim()).filter(Boolean);
                        if (labels.length > 0) val = labels.slice(0, 8).join(", ");
                    }
                    // 3. Rect/path bars and slices with aria-label
                    if (!val) {
                        const rects = Array.from(vc.querySelectorAll("rect.bar[aria-label], g.slice[aria-label], path.slice[aria-label]"))
                            .map(e => e.getAttribute("aria-label")).filter(Boolean);
                        if (rects.length > 0) val = rects.slice(0, 8).join(", ");
                    }
                    // 4. Table cells
                    if (!val) {
                        const cells = Array.from(vc.querySelectorAll("[role='gridcell']"))
                            .map(e => e.textContent.trim()).filter(Boolean);
                        if (cells.length > 0) val = cells.slice(0, 10).join(" | ");
                    }
                    // 5. Button slicer selected item
                    if (!val && isBtnSlicer) {
                        const selectedBtn = vc.querySelector(".selected, [aria-selected='true'], [aria-checked='true']");
                        if (selectedBtn) val = selectedBtn.textContent.trim();
                    }
                    // 6. Generic text summary
                    if (!val) {
                        const allTxt = (vc.textContent || "").replace(/Press Enter to explore data/g, "").trim();
                        const words = allTxt.split(/\\s+/).slice(0, 10);
                        val = words.length > 0 ? words.join(" ") : "rendered";
                    }

                    results[title] = val;
                }
                return results;
            }
        """
        results = {}
        try:
            results = self.page.evaluate(eval_script) or {}
        except Exception as e:
            log.warning(f"Error evaluating PBI baseline via JS: {e}")

        # Fallback to region locator if JS returned empty
        if not results:
            ctx = self._ctx()
            try:
                for region in ctx.locator("[role='region'][aria-label]").all():
                    t = region.get_attribute("aria-label", timeout=1_000)
                    if t and t.lower() not in {"power bi report", "legend", "navigation"}:
                        val_el = region.locator("[class*='value'], text").first
                        results[t] = val_el.inner_text(timeout=500).strip() if val_el.count() else "rendered"
            except Exception as e:
                log.debug(f"PBI baseline region fallback error: {e}")

        log.info(f"PBI baseline: {len(results)} visuals captured ({list(results.keys())[:4]}...)")
        return results

    def deselect_all_visuals(self) -> None:
        """
        Deselect any active visual on the canvas so the Filters Pane shows
        page-level / report-level filters instead of 'Filters on this visual'.
        """
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(100)

            # Click canvas margin padding in an empty area
            canvas = self.page.locator(".exploreCanvas, .displayAreaContainer, .canvasFlexBox, .visualArea").first
            if canvas.count() > 0 and canvas.is_visible():
                try:
                    canvas.click(position={"x": 5, "y": 5}, timeout=1_500)
                    self.page.wait_for_timeout(200)
                except Exception:
                    self.page.mouse.click(500, 110)
            else:
                self.page.mouse.click(500, 110)
                self.page.wait_for_timeout(200)

            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
        except Exception as e:
            log.debug(f"Deselect visuals error (non-fatal): {e}")

    def select_visual(self, visual_target: Optional[str] = None) -> bool:
        """
        Locate and click a visual on the canvas to activate its 'Filters on this visual'
        section in the Power BI Filters Pane.

        Args:
            visual_target: Title, partial name, or keywords of the visual to select.
                          If None, clicks the primary data visual (chart or table) on the canvas.
        Returns:
            True if a visual was successfully located and clicked, False otherwise.
        """
        log.info(f"Selecting visual on canvas: {visual_target or '<primary data visual>'}")
        ctx = self._ctx()
        try:
            candidate_vis = None
            if visual_target:
                v_clean = visual_target.strip().lower()
                is_target_qty = ("quant" in v_clean) or ("qty" in v_clean)
                is_target_rev = ("rev" in v_clean) or ("usd" in v_clean) or ("rate" in v_clean)

                stop_words = {"revenue", "quantity", "by", "the", "for", "in", "of", "and", "usd", "@", "actual", "rate"}
                target_tokens = [w for w in re.split(r"\s+", v_clean) if len(w) > 2 and w not in stop_words]

                selectors = [
                    f"visual-container:has([class*='visualTitle']:has-text('{visual_target}'))",
                    f"visual-container[aria-label*='{visual_target}' i]",
                    f"[class*='visualContainer'][aria-label*='{visual_target}' i]",
                    f"visual-container:has-text('{visual_target}')",
                    f"[class*='visualContainer']:has-text('{visual_target}')",
                ]
                for sel in selectors:
                    loc = ctx.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        candidate_vis = loc
                        log.debug(f"Matched target visual with selector: {sel}")
                        break

                if not candidate_vis:
                    all_vcs = ctx.locator("visual-container, [data-automation-type='visualContainer']").all()
                    for vc in all_vcs:
                        try:
                            if vc.locator(".buttonSlicerVisual, [class*='buttonSlicer']").count() > 0:
                                continue
                            txt = (vc.inner_text(timeout=500) or "").lower()
                            aria = (vc.get_attribute("aria-label") or "").lower()
                            combined = f"{txt} {aria}"

                            # Prevent cross-measure matching (Revenue vs Quantity)
                            if is_target_qty and ("revenue" in combined or "rev " in combined or "usd" in combined) and ("quantity" not in combined and "qty" not in combined):
                                continue
                            if is_target_rev and ("quantity" in combined or "qty" in combined) and ("revenue" not in combined and "rev" not in combined and "usd" not in combined):
                                continue

                            if target_tokens and all(tok in combined for tok in target_tokens):
                                candidate_vis = vc
                                log.debug(f"Matched target visual via tokens {target_tokens}")
                                break
                            elif not target_tokens and (is_target_qty or is_target_rev):
                                if is_target_qty and ("quantity" in combined or "qty" in combined):
                                    candidate_vis = vc
                                    log.debug("Matched target visual via Quantity measure token")
                                    break
                                elif is_target_rev and ("revenue" in combined or "rev" in combined or "usd" in combined):
                                    candidate_vis = vc
                                    log.debug("Matched target visual via Revenue measure token")
                                    break
                        except Exception:
                            continue

            if not candidate_vis:
                data_vis_selectors = [
                    "visual-container:has([aria-roledescription*='chart' i])",
                    "visual-container:has([aria-roledescription*='table' i])",
                    "visual-container:has(svg)",
                    "visual-container:has([class*='tablix'])",
                    "visual-container:has([class*='pivotTable'])",
                ]
                for d_sel in data_vis_selectors:
                    loc = ctx.locator(d_sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        candidate_vis = loc
                        log.debug(f"Matched fallback data visual with selector: {d_sel}")
                        break

            # Fallback: pick the first visible canvas visual container (excluding toggle button slicers)
            if not candidate_vis:
                all_vcs = ctx.locator("visual-container, [data-automation-type='visualContainer']").all()
                for vc in all_vcs:
                    try:
                        if vc.locator(".buttonSlicerVisual, [class*='buttonSlicer']").count() > 0:
                            continue
                        if vc.is_visible(timeout=400):
                            candidate_vis = vc
                            log.debug("Matched first visible non-toggle canvas visual container")
                            break
                    except Exception:
                        continue

            if not candidate_vis:
                log.warning(f"Could not locate visual '{visual_target}' on canvas to select")
                return False

            candidate_vis.scroll_into_view_if_needed()
            clicked = False

            # 1. Right-click activation: as shown in Image 1, right-clicking the visual
            # populates and focuses 'Filters on this visual' in the Filters Pane.
            try:
                candidate_vis.click(button="right", timeout=2_000, force=True)
                clicked = True
                self.page.wait_for_timeout(400)
                # Press Escape to dismiss the context menu ('Copy', 'Share', etc.) without losing visual selection
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(200)
                log.info(f"Visual '{visual_target or '<primary data visual>'}' right-clicked to activate visual filters")
            except Exception as ex_r:
                log.debug(f"Right-click on visual container failed: {ex_r}")

            # 2. Header click fallback
            if not clicked:
                header = candidate_vis.locator(
                    "[class*='visualTitle'], [class*='header-title'], h3, .visualHeader, .visualHeaderWrapper, .header-text"
                ).first
                if header.count() > 0 and header.is_visible(timeout=800):
                    try:
                        header.click(timeout=1_500, force=True)
                        clicked = True
                    except Exception as ex_h:
                        log.debug(f"Header click with force failed: {ex_h}")

            # 3. Margin click fallback
            if not clicked:
                try:
                    candidate_vis.click(position={"x": 15, "y": 15}, timeout=1_500, force=True)
                    clicked = True
                except Exception as ex_c:
                    log.debug(f"Container margin click with force failed: {ex_c}")

            # 4. Dispatch event fallback
            if not clicked:
                try:
                    candidate_vis.dispatch_event("click")
                    clicked = True
                    log.debug("Fallback dispatch_event('click') executed on visual container")
                except Exception as ex_d:
                    log.warning(f"Fallback dispatch_event('click') failed: {ex_d}")

            self.page.wait_for_timeout(500)
            log.info(f"Visual '{visual_target or '<primary data visual>'}' selected on canvas")
            return True

        except Exception as e:
            log.warning(f"Error selecting visual '{visual_target}' on canvas: {e}")
            return False

    def set_metric_toggle(self, metric_name: str) -> bool:
        """
        Click the canvas toggle button corresponding to the requested metric.
        e.g., if measure is 'Revenue USD @ Actual Rate' -> click 'Revenue'.
        if measure is 'Quantity' -> click 'Quantity'.
        """
        if not metric_name:
            return False
        ctx = self._ctx()
        m_lower = metric_name.lower()
        target = "Revenue" if ("rev" in m_lower or "usd" in m_lower or "rate" in m_lower) else ("Quantity" if ("qty" in m_lower or "quant" in m_lower) else None)
        if not target:
            return False

        log.info(f"Setting metric toggle to: '{target}'")
        selectors = [
            f"[class*='buttonSlicerVisual'] div[title='{target}']",
            f"[class*='button-slicer-text-wrap'] :text-is('{target}')",
            f"div[title='{target}']",
            f"button:has-text('{target}')",
            f"[role='button']:has-text('{target}')",
        ]
        clicked = False
        for sel in selectors:
            try:
                btn = ctx.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1_500):
                    btn.click(timeout=3_000)
                    self.page.wait_for_timeout(1_500)
                    log.info(f"Metric toggle '{target}' clicked successfully")
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:
            self.deselect_all_visuals()
            self.page.wait_for_timeout(1_000)
            return True
        log.debug(f"Metric toggle '{target}' not found on current page")
        return False


    def extract_visual_value(
        self,
        visual_target: str,
        category_filter: Optional[Union[str, list[str]]] = None,
    ) -> Optional[str]:
        """
        Extract the displayed numeric or total value for a target visual on the current page.

        Supports:
          - KPI cards (kpiValue, visualValue, text)
          - Bar & column charts (data bars, axis ticks, category matching)
          - Pie & donut charts (slices with accessibility aria-labels)
          - Tables & matrix grids
        """
        # Ensure stabilization wait for visual rendering post-toggle/slicer
        self.page.wait_for_timeout(1_000)

        target_clean = visual_target.replace("'", "\\'")
        cats_json = "[]"
        if category_filter:
            if isinstance(category_filter, str):
                c_list = [c.strip().lower() for c in category_filter.split(",") if c.strip()]
            else:
                c_list = [str(c).strip().lower() for c in category_filter if str(c).strip()]
            import json
            cats_json = json.dumps(c_list)

        eval_script = f"""
            () => {{
                const targetLower = '{target_clean}'.toLowerCase().trim();
                const targetWords = targetLower.split(/\\s+/).filter(w => w.length > 2);
                const catFilters = {cats_json};

                const isTargetQty = targetLower.includes('quant') || targetLower.includes('qty');
                const isTargetRev = targetLower.includes('rev') || targetLower.includes('usd') || targetLower.includes('rate');

                // 1. Find matching visual container
                const vcs = Array.from(document.querySelectorAll('visual-container, [class*=\"visualContainer\"]'));
                let targetVc = null;

                for (const vc of vcs) {{
                    const titleEl = vc.querySelector(\"[class*='visualTitle'], h3, [class*='header-title']\");
                    const tText = (titleEl ? titleEl.textContent : '').toLowerCase().trim();
                    const aria = (vc.getAttribute('aria-label') || '').toLowerCase().trim();
                    const combined = tText + ' ' + aria;

                    // Differentiate Measure Tokens: prevent cross-measure matching
                    if (isTargetQty && (combined.includes('revenue') || combined.includes('rev ') || combined.includes('usd'))) continue;
                    if (isTargetRev && (combined.includes('quantity') || combined.includes('qty'))) continue;

                    if (tText.includes(targetLower) || aria.includes(targetLower)) {{
                        targetVc = vc;
                        break;
                    }}
                    const matchWords = targetWords.filter(w => tText.includes(w) || aria.includes(w));
                    if (matchWords.length >= Math.min(2, targetWords.length) && targetWords.length > 0) {{
                        targetVc = vc;
                        break;
                    }}
                }}

                if (!targetVc) {{
                    for (const vc of vcs) {{
                        const fullText = (vc.textContent || '').toLowerCase();
                        if (isTargetQty && (fullText.includes('revenue') || fullText.includes('rev ') || fullText.includes('usd'))) continue;
                        if (isTargetRev && (fullText.includes('quantity') || fullText.includes('qty'))) continue;

                        const matchWords = targetWords.filter(w => fullText.includes(w));
                        if (matchWords.length === targetWords.length && targetWords.length > 0) {{
                            targetVc = vc;
                            break;
                        }}
                    }}
                }}

                if (!targetVc) {{
                    targetVc = document.querySelector(
                        'visual-container.selected, visual-container.visualContainerFocused, ' +
                        'visual-container:has([class*="selected"]), visual-container:has(.visualContainerSelection)'
                    );
                }}

                if (!targetVc) return null;

                // 2. Case A: KPI card values
                const cardSelectors = [
                    \"[class*='kpiValue']\",
                    \"[class*='visualValue']\",
                    \"[class*='card'] [class*='value']\",
                    \".value.kpi\",
                    \"span[class*='value']\",
                ];
                for (const sel of cardSelectors) {{
                    const el = targetVc.querySelector(sel);
                    if (el && el.textContent.trim()) {{
                        return el.textContent.trim();
                    }}
                }}

                // 3. Case B: Bar / Column Chart with rect[aria-label] and axis tick labels
                const barRects = Array.from(targetVc.querySelectorAll(
                    '[data-automation-type=\"column-chart-rect\"], [data-automation-type=\"bar-chart-rect\"], rect[aria-label]'
                )).filter(r => {{
                    const a = r.getAttribute('aria-label');
                    return a && !isNaN(parseFloat(a));
                }});

                const dedup = s => {{
                    if (s.length % 2 === 0 && s.slice(0, s.length / 2) === s.slice(s.length / 2)) {{
                        return s.slice(0, s.length / 2);
                    }}
                    return s;
                }};

                const axisTickEls = Array.from(targetVc.querySelectorAll('text')).map(t => dedup(t.textContent.trim()));
                const nonNumericAxis = [];
                for (const t of axisTickEls) {{
                    if (!t || /^[\\d.,]+[kmgb%]?$/i.test(t) || t.includes('GEO Level') || t.includes('Measure')) continue;
                    if (!nonNumericAxis.includes(t)) nonNumericAxis.push(t);
                }}

                if (barRects.length > 0 && nonNumericAxis.length > 0) {{
                    const nCats = nonNumericAxis.length;
                    const catMap = {{}};
                    barRects.forEach((r, i) => {{
                        const cat = nonNumericAxis[i % nCats];
                        const val = parseFloat(r.getAttribute('aria-label'));
                        if (cat && !isNaN(val)) {{
                            if (!(cat in catMap)) {{
                                catMap[cat] = val;
                            }}
                        }}
                    }});

                    if (catFilters.length > 0) {{
                        let matchedSum = 0;
                        let found = false;
                        for (const [cName, val] of Object.entries(catMap)) {{
                            if (catFilters.some(cf => cName.toLowerCase().includes(cf) || cf.includes(cName.toLowerCase()))) {{
                                matchedSum += val;
                                found = true;
                            }}
                        }}
                        if (found) return String(matchedSum);
                    }} else {{
                        const total = Object.values(catMap).reduce((a, b) => a + b, 0);
                        return String(total);
                    }}
                }}

                // 4. Case C: Donut / Pie Chart slices (path[aria-label] / g[aria-label])
                const sliceEls = Array.from(targetVc.querySelectorAll('path[aria-label], g[aria-label]'));
                const sliceData = [];
                for (const s of sliceEls) {{
                    const aria = (s.getAttribute('aria-label') || '').trim();
                    if (!aria || aria.length < 5) continue;
                    const clean = aria.replace(/\\.$/, '');
                    const parts = clean.split('. ');
                    if (parts.length >= 2) {{
                        const cat = parts[0].trim();
                        const valStr = parts[parts.length - 1];
                        const numMatch = valStr.match(/([\\d,]+(?:\\.\\d+)?)/);
                        if (numMatch) {{
                            const val = parseFloat(numMatch[1].replace(/,/g, ''));
                            sliceData.push({{ category: cat, val: val }});
                        }}
                    }}
                }}

                if (sliceData.length > 0) {{
                    if (catFilters.length > 0) {{
                        let matchedSum = 0;
                        let found = false;
                        for (const sd of sliceData) {{
                            if (catFilters.some(cf => sd.category.toLowerCase().includes(cf) || cf.includes(sd.category.toLowerCase()))) {{
                                matchedSum += sd.val;
                                found = true;
                            }}
                        }}
                        if (found) return String(matchedSum);
                    }} else {{
                        const total = sliceData.reduce((a, b) => a + b.val, 0);
                        return String(total);
                    }}
                }}

                // 5. Case D: Table Visual gridcells
                const gridcells = Array.from(targetVc.querySelectorAll(\"[role='gridcell'], [class*='pivotTableCellWrap']\"));
                if (gridcells.length > 0) {{
                    for (const cell of gridcells) {{
                        const txt = cell.textContent.trim();
                        if (txt && /[\\d]/.test(txt)) return txt;
                    }}
                }}

                // 6. Case E: Fallback to any numeric string in targetVc
                const allText = targetVc.textContent || '';
                const numMatches = allText.match(/[\\$€£]?\\s*[\\d,]+(?:\\.\\d+)?\\s*[KkMmBb%]?/g);
                if (numMatches && numMatches.length > 0) {{
                    return numMatches[numMatches.length - 1].trim();
                }}

                return null;
            }}
        """

        try:
            raw = self.page.evaluate(eval_script)
            if raw:
                log.info(f"Extracted visual value for '{visual_target}': '{raw}'")
                return str(raw).strip()
        except Exception as e:
            log.warning(f"Error evaluating visual extraction for '{visual_target}': {e}")

        return None

    def has_slicer(self, slicer_name: str) -> bool:
        """Check if a slicer or filter matching slicer_name exists on the current page."""
        ctx = self._ctx()
        clean = slicer_name.lower().strip()

        # Common dimension aliases across Qlik and PBI models
        alias_map = {
            "geo level 3": ["geo level 3", "geo level3", "level 3", "level 3 region", "country[geo level3]", "geo", "market", "region", "country"],
            "geo level3": ["geo level 3", "geo level3", "level 3", "level 3 region", "country[geo level3]", "geo", "market", "region", "country"],
            "country[geo level3]": ["country[geo level3]", "geo level 3", "geo level3", "level 3", "geo", "market", "region", "country"],
            "geo": ["geo", "geo level3", "geo level 3", "market", "region", "country"],
            "date": ["date", "calendar", "calendar[date]", "calendar date", "yearmonth", "year month", "calendar yearmonth", "calendar[yearmonth]", "month", "period"],
            "calendar[date]": ["calendar[date]", "calendar date", "date", "calendar", "yearmonth", "year month", "calendar yearmonth", "calendar[yearmonth]", "month", "period"],
            "calendar yearmonth": ["calendar yearmonth", "calendar[yearmonth]", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"],
            "calendar[yearmonth]": ["calendar[yearmonth]", "calendar yearmonth", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"],
            "yearmonth": ["yearmonth", "year month", "calendar yearmonth", "calendar[yearmonth]", "calendar[date]", "date", "calendar"],
        }
        tokens = list(alias_map.get(clean, [clean]))
        for t in clean.replace("[", " ").replace("]", " ").split():
            clean_t = t.strip()
            # Do not add generic words that cause false positive containment
            if clean_t in ("geo", "level", "region", "country", "calendar", "period"):
                continue
            if len(clean_t) > 2 and clean_t not in tokens:
                tokens.append(clean_t)

        # 1. Check canvas slicers
        for tok in tokens:
            selectors = [
                f"visual-container:has([aria-roledescription*='slicer' i]:has-text('{tok}'))",
                f"visual-container:has([class*='slicer']:has-text('{tok}'))",
                f"[class*='visualContainer'][aria-roledescription*='slicer' i]:has-text('{tok}')",
                f".slicer-header:has-text('{tok}')",
                f".slicerContainer:has-text('{tok}')",
                f"[role='region'][aria-roledescription*='slicer' i][aria-label*='{tok}' i]",
            ]
            for sel in selectors:
                try:
                    cand = ctx.locator(sel).first
                    if cand.count() > 0 and cand.is_visible(timeout=500):
                        return True
                except Exception:
                    pass

        # 2. Check Filters Pane cards (both before and after selecting data visuals on canvas)
        try:
            for tok in tokens:
                card = self.page.locator(
                    f"[data-automation-type='filterCard']:has([data-testid='filter-card-title']:has-text('{tok}')), "
                    f"[data-automation-type='filterCard']:has(.textLabel:has-text('{tok}')), "
                    f"[data-automation-type='filterCard']:has([aria-label*='{tok}' i])"
                ).first
                if card.count() > 0:
                    return True

            if self.select_visual(None):
                for tok in tokens:
                    card = self.page.locator(
                        f"[data-automation-type='filterCard']:has([data-testid='filter-card-title']:has-text('{tok}')), "
                        f"[data-automation-type='filterCard']:has(.textLabel:has-text('{tok}')), "
                        f"[data-automation-type='filterCard']:has([aria-label*='{tok}' i])"
                    ).first
                    if card.count() > 0:
                        return True
        except Exception:
            pass

        return False


    def apply_slicer(
        self,
        slicer_name: str,
        value: str,
        raise_on_error: bool = False,
        target_visual: Optional[str] = None,
    ) -> bool:
        """
        Apply a slicer on the current PBI page with robust alias matching and dropdown handling.

        Finds the slicer by visual title, aria-label, or alias tokens, interacts with dropdowns/lists,
        and selects the specified values. Falls back to the collapsible Filters Pane (including
        visual-level filters on the selected canvas visual).
        """
        log.info(f"Applying PBI slicer: {slicer_name} = {value} (target_visual='{target_visual}')")
        ctx = self._ctx()

        clean = slicer_name.lower().strip()
        alias_map = {
            "geo level 3": [
                "country[geo level3]", "geo level 3", "geo level3", "level 3", "level3",
                "geo level_3", "level_3", "geo level 3 region", "market", "region", "geo", "country"
            ],
            "geo level3": [
                "country[geo level3]", "geo level 3", "geo level3", "level 3", "level3",
                "geo level_3", "level_3", "geo level 3 region", "market", "region", "geo", "country"
            ],
            "country[geo level3]": [
                "country[geo level3]", "geo level 3", "geo level3", "level 3", "level3",
                "geo level_3", "level_3", "geo level 3 region", "market", "region", "geo", "country"
            ],
            "geo level 1": [
                "country[geo level1]", "geo level 1", "geo level1", "level 1", "level1", "geo level_1", "level_1"
            ],
            "geo level1": [
                "country[geo level1]", "geo level 1", "geo level1", "level 1", "level1", "geo level_1", "level_1"
            ],
            "calendar yearmonth": [
                "calendar[calendaryearmonth]", "calendar[yearmonth]", "calendar yearmonth",
                "calendar year month", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"
            ],
            "calendar[yearmonth]": [
                "calendar[calendaryearmonth]", "calendar[yearmonth]", "calendar yearmonth",
                "calendar year month", "yearmonth", "year month", "calendar[date]", "calendar date", "date", "calendar"
            ],
            "calendar[date]": [
                "calendar[date]", "calendar date", "calendar[calendaryearmonth]", "calendar[yearmonth]",
                "calendar yearmonth", "calendar year month", "yearmonth", "year month", "date", "calendar"
            ],
            "date": [
                "date", "calendar[date]", "calendar date", "calendar[calendaryearmonth]", "calendar[yearmonth]",
                "calendar yearmonth", "calendar year month", "yearmonth", "year month", "calendar"
            ],
            "yearmonth": [
                "yearmonth", "year month", "calendar[calendaryearmonth]", "calendar[yearmonth]",
                "calendar yearmonth", "calendar date", "calendar[date]", "date", "calendar"
            ],
        }
        tokens = list(alias_map.get(clean, [clean]))
        for t in clean.replace("[", " ").replace("]", " ").split():
            clean_t = t.strip()
            # If slicer specifies a level, do not add generic 'geo' or 'level' as a standalone token
            if ("level 3" in clean or "level3" in clean or "level 1" in clean or "level1" in clean) and clean_t in ("geo", "level"):
                continue
            if len(clean_t) > 2 and clean_t not in tokens:
                tokens.append(clean_t)

        slicer_region = None
        for tok in tokens:
            candidate = ctx.locator(
                f"[role='region'][aria-label*='{tok}' i], "
                f"visual-container:has([class*='visualTitle']:has-text('{tok}')), "
                f"visual-container:has(h3:has-text('{tok}')), "
                f"visual-container:has([class*='slicer']:has-text('{tok}')), "
                f"visual-container[aria-roledescription*='slicer' i]:has-text('{tok}'), "
                f"visual-container[aria-label*='{tok}' i], "
                f"[class*='slicer']:has-text('{tok}'), "
                f".slicer-header:has-text('{tok}'), "
                f".slicerContainer:has-text('{tok}')"
            ).first
            try:
                if candidate.count() > 0 and candidate.is_visible(timeout=1_000):
                    slicer_region = candidate
                    log.debug(f"Slicer '{slicer_name}' matched candidate token '{tok}'")
                    break
            except Exception:
                continue

        if not slicer_region or not slicer_region.count():
            # Check Filters Pane fallback before giving up
            if self._apply_filter_pane_slicer(slicer_name, value, tokens, target_visual=target_visual):
                return True
            msg = f"Slicer region '{slicer_name}' not found on current page or Filters Pane."
            log.warning(msg)
            if raise_on_error:
                raise RuntimeError(msg)
            return False

        try:
            # 1. Expand dropdown if this is a dropdown-style slicer
            dropdown_triggers = [
                "[class*='slicer-dropdown-menu']",
                "[class*='slicerDropdown']",
                "[class*='slicer-rest-of-header']",
                "[class*='dropdown-chevron']",
                "[class*='dropdownChevron']",
                "i[class*='chevron']",
                "i[class*='dropdown']",
                "[aria-haspopup='true']",
                "button[class*='slicer']",
            ]
            expanded = False
            for trig_sel in dropdown_triggers:
                trig = slicer_region.locator(trig_sel).first
                try:
                    if trig.count() > 0 and trig.is_visible(timeout=1_000):
                        trig.click(timeout=3_000)
                        self.page.wait_for_timeout(500)
                        expanded = True
                        break
                except Exception:
                    pass

            if not expanded:
                # Try clicking the slicer region directly
                try:
                    slicer_region.click(timeout=3_000)
                    self.page.wait_for_timeout(400)
                except Exception:
                    pass

            # 2. Select each target value
            vals = [v.strip() for v in str(value).split(",") if v.strip()]
            any_clicked = False
            for single_val in vals:
                # Check for search input in slicer
                search_box = slicer_region.locator("input[type='text'], input[type='search'], [class*='searchInput']").first
                if search_box.count() > 0:
                    try:
                        search_box.fill(single_val)
                        self.page.wait_for_timeout(400)
                    except Exception:
                        pass

                # Locate value element in popup or container
                val_selectors = [
                    f"[role='option']:has-text('{single_val}')",
                    f"[class*='slicerText']:has-text('{single_val}')",
                    f"[aria-label='{single_val}']",
                    f"[title='{single_val}']",
                    f"span:text-is('{single_val}')",
                    f":text-is('{single_val}')",
                    f"div:has-text('{single_val}')",
                ]
                clicked = False
                for vsel in val_selectors:
                    item_el = ctx.locator(vsel).first
                    try:
                        if item_el.count() > 0 and item_el.is_visible(timeout=1_500):
                            item_el.click(timeout=3_000)
                            self.page.wait_for_timeout(400)
                            clicked = True
                            any_clicked = True
                            break
                    except Exception:
                        continue

                if not clicked:
                    log.warning(f"Could not click slicer item '{single_val}' inside '{slicer_name}'")

            # 3. Close popup if open
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

            if not any_clicked:
                log.info(f"Canvas slicer items not matched for '{slicer_name}'; attempting Filters Pane fallback")
                if self._apply_filter_pane_slicer(slicer_name, value, tokens, target_visual=target_visual):
                    return True

            self.page.wait_for_timeout(QLIK_FILTER_WAIT)
            log.info(f"PBI slicer applied: {slicer_name} = {value}")
            return any_clicked

        except Exception as e:
            self.capture_screenshot("pbi_slicer_fail")
            msg = f"Could not apply PBI slicer '{slicer_name}' = '{value}': {e}"
            log.warning(msg)
            if raise_on_error:
                raise RuntimeError(msg)
            return False

    def _apply_filter_pane_slicer(
        self,
        slicer_name: str,
        value: str,
        tokens: list[str],
        target_visual: Optional[str] = None,
    ) -> bool:
        """
        Apply filter via Power BI's collapsible Filters Pane when not found on canvas.
        First locates and clicks the target visual (or fallback data visual) on the canvas
        to activate its 'Filters on this visual' section in the Filters Pane.
        Then searches the Filters Pane, matches the exact filter card (preventing collisions
        such as GEO Level1 vs GEO Level3), expands it, searches and checks target values,
        and allows the visual to update.
        """
        log.info(f"Looking for '{slicer_name}' in Power BI Filters Pane (target_visual='{target_visual}')")
        ctx = self._ctx()
        try:
            # 1. Expand Filters Pane container if present and collapsed
            expand_sel = (
                "article.outspacePane button[aria-label*='Show/hide' i], "
                "article.outspacePane .collapseIcon, "
                "button[aria-label*='Expand Filters' i], "
                "button[title*='Expand Filters' i], "
                "button[aria-label*='Show/hide filter pane' i], "
                ".filterPaneToggleBtn, "
                "button.pbi-glyph-chevronright, "
                "button.pbi-glyph-doublechevronright"
            )
            expand_btn = ctx.locator(expand_sel).first if ctx.locator(expand_sel).count() > 0 else self.page.locator(expand_sel).first
            if expand_btn.count() > 0 and expand_btn.is_visible(timeout=800):
                aria_exp = expand_btn.get_attribute("aria-expanded")
                if aria_exp == "false":
                    expand_btn.click(timeout=2_000)
                    self.page.wait_for_timeout(400)

            top_search_sel = (
                "input[data-testid='search-bar-input'], "
                "article.outspacePane input[data-testid='search-bar-input'], "
                "article.outspacePane input[aria-label*='Search Filters' i], "
                ".filterPaneModern input[placeholder*='Search' i], "
                "mat-form-field.searchBox input, "
                "search-box-modern input"
            )
            top_search = ctx.locator(top_search_sel).first if ctx.locator(top_search_sel).count() > 0 else self.page.locator(top_search_sel).first

            used_top_search = False

            def _clear_top_search() -> None:
                nonlocal used_top_search
                if top_search.count() > 0 and top_search.is_visible(timeout=500):
                    try:
                        top_search.fill("")
                        self.page.wait_for_timeout(100)
                        top_search.press("Escape")
                        self.page.wait_for_timeout(200)
                    except Exception:
                        pass
                used_top_search = False

            def _search_pane(term: str) -> None:
                if top_search.count() > 0 and top_search.is_visible(timeout=500):
                    try:
                        top_search.click(timeout=1_000)
                        top_search.fill("")
                        if term:
                            top_search.fill(term)
                        self.page.wait_for_timeout(400)
                    except Exception:
                        pass

            def _card_matches(card: Any, target_name: str) -> bool:
                try:
                    t_el = card.locator("[data-testid='filter-card-title'], .textLabel, .title").first
                    t_text = (t_el.inner_text(timeout=300) if t_el.count() > 0 else "").strip().lower()
                    aria = (card.get_attribute("aria-label") or "").strip().lower()
                    title_attr = (card.get_attribute("title") or "").strip().lower()
                    combined = f"{t_text} {aria} {title_attr}"

                    t_clean = target_name.strip().lower().replace("[", " ").replace("]", " ")

                    # 1. Level 3 vs Level 1 disambiguation
                    if "level 3" in t_clean or "level3" in t_clean:
                        # Direct title match for level 3
                        if "level 3" in t_text or "level3" in t_text:
                            return True
                        if ("level 1" in t_text or "level1" in t_text or "level 2" in t_text or "level2" in t_text):
                            return False
                        return ("level 3" in combined or "level3" in combined or "geo level3" in combined or "geo level 3" in combined)

                    if "level 1" in t_clean or "level1" in t_clean:
                        # Direct title match for level 1
                        if "level 1" in t_text or "level1" in t_text:
                            return True
                        if ("level 3" in t_text or "level3" in t_text or "level 2" in t_text or "level2" in t_text):
                            return False
                        return ("level 1" in combined or "level1" in combined or "geo level1" in combined or "geo level 1" in combined)

                    # 2. Calendar / YearMonth / Date matching:
                    # In Qlik to PBI migration, Calendar[Date] or Date maps to YearMonth or Calendar YearMonth
                    is_date_field = any(k in t_clean for k in ["date", "calendar", "yearmonth", "year month"]) or bool(re.search(r"\d{4}-\d{2}", str(value)))
                    if is_date_field:
                        if "fiscal" in combined and "calendar" not in combined:
                            return False
                        date_keywords = [
                            "calendar yearmonth", "calendar[yearmonth]", "calendar year month",
                            "yearmonth", "year month", "calendaryearmonth",
                            "calendar date", "calendar[date]", "date", "calendar"
                        ]
                        if any(k in t_text for k in date_keywords):
                            return True
                        if any(k in combined for k in date_keywords):
                            return True

                    # 3. Check against caller tokens
                    for tok in tokens:
                        tok_lower = tok.lower()
                        if tok_lower in t_text or tok_lower in combined:
                            return True

                    return t_clean in combined
                except Exception:
                    return False

            def _find_card() -> Optional[Any]:
                card_sel = "[data-automation-type='filterCard'], filter:has([data-testid='filter-card-title']), .card.categorical"
                cards = ctx.locator(card_sel).all() if ctx.locator(card_sel).count() > 0 else self.page.locator(card_sel).all()
                for c in cards:
                    try:
                        if _card_matches(c, slicer_name):
                            try:
                                c.scroll_into_view_if_needed(timeout=1_000)
                            except Exception:
                                pass
                            t_el = c.locator("[data-testid='filter-card-title'], .textLabel, .title").first
                            matched_title = t_el.inner_text(timeout=300) if t_el.count() > 0 else ""
                            log.info(f"Matched filter card in Filters Pane: '{matched_title}' for '{slicer_name}'")
                            return c
                    except Exception:
                        continue
                return None

            try:
                # 2. Check directly in Filters Pane first (page-level filters may already be visible)
                filter_card = _find_card()

                # If not found directly, activate target visual (or fallback primary visual) to reveal visual-level filters
                if not filter_card:
                    if target_visual:
                        log.info(f"Activating target visual '{target_visual}' on canvas to reveal visual-level filters")
                        self.select_visual(target_visual)
                    else:
                        log.info("Activating canvas primary data visual to reveal visual-level filters")
                        self.select_visual(None)
                    filter_card = _find_card()

                # 4. If not directly visible, search the Filters Pane top search bar
                if not filter_card:
                    s_lower = slicer_name.lower()
                    search_candidates = []
                    if "geo" in s_lower:
                        search_candidates = ["GEO Level3" if "3" in s_lower else "GEO Level1", "GEO"]
                    elif "calendar" in s_lower or "date" in s_lower or "yearmonth" in s_lower or re.search(r"\d{4}-\d{2}", str(value)):
                        search_candidates = ["YearMonth", "Calendar", "Date"]
                    else:
                        search_candidates = [slicer_name]

                    for term in search_candidates:
                        log.info(f"Searching '{term}' in Filters Pane top search bar")
                        _search_pane(term)
                        used_top_search = True
                        filter_card = _find_card()
                        if filter_card:
                            break

                # Clear top search bar if probed initial visual without finding card
                if used_top_search and not filter_card:
                    _clear_top_search()

                # 6. If still not found, probe other canvas data visuals (tables, charts)
                if not filter_card:
                    log.info("Filter card not yet found on current visual; probing other canvas data visuals")
                    data_vis_sel = (
                        "visual-container:has([aria-roledescription*='table' i]), "
                        "visual-container:has([class*='tablix']), "
                        "visual-container:has([role='grid']), "
                        "visual-container:has([aria-roledescription*='chart' i]), "
                        "visual-container:has(svg)"
                    )
                    data_visual_candidates = ctx.locator(data_vis_sel).all() if ctx.locator(data_vis_sel).count() > 0 else self.page.locator(data_vis_sel).all()

                    for cand_vc in data_visual_candidates:
                        try:
                            cand_vc.scroll_into_view_if_needed()
                            header = cand_vc.locator("[class*='visualTitle'], h3, [class*='header-title'], .visualHeader").first
                            if header.count() > 0 and header.is_visible(timeout=500):
                                header.click(timeout=1_500, force=True)
                            else:
                                cand_vc.click(position={"x": 15, "y": 15}, timeout=1_500, force=True)
                            self.page.wait_for_timeout(400)

                            s_lower = slicer_name.lower()
                            search_term = (
                                "YearMonth" if ("calendar" in s_lower or "yearmonth" in s_lower)
                                else ("GEO" if "geo" in s_lower else slicer_name)
                            )
                            _search_pane(search_term)
                            used_top_search = True
                            filter_card = _find_card()
                            if filter_card:
                                log.info("Discovered filter card after selecting canvas data visual")
                                break
                            _clear_top_search()
                        except Exception:
                            _clear_top_search()
                            continue

                if not filter_card:
                    _clear_top_search()
                    log.debug(f"No filter card matching '{slicer_name}' found in Filters Pane")
                    return False

                filter_card.scroll_into_view_if_needed()

                # 7. Expand card if collapsed (wait for content to be visible; no redundant re-collapse click)
                collapse_btn = filter_card.locator(
                    "button.collapse, button[aria-label*='Expand or collapse' i], button[aria-label*='expand' i], button.pbi-glyph-chevronright"
                ).first
                if collapse_btn.count() > 0:
                    is_expanded = collapse_btn.get_attribute("aria-expanded") == "true"
                    if not is_expanded:
                        collapse_btn.click(timeout=2_000, force=True)
                        try:
                            filter_card.locator(".filterContent, .categoricalFilterValues, filter-visual").first.wait_for(
                                state="visible", timeout=1_500
                            )
                        except Exception:
                            self.page.wait_for_timeout(400)
                else:
                    try:
                        filter_card.locator(".filterContent, .categoricalFilterValues, filter-visual").first.wait_for(
                            state="visible", timeout=1_500
                        )
                    except Exception:
                        pass

                # 8. Apply target values to checkboxes
                vals = [v.strip() for v in str(value).split(",") if v.strip()]
                any_clicked = False

                cb_selectors_template = [
                    lambda v: f".row:has([title='{v}']) [role='checkbox']",
                    lambda v: f".row:has-text('{v}') [role='checkbox']",
                    lambda v: f"[role='checkbox']:has-text('{v}')",
                    lambda v: f".slicerItemContainer:has-text('{v}') [role='checkbox']",
                    lambda v: f".categoricalFilterValues [role='checkbox']:has-text('{v}')",
                    lambda v: f"span.textLabel:has-text('{v}')",
                    lambda v: f"span.slicerText:has-text('{v}')",
                    lambda v: f"[role='option']:has-text('{v}')",
                    lambda v: f":text-is('{v}')",
                ]

                for single_val in vals:
                    clicked = False

                    # Check if already visible in card without searching
                    for fn in cb_selectors_template:
                        cb = filter_card.locator(fn(single_val)).first
                        if cb.count() > 0 and cb.is_visible(timeout=400):
                            chk_el = None
                            if cb.get_attribute("role") == "checkbox":
                                chk_el = cb
                            elif cb.locator("[role='checkbox']").count() > 0:
                                chk_el = cb.locator("[role='checkbox']").first
                            else:
                                row_parent = cb.locator("xpath=ancestor::*[contains(@class, 'row') or contains(@class, 'slicerItemContainer') or @role='option'][1]")
                                if row_parent.count() > 0 and row_parent.locator("[role='checkbox']").count() > 0:
                                    chk_el = row_parent.locator("[role='checkbox']").first

                            aria_checked = chk_el.get_attribute("aria-checked") if (chk_el and chk_el.count() > 0) else cb.get_attribute("aria-checked")
                            if aria_checked != "true":
                                click_target = chk_el if (chk_el and chk_el.count() > 0 and chk_el.is_visible(timeout=200)) else cb
                                click_target.click(timeout=2_000, force=True)
                                self.page.wait_for_timeout(300)
                                log.info(f"Selected checkbox '{single_val}' in Filters Pane")
                            else:
                                log.info(f"Checkbox '{single_val}' already selected")
                            clicked = True
                            any_clicked = True
                            break

                    # If not visible directly, search inside filter card
                    if not clicked:
                        search_input = filter_card.locator(
                            "input.searchInput, input[data-testid='filter-search-input'], input[type='search'], input[type='text'], input[placeholder*='Search' i]"
                        ).first
                        if search_input.count() > 0 and search_input.is_visible(timeout=600):
                            try:
                                search_input.click(timeout=1_000)
                                search_input.fill("")
                                search_input.fill(single_val)
                                search_input.press("Enter")
                                self.page.wait_for_timeout(600)
                            except Exception:
                                pass

                            for fn in cb_selectors_template:
                                cb = filter_card.locator(fn(single_val)).first
                                if cb.count() > 0 and cb.is_visible(timeout=2_500):
                                    chk_el = None
                                    if cb.get_attribute("role") == "checkbox":
                                        chk_el = cb
                                    elif cb.locator("[role='checkbox']").count() > 0:
                                        chk_el = cb.locator("[role='checkbox']").first
                                    else:
                                        row_parent = cb.locator("xpath=ancestor::*[contains(@class, 'row') or contains(@class, 'slicerItemContainer') or @role='option'][1]")
                                        if row_parent.count() > 0 and row_parent.locator("[role='checkbox']").count() > 0:
                                            chk_el = row_parent.locator("[role='checkbox']").first

                                    aria_checked = chk_el.get_attribute("aria-checked") if (chk_el and chk_el.count() > 0) else cb.get_attribute("aria-checked")
                                    if aria_checked != "true":
                                        click_target = chk_el if (chk_el and chk_el.count() > 0 and chk_el.is_visible(timeout=200)) else cb
                                        click_target.click(timeout=2_000, force=True)
                                        self.page.wait_for_timeout(300)
                                        log.info(f"Selected checkbox '{single_val}' in Filters Pane (via search)")
                                    else:
                                        log.info(f"Checkbox '{single_val}' already selected")
                                    clicked = True
                                    any_clicked = True
                                    break

                            try:
                                search_input.fill("")
                                self.page.wait_for_timeout(200)
                            except Exception:
                                pass

                    if not clicked:
                        log.warning(f"Could not locate checkbox for '{single_val}' in Filters Pane")

                # 9. Clean up top search bar unconditionally
                _clear_top_search()

                if any_clicked:
                    self.page.wait_for_timeout(QLIK_FILTER_WAIT)
                    log.info(f"Filters Pane filter applied: {slicer_name} = {value}")
                    return True
                else:
                    log.warning(f"No values could be selected for '{slicer_name}' in Filters Pane")
                    return False

            finally:
                _clear_top_search()

        except Exception as e:
            log.warning(f"Error applying filter via Filters Pane: {e}")
            return False

    def clear_all_slicers(self) -> None:
        """
        Attempt to clear all slicers on the current PBI page.

        Tries 'Reset to default' button (.resetBtn, rightActionBarBtn, etc.)
        and accepts the confirmation modal if presented.
        """
        log.info("Clearing all PBI slicers")
        ctx = self._ctx()

        # Check if an existing confirmation dialog or backdrop is already hanging
        try:
            hanging_reset = self.page.locator(
                "mat-dialog-container button:has-text('Reset'), "
                "[role='dialog'] button:has-text('Reset'), "
                ".cdk-overlay-pane button:has-text('Reset')"
            ).first
            if hanging_reset.count() > 0 and hanging_reset.is_visible(timeout=500):
                hanging_reset.click(timeout=1_500)
                self.page.wait_for_timeout(1_000)
        except Exception:
            pass

        reset_selectors = [
            "button.resetBtn",
            "button:has(.pbi-glyph-reset)",
            "button[class*='resetBtn']",
            "[aria-label*='Reset to default']",
            "[title*='Reset to default']",
            "button:has-text('Reset')",
        ]
        for sel in reset_selectors:
            try:
                btn = ctx.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1_500):
                    aria = btn.get_attribute("aria-label") or ""
                    if "author's default view" in aria.lower():
                        log.info("Report is already in author's default view — no reset needed")
                        return
                    btn.click(timeout=3_000)
                    self.page.wait_for_timeout(1_000)

                    # Power BI pops up a confirmation dialog:
                    # "Reset to default: Do you want to reset filters, slicers, and other data view changes you've made? [Reset] [Cancel]"
                    try:
                        confirm_btn = self.page.locator(
                            "mat-dialog-container button:has-text('Reset'), "
                            "[role='dialog'] button:has-text('Reset'), "
                            ".cdk-overlay-pane button:has-text('Reset'), "
                            "button.mat-mdc-unelevated-button:has-text('Reset'), "
                            "div[class*='dialog'] button:has-text('Reset')"
                        ).first
                        if confirm_btn.count() > 0 and confirm_btn.is_visible(timeout=2_000):
                            confirm_btn.click(timeout=2_000)
                            log.info("Clicked confirmation 'Reset' in dialog")
                            self.page.wait_for_timeout(1_500)
                    except Exception as de:
                        log.debug(f"Confirmation dialog not encountered: {de}")

                    # Dismiss any remaining backdrop or dialog
                    try:
                        backdrop = self.page.locator(".cdk-overlay-backdrop")
                        if backdrop.count() > 0 and backdrop.is_visible(timeout=500):
                            self.page.keyboard.press("Escape")
                            self.page.wait_for_timeout(500)
                    except Exception:
                        pass

                    log.info("PBI slicers cleared via reset button")
                    return
            except Exception:
                pass

        log.debug("PBI reset button not active or not found")