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
        for sel in selectors:
            try:
                btn = ctx.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1_500):
                    btn.click(timeout=3_000)
                    self.page.wait_for_timeout(2_000)
                    log.info(f"Metric toggle '{target}' clicked successfully")
                    return True
            except Exception:
                continue
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

                // 1. Find matching visual container
                const vcs = Array.from(document.querySelectorAll('visual-container, [class*=\"visualContainer\"]'));
                let targetVc = null;

                for (const vc of vcs) {{
                    const titleEl = vc.querySelector(\"[class*='visualTitle'], h3, [class*='header-title']\");
                    const tText = (titleEl ? titleEl.textContent : '').toLowerCase().trim();
                    const aria = (vc.getAttribute('aria-label') || '').toLowerCase().trim();

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
                        const matchWords = targetWords.filter(w => fullText.includes(w));
                        if (matchWords.length === targetWords.length && targetWords.length > 0) {{
                            targetVc = vc;
                            break;
                        }}
                    }}
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
            "geo level 3": ["geo level 3", "geo level3", "level 3", "level 3 region", "geo", "market", "region", "country"],
            "geo": ["geo", "geo level3", "market", "region", "country"],
            "date": ["date", "calendar", "yearmonth", "month", "period"],
            "calendar[date]": ["date", "calendar", "yearmonth", "month"],
        }
        tokens = alias_map.get(clean, [clean])
        # Add individual alphanumeric words
        for t in clean.replace("[", " ").replace("]", " ").split():
            clean_t = t.strip()
            if len(clean_t) > 1 and clean_t not in tokens:
                tokens.append(clean_t)

        for tok in tokens:
            selectors = [
                f"[aria-label*='{tok}' i]",
                f"[title*='{tok}' i]",
                f"visual-container:has-text('{tok}')",
                f"[class*='slicer']:has-text('{tok}')",
                f".slicer-header:has-text('{tok}')",
            ]
            for sel in selectors:
                try:
                    if ctx.locator(sel).first.count() > 0:
                        return True
                except Exception:
                    pass

        titles = self.get_visual_titles()
        for t in titles:
            t_lower = t.lower()
            if any(tok in t_lower for tok in tokens):
                return True

        try:
            body_text = ctx.locator("body").inner_text(timeout=2_000).lower()
            if any(tok in body_text for tok in tokens):
                return True
        except Exception:
            pass

        return False


    def apply_slicer(self, slicer_name: str, value: str, raise_on_error: bool = False) -> bool:
        """
        Apply a slicer on the current PBI page with robust alias matching and dropdown handling.

        Finds the slicer by visual title, aria-label, or alias tokens, interacts with dropdowns/lists,
        and selects the specified values.
        """
        log.info(f"Applying PBI slicer: {slicer_name} = {value}")
        ctx = self._ctx()

        clean = slicer_name.lower().strip()
        alias_map = {
            "geo level 3": ["country[geo level3]", "geo level 3", "geo level3", "level 3", "level 3 region", "geo", "market", "region", "country"],
            "geo level3": ["country[geo level3]", "geo level 3", "geo level3", "level 3", "level 3 region", "geo", "market", "region", "country"],
            "country[geo level3]": ["country[geo level3]", "geo level 3", "geo level3", "level 3", "geo", "market", "region", "country"],
            "calendar yearmonth": ["calendar[yearmonth]", "calendar yearmonth", "yearmonth", "year month", "date", "calendar", "period"],
            "calendar[yearmonth]": ["calendar[yearmonth]", "calendar yearmonth", "yearmonth", "year month", "date", "calendar", "period"],
            "calendar[date]": ["calendar[date]", "date", "calendar", "yearmonth", "month"],
        }
        tokens = alias_map.get(clean, [clean])
        for t in clean.replace("[", " ").replace("]", " ").split():
            clean_t = t.strip()
            if len(clean_t) > 2 and clean_t not in tokens:
                tokens.append(clean_t)

        slicer_region = None
        for tok in tokens:
            candidate = ctx.locator(
                f"[role='region'][aria-label*='{tok}' i], "
                f"visual-container:has-text('{tok}'), "
                f"[class*='slicer']:has-text('{tok}'), "
                f".slicer-header:has-text('{tok}')"
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
            if self._apply_filter_pane_slicer(slicer_name, value, tokens):
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

            self.page.wait_for_timeout(QLIK_FILTER_WAIT)
            log.info(f"PBI slicer applied: {slicer_name} = {value}")
            return True

        except Exception as e:
            self.capture_screenshot("pbi_slicer_fail")
            msg = f"Could not apply PBI slicer '{slicer_name}' = '{value}': {e}"
            log.warning(msg)
            if raise_on_error:
                raise RuntimeError(msg)
            return False

    def _apply_filter_pane_slicer(self, slicer_name: str, value: str, tokens: list[str]) -> bool:
        """
        Apply filter via Power BI's collapsible Filters Pane when not found on canvas.
        Handles visual deselection, card expansion, search filtering, and checkbox selection.
        """
        log.info(f"Looking for '{slicer_name}' in Power BI Filters Pane")
        try:
            # 1. Deselect any active canvas visual so 'Filters on this page' is shown
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(200)
            try:
                self.page.evaluate("""() => {
                    const canvas = document.querySelector('.displayAreaContainer')
                                || document.querySelector('.exploreCanvas')
                                || document.querySelector('.canvasFlexBox');
                    if (canvas) {
                        canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    }
                }""")
                self.page.wait_for_timeout(300)
            except Exception:
                pass

            # 2. Check if Filters Pane container is present, expand if collapsed
            fp = self.page.locator(".filterPaneModern, [data-automation-type='filterPane'], section.filterPane-FlexWrapper, article.outspacePane").first
            if fp.count() == 0 or not fp.is_visible(timeout=1_000):
                expand_btn = self.page.locator(
                    "button[aria-label*='Expand Filters' i], "
                    "button[title*='Expand Filters' i], "
                    ".filterPaneToggleBtn, "
                    "button.pbi-glyph-chevronright"
                ).first
                if expand_btn.count() > 0 and expand_btn.is_visible(timeout=1_000):
                    expand_btn.click(timeout=2_000)
                    self.page.wait_for_timeout(500)

            # 3. Locate the filter card matching any of the tokens
            filter_card = None
            for tok in tokens:
                card = self.page.locator(
                    f"[data-automation-type='filterCard']:has([data-testid='filter-card-title']:has-text('{tok}')), "
                    f"[data-automation-type='filterCard']:has(.textLabel:has-text('{tok}')), "
                    f"[data-automation-type='filterCard']:has-text('{tok}')"
                ).first
                if card.count() > 0 and card.is_visible(timeout=600):
                    filter_card = card
                    log.info(f"Matched filter card in Filters Pane with token '{tok}'")
                    break

            # If not found immediately, try using the Filters Pane search box
            if not filter_card:
                top_search = self.page.locator(
                    "article.outspacePane input[aria-label*='Search Filters' i], "
                    ".filterPaneModern input[placeholder*='Search' i], "
                    "mat-form-field.searchBox input"
                ).first
                if top_search.count() > 0 and top_search.is_visible(timeout=500):
                    try:
                        top_search.fill(slicer_name)
                        self.page.wait_for_timeout(500)
                        card = self.page.locator("[data-automation-type='filterCard']").first
                        if card.count() > 0 and card.is_visible(timeout=600):
                            filter_card = card
                            log.info(f"Matched filter card via Filters Pane search box: '{slicer_name}'")
                    except Exception:
                        pass

            if not filter_card:
                log.debug(f"No filter card matching '{slicer_name}' found in Filters Pane")
                return False

            filter_card.scroll_into_view_if_needed()

            # 4. Expand card if collapsed
            collapse_btn = filter_card.locator("button.collapse, button[aria-label*='Expand or collapse' i]").first
            if collapse_btn.count() > 0:
                is_expanded = collapse_btn.get_attribute("aria-expanded") == "true"
                if not is_expanded:
                    collapse_btn.click(timeout=2_000)
                    self.page.wait_for_timeout(500)

            # 5. Apply target values to checkboxes
            vals = [v.strip() for v in str(value).split(",") if v.strip()]
            for single_val in vals:
                # Use search box inside filter card if available to filter down items
                search_input = filter_card.locator("input[type='search'], input[type='text'], input[placeholder*='Search' i]").first
                if search_input.count() > 0 and search_input.is_visible(timeout=500):
                    try:
                        search_input.fill("")
                        search_input.fill(single_val)
                        self.page.wait_for_timeout(400)
                    except Exception:
                        pass

                cb_selectors = [
                    f"[role='checkbox']:has-text('{single_val}')",
                    f".row:has-text('{single_val}') [role='checkbox']",
                    f"div:has-text('{single_val}') [role='checkbox']",
                    f".slicerItemContainer:has-text('{single_val}')",
                    f"span.slicerText:text-is('{single_val}')",
                    f"span:text-is('{single_val}')",
                    f":text-is('{single_val}')",
                ]
                clicked = False
                for cbs in cb_selectors:
                    cb = filter_card.locator(cbs).first
                    if cb.count() > 0 and cb.is_visible(timeout=1_000):
                        aria_checked = cb.get_attribute("aria-checked")
                        if aria_checked != "true":
                            cb.click(timeout=2_000)
                            self.page.wait_for_timeout(300)
                            log.info(f"Selected checkbox '{single_val}' in Filters Pane")
                        else:
                            log.info(f"Checkbox '{single_val}' already selected")
                        clicked = True
                        break

                if not clicked:
                    log.warning(f"Could not locate checkbox for '{single_val}' in Filters Pane")

            # 6. Settle dashboard
            self.page.wait_for_timeout(1_500)
            log.info(f"Filters Pane filter applied: {slicer_name} = {value}")
            return True

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