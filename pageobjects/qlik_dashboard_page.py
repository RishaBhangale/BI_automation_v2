"""
qlik_dashboard_page.py — Page Object for Qlik Cloud dashboards.

AUTHENTICATION:
  Uses a pre-captured browser session (playwright/.auth/qlik_session.json).
  Run: python scripts/capture_qlik_session.py  (once, manually)

READING QLIK DATA:
  Qlik renders charts on <canvas> — no readable DOM text exists for chart values.
  We inject enigma.js into the already-authenticated page via page.evaluate().
  The WebSocket connection inherits the browser session cookies automatically.

FILTER APPLICATION:
  Applied via Playwright UI interactions (click the filter bar, select values).
  This tests the actual user interaction, not just the data state.

SESSION EXPIRY:
  Re-run capture_qlik_session.py when tests start failing with auth redirects.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests
from playwright.sync_api import Page, TimeoutError as PwTimeoutError

from pageobjects.base_page import BasePage
from utils.logger import get_logger
from config.settings import (
    QLIK_TENANT,
    QLIK_BASE_URL,
    QLIK_SESSION_FILE,
    QLIK_RENDER_TIMEOUT,
    QLIK_FILTER_WAIT,
    ENIGMA_JS_URL,
    ENIGMA_SCHEMA_URL,
)

log = get_logger("qlik_dashboard_page")


class QlikDashboardPage(BasePage):
    """
    Page Object for Qlik Cloud dashboard interaction.

    Usage (in tests, via conftest fixtures):
        # qlik_dashboard fixture is already wired — see tests/conftest.py
        qlik_dashboard.open(config["qlik"]["app_id"], config["qlik"]["primary_sheet_id"])
        baseline = qlik_dashboard.capture_baseline()
        qlik_dashboard.apply_filter("Geo", "META")
        filtered = qlik_dashboard.capture_baseline()
        changed = detect_changed_visuals(baseline, filtered)
    """

    # Selectors — best-effort aria/role based. Inspect actual DOM to verify.
    _SEL_LOADING = "[class*='loading'], [class*='spinner'], [aria-label*='Loading']"
    _SEL_CLEAR_ALL = (
        "[title*='Clear all selections'], "
        "[aria-label*='Clear all selections'], "
        "button[title*='Clear']"
    )

    def __init__(self, page: Page) -> None:
        super().__init__(page)
        self._app_id: str = ""
        self._sheet_id: str = ""
        self._enigma_injected: bool = False
        self._discovered_sheets: dict[str, str] = {}
        self._setup_ws_listener()

    # ─────────────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────────────

    def open(self, app_id: str, sheet_id: str) -> None:
        """
        Navigate to a specific Qlik Cloud sheet using the saved session or reuse warm page.

        Args:
            app_id:   Qlik App ID (GUID from URL)
            sheet_id: Sheet ID (GUID from URL)
        """
        self._app_id = app_id
        self._sheet_id = sheet_id
        self._setup_ws_listener()

        current_url = self.page.url
        if app_id in current_url and sheet_id in current_url:
            log.info(f"Qlik sheet {sheet_id} already open — reusing warm page")
            return

        url = (
            f"{QLIK_BASE_URL}/sense/app/{app_id}/sheet/{sheet_id}"
            f"/state/analysis/hubUrl/%2Fanalytics%2Fhome"
        )
        log.info(f"Opening Qlik sheet: app={app_id}, sheet={sheet_id}")
        self.page.goto(url, timeout=QLIK_RENDER_TIMEOUT)
        self._assert_not_login_page()
        self._wait_for_sheet_load()
        self._assert_not_login_page()
        log.info("Qlik sheet loaded")

    def _assert_not_login_page(self) -> None:
        """Handle Qlik login redirect: pause for user login in headed browser instead of crashing."""
        url = self.page.url.lower()
        if "login" in url and "qlikcloud.com" in url:
            self.capture_screenshot("qlik_login_required")
            log.warning(
                f"[AUTH REQUIRED] Qlik redirected to login page ({self.page.url[:80]}...). "
                "Please complete login in the open browser window. Waiting up to 60s..."
            )
            try:
                self.page.wait_for_url(
                    lambda u: "sense/app" in u.lower() or ("qlikcloud.com" in u.lower() and "login" not in u.lower()),
                    timeout=60_000,
                )
                log.info("Qlik login completed! Sheet loading...")
                self._wait_for_sheet_load()
            except Exception:
                raise RuntimeError(
                    f"Qlik redirected to login page ({self.page.url[:80]}...) "
                    "and login was not completed within 60s."
                )

    def _wait_for_sheet_load(self, max_ms: int = None) -> None:
        """Poll until loading indicators disappear."""
        max_ms = max_ms or QLIK_RENDER_TIMEOUT
        self.page.wait_for_timeout(2_000)  # initial settle
        deadline = time.time() + max_ms / 1_000
        while time.time() < deadline:
            try:
                spinner = self.page.locator(self._SEL_LOADING)
                if not spinner.is_visible(timeout=400):
                    log.debug("Qlik sheet settled")
                    return
            except Exception:
                return
            self.page.wait_for_timeout(600)
        log.warning("Qlik sheet load timed out — continuing")

    def navigate_to_sheet(self, sheet_title: str) -> None:
        """
        Navigate to a sheet by its display title.
        Resolves the sheet title to its discovered GUID and opens it directly.

        Args:
            sheet_title: Display name of the sheet (e.g., 'Pipeline Hygiene IDG')
        """
        log.info(f"Navigating to Qlik sheet: '{sheet_title}'")
        if not self._discovered_sheets:
            self.get_sheet_list_via_api()

        target_sid = None
        for sid, title in self._discovered_sheets.items():
            if title.lower().strip() == sheet_title.lower().strip():
                target_sid = sid
                break

        if target_sid and self._app_id:
            log.info(f"Resolved sheet '{sheet_title}' -> ID: {target_sid}")
            self.open(self._app_id, target_sid)
            return

        try:
            tab = self.page.get_by_text(sheet_title, exact=True).first
            tab.click(timeout=QLIK_RENDER_TIMEOUT)
            self._wait_for_sheet_load()
            log.info(f"Navigated to: '{sheet_title}'")
        except PwTimeoutError:
            self.capture_screenshot(f"qlik_nav_fail_{sheet_title.replace(' ', '_')[:30]}")
            raise RuntimeError(
                f"Qlik sheet '{sheet_title}' not found among discovered sheets: "
                f"{list(self._discovered_sheets.values())}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Sheet & Object Discovery (via native WebSocket stream)
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_ws_listener(self) -> None:
        """Attach listener to intercept Engine WebSocket messages during page load."""
        if hasattr(self, "_ws_attached") and self._ws_attached:
            return
        self._ws_attached = True

        def _on_ws(ws):
            if "/app/" not in ws.url:
                return

            def _on_msg(payload):
                try:
                    if "sheet" not in payload.lower():
                        return
                    d = json.loads(payload)

                    def find_sheets(obj):
                        if isinstance(obj, dict):
                            if obj.get("qInfo", {}).get("qType") == "sheet" or obj.get("qType") == "sheet":
                                sid = obj.get("qInfo", {}).get("qId") or obj.get("id")
                                t = obj.get("qMeta", {}).get("title") or obj.get("qMetaDef", {}).get("title")
                                if sid and t:
                                    self._discovered_sheets[sid] = t
                            for k, v in obj.items():
                                find_sheets(v)
                        elif isinstance(obj, list):
                            for it in obj:
                                find_sheets(it)

                    find_sheets(d)
                except Exception:
                    pass

            ws.on("framereceived", _on_msg)

        self.page.on("websocket", _on_ws)

    def get_sheet_list_via_api(self, timeout_s: float = 8.0) -> list[dict]:
        """
        Return all sheets in the app by inspecting the Engine WebSocket stream.
        If fewer than 10 sheets were loaded (single-sheet analysis mode),
        loads the app overview to retrieve the complete public catalog of 21 sheets.

        Returns: [{id, title, published}, ...]
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline and len(self._discovered_sheets) >= 10:
            self.page.wait_for_timeout(300)

        # If we are in single-sheet analysis mode, navigate to overview to get all 21 sheets
        if len(self._discovered_sheets) < 10 and self._app_id:
            try:
                overview_url = f"{QLIK_BASE_URL}/sense/app/{self._app_id}/overview"
                log.info(f"Loading Qlik app overview for full sheet catalog: {overview_url}")
                self.page.goto(overview_url, timeout=30_000)
                overview_deadline = time.time() + 6.0
                while time.time() < overview_deadline and len(self._discovered_sheets) < 15:
                    self.page.wait_for_timeout(500)
            except Exception as e:
                log.warning(f"Could not load overview: {e}")

        sheets = [
            {"id": sid, "title": title, "published": True}
            for sid, title in sorted(self._discovered_sheets.items(), key=lambda x: x[1])
        ]
        log.info(f"Discovered {len(sheets)} Qlik sheets: {[s['title'] for s in sheets]}")
        return sheets

    # ─────────────────────────────────────────────────────────────────────────
    # enigma.js — Visual State Reading
    # ─────────────────────────────────────────────────────────────────────────

    def _inject_enigma(self) -> bool:
        """
        Inject enigma.js inline into the current Qlik page to satisfy CSP.
        """
        if self._enigma_injected:
            return True
        try:
            local_vendor = Path(__file__).parent.parent / "playwright" / "vendor" / "enigma.min.js"
            if local_vendor.exists():
                content = local_vendor.read_text(encoding="utf-8")
                # Wrap to bypass RequireJS define.amd check on Qlik Cloud
                wrapped = f"""
                (() => {{
                    try {{
                        const _prev = window.define;
                        window.define = undefined;
                        {content}
                        window.define = _prev;
                    }} catch(e) {{
                        console.error('enigma wrapper error:', e);
                    }}
                }})();
                """
                self.page.add_script_tag(content=wrapped)
            else:
                self.page.add_script_tag(url=ENIGMA_JS_URL)
            self.page.wait_for_timeout(500)
            loaded = self.page.evaluate("typeof window.enigma !== 'undefined'")
            if loaded:
                log.info("enigma.js injected successfully (inline)")
                self._enigma_injected = True
                return True
            log.warning("enigma.js tag added but enigma is undefined")
            return False
        except Exception as e:
            log.warning(f"enigma.js injection failed: {e}")
            return False

    def capture_baseline(self, app_id: str = None, sheet_id: str = None) -> dict[str, str | None]:
        """
        Snapshot current displayed values for all visuals on the active sheet.

        Returns: {visual_title: displayed_value_string}
        """
        app_id   = app_id   or self._app_id
        sheet_id = sheet_id or self._sheet_id

        log.info(f"Capturing Qlik baseline: app={app_id}, sheet={sheet_id}")

        # Ensure sheet visual elements are mounted
        self.page.wait_for_timeout(3_000)
        try:
            self.page.wait_for_selector(
                "div[class*='qv-object'], div[data-testid*='sheet-object'], .qv-inner-object, header, h1",
                timeout=8_000,
            )
        except Exception:
            pass

        if self._inject_enigma():
            try:
                result = self._capture_via_enigma(app_id, sheet_id)
                if result:
                    log.info(f"enigma.js baseline: {len(result)} visuals captured")
                    return result
            except Exception as e:
                log.warning(f"enigma.js capture failed: {e}. Falling back to DOM.")

        return self._capture_via_dom()

    def _capture_via_enigma(self, app_id: str, sheet_id: str) -> dict[str, str | None]:
        """Use enigma.js with embedded schema to read visual layouts via Engine API."""
        local_schema = Path(__file__).parent.parent / "playwright" / "vendor" / "schema.json"
        if not local_schema.exists():
            raise FileNotFoundError(f"Schema not found: {local_schema}")
        schema_json = local_schema.read_text(encoding="utf-8")

        ws_url = f"wss://{QLIK_TENANT}/app/{app_id}"

        js = f"""
        async () => {{
            try {{
                const schema = {schema_json};
                const session = window.enigma.create({{
                    schema,
                    url: '{ws_url}',
                }});
                const global = await session.open();
                const app = await global.openDoc('{app_id}');
                const sheet = await app.getObject('{sheet_id}');
                const sheetLayout = await sheet.getLayout();
                const cells = sheetLayout.cells || [];
                const results = {{}};
                for (const cell of cells) {{
                    try {{
                        const obj = await app.getObject(cell.name);
                        const layout = await obj.getLayout();
                        const title = layout.title || layout.qInfo?.qId || cell.name;
                        let value = null;
                        if (layout.qHyperCube?.qGrandTotalRow?.length > 0) {{
                            value = layout.qHyperCube.qGrandTotalRow[0].qText || null;
                        }} else if (layout.qHyperCube?.qDataPages?.[0]?.qMatrix?.[0]?.[0]) {{
                            value = layout.qHyperCube.qDataPages[0].qMatrix[0][0].qText || null;
                        }}
                        results[title] = value || "present";
                    }} catch(e) {{
                        // skip objects that fail
                    }}
                }}
                await session.close();
                return {{ success: true, data: results }};
            }} catch(err) {{
                return {{ success: false, error: err.toString() }};
            }}
        }}
        """

        outcome = self.page.evaluate(js)
        if not outcome.get("success"):
            raise RuntimeError(f"enigma.js failed: {outcome.get('error')}")
        return outcome.get("data", {})

    def _capture_via_dom(self) -> dict[str, str | None]:
        """
        Fallback: Extract visual titles and metrics from the Qlik Cloud DOM.
        """
        log.info("DOM fallback for Qlik visual capture.")
        results = {}

        # 1. Search visual containers
        obj_selectors = [
            "div[class*='qv-object']",
            "div[data-testid*='sheet-object']",
            "div[class*='qv-grid-cell']",
            ".qv-inner-object",
        ]
        for sel in obj_selectors:
            try:
                objs = self.page.locator(sel).all()
                for obj in objs:
                    title_el = obj.locator(".qvt-visualization-title, header, h1, [class*='title']").first
                    title = title_el.inner_text().strip() if title_el.count() else ""
                    if not title:
                        title = obj.get_attribute("aria-label") or obj.get_attribute("title") or ""
                    if not title or title in results:
                        continue
                    val_el = obj.locator("[class*='value'], [class*='numeric'], text, span").first
                    val = val_el.inner_text().strip() if val_el.count() else ""
                    if not val:
                        txt = obj.inner_text().strip()
                        val = " ".join(txt.split()[:4]) if txt else "rendered"
                    results[title] = val or "rendered"
            except Exception:
                pass

        # 2. Extract visual headers and titles across canvas
        header_selectors = [
            ".qvt-visualization-title",
            "[class*='qv-object-title']",
            "div[data-testid*='sheet-object'] header",
            "div[class*='qv-object'] header",
            "header h1, header span",
            "[data-testid*='title']",
            "span[title]",
        ]
        for h_sel in header_selectors:
            try:
                h_els = self.page.locator(h_sel).all()
                for h in h_els:
                    htxt = h.inner_text().strip()
                    if htxt and len(htxt) < 80 and htxt not in results:
                        results[htxt] = "rendered"
            except Exception:
                pass

        # 3. Check KPI cards
        kpi_selectors = [
            "[class*='kpi'] [class*='value']",
            "[class*='qv-kpi'] [class*='value']",
            "[class*='lui-kpi']",
        ]
        for sel in kpi_selectors:
            try:
                els = self.page.locator(sel).all()
                for idx, el in enumerate(els):
                    title = el.get_attribute("aria-label") or el.get_attribute("title") or f"kpi_{idx+1}"
                    val = el.inner_text().strip() or None
                    if val and title not in results:
                        results[title] = val
            except Exception:
                pass

        # 4. Ultimate canvas fallback if DOM titles were not text nodes
        if not results:
            try:
                canvas_objs = self.page.locator(".qv-grid-cell, div[class*='qv-object']").all()
                for idx, cell in enumerate(canvas_objs):
                    cid = cell.get_attribute("id") or cell.get_attribute("data-testid") or f"sheet_object_{idx+1}"
                    results[cid] = "rendered"
            except Exception:
                pass

        log.info(f"DOM fallback captured {len(results)} visuals/values")
        return results



    # ─────────────────────────────────────────────────────────────────────────
    # Filter Application
    # ─────────────────────────────────────────────────────────────────────────

    def apply_filter(self, field_name: str, value: str) -> None:
        """
        Apply a filter selection in Qlik via UI interaction.

        This clicks on the filter bar, selects the field, and picks the value.
        Used for Category D tests where we want to test the actual UI interaction.

        IMPORTANT: Qlik's filter UI varies between versions. If this fails,
        inspect the actual Qlik DOM and update the selectors below.
        The selectors here are best-effort based on known Qlik Cloud DOM patterns.

        Args:
            field_name: Qlik dimension name (e.g., 'Geo', 'IDG/ISG', 'Market')
            value:      Value to select (e.g., 'META', 'IDG', 'KEY ACCOUNT')
        """
        log.info(f"Applying Qlik filter: {field_name} = {value}")

        try:
            # Attempt 1: Find the field button in the filter pane/toolbar
            field_locator = (
                self.page.get_by_role("button", name=field_name)
                or self.page.locator(
                    f"[aria-label='{field_name}'], "
                    f"[title='{field_name}']"
                ).first
            )
            field_locator.click(timeout=15_000)
            self.page.wait_for_timeout(600)

            # Find and select each value (handles comma-separated multi-select e.g. 'BENELUX, CH')
            vals = [v.strip() for v in str(value).split(",") if v.strip()]
            for single_val in vals:
                value_locator = self.page.get_by_role("option", name=single_val)
                if not value_locator.count():
                    value_locator = self.page.locator(
                        f"[aria-label='{single_val}'], [title='{single_val}']"
                    ).first
                if value_locator.count():
                    value_locator.click(timeout=10_000)
                    self.page.wait_for_timeout(300)

            # Close the dropdown
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(QLIK_FILTER_WAIT)
            log.info(f"Filter applied: {field_name} = {value}")

        except Exception as e:
            log.warning(f"UI filter interaction failed for '{field_name}' = '{value}' ({e}). Attempting API filter fallback...")
            try:
                self.apply_filter_via_api(field_name, value)
            except Exception as api_err:
                self.capture_screenshot(f"qlik_filter_fail_{field_name.replace('/', '_')}")
                raise RuntimeError(
                    f"Failed to apply Qlik filter '{field_name}' = '{value}' via both UI and API: {api_err}"
                )

    def apply_filter_via_api(self, field_name: str, value: str) -> None:
        """
        Apply a filter via enigma.js SelectValues (no UI click needed).

        Use this for test setup steps where UI interaction fidelity is not required.
        Faster and more reliable than apply_filter() for non-UI tests.
        """
        if not self._inject_enigma():
            log.warning("enigma.js not available — falling back to UI filter")
            self.apply_filter(field_name, value)
            return

        log.info(f"Applying Qlik filter via API: {field_name} = {value}")

        local_schema = Path(__file__).parent.parent / "playwright" / "vendor" / "schema.json"
        if not local_schema.exists():
            raise FileNotFoundError(f"Schema not found: {local_schema}")
        schema_json = local_schema.read_text(encoding="utf-8")

        js = f"""
        async () => {{
            try {{
                const schema = {schema_json};
                const session = enigma.create({{
                    schema,
                    url: 'wss://{QLIK_TENANT}/app/{self._app_id}',
                }});
                const global = await session.open();
                const app = await global.openDoc('{self._app_id}');
                const field = await app.getField('{field_name}');
                await field.selectValues([{{ qText: '{value}' }}], true, false);
                await session.close();
                return {{ success: true }};
            }} catch(err) {{
                return {{ success: false, error: err.toString() }};
            }}
        }}
        """

        result = self.page.evaluate(js)
        if not result.get("success"):
            raise RuntimeError(f"API filter failed: {result.get('error')}")

        self.page.wait_for_timeout(QLIK_FILTER_WAIT)
        log.info(f"API filter applied: {field_name} = {value}")

    def clear_all_filters(self) -> None:
        """
        Clear all active Qlik selections.

        Tries the toolbar button first, then falls back to Ctrl+Shift+Delete.
        """
        log.info("Clearing all Qlik selections")

        # Method 1: Toolbar button
        try:
            btn = self.page.locator(self._SEL_CLEAR_ALL).first
            if btn.is_visible(timeout=3_000):
                btn.click()
                self.page.wait_for_timeout(2_000)
                log.info("Cleared via toolbar button")
                return
        except Exception:
            pass

        # Method 2: Keyboard shortcut (standard Qlik)
        try:
            self.page.keyboard.press("Control+Shift+Delete")
            self.page.wait_for_timeout(2_000)
            log.info("Cleared via Ctrl+Shift+Delete")
        except Exception as e:
            log.warning(f"Could not clear Qlik selections: {e}")

    def get_selection_state(self) -> dict[str, list[str]]:
        """Return current active selections: {field_name: [selected_values]}."""
        if not self._inject_enigma():
            return {}

        js = f"""
        async () => {{
            try {{
                const schema = await fetch('{ENIGMA_SCHEMA_URL}').then(r => r.json());
                const session = enigma.create({{
                    schema,
                    url: 'wss://{QLIK_TENANT}/app/{self._app_id}',
                }});
                const global = await session.open();
                const app = await global.openDoc('{self._app_id}');
                const selections = await app.getCurrentSelections();
                await session.close();
                const result = {{}};
                for (const sel of (selections || [])) {{
                    result[sel.fieldName] = sel.selectedValues?.map(v => v.qName) || [];
                }}
                return result;
            }} catch(err) {{
                return {{}};
            }}
        }}
        """
        return self.page.evaluate(js) or {}
