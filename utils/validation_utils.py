"""
validation_utils.py — Comparison utilities for Lenovo Qlik/PBI Validation.

Scenario 1 functions:
  detect_changed_visuals()  - which visuals changed between two snapshots
  compare_visual_impact()   - did the same visuals change on both platforms

Scenario 2 functions (to be added):
  parse_number()            - parse display strings like '580M' to float
  compare_single_value()    - compare one PBI value against CSV source
"""
from __future__ import annotations

import re
from typing import Optional

from utils.logger import get_logger

log = get_logger("validation_utils")


def parse_number(raw: str) -> Optional[float]:
    """Parse a display string like '580M', '33.8%', '2.49G' to float."""
    if not raw:
        return None
    s = str(raw).strip()
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = re.sub(r"[\u20ac\xa3$,%]", "", s).strip()
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "G": 1e9, "T": 1e12}
    scale = 1.0
    if s and s[-1].upper() in multipliers:
        scale = multipliers[s[-1].upper()]
        s = s[:-1]
    s = s.replace(",", "")
    try:
        value = float(s) * scale
        return -value if negative else value
    except ValueError:
        return None


def detect_changed_visuals(
    baseline: dict[str, str | None],
    current: dict[str, str | None],
) -> set[str]:
    """
    Return the set of visual titles whose values changed between two snapshots.
    A visual is "changed" if its value differs or if it appeared/disappeared.
    """
    changed = set()
    all_keys = set(baseline.keys()) | set(current.keys())
    for key in all_keys:
        b_val = baseline.get(key)
        c_val = current.get(key)
        if b_val != c_val:
            changed.add(key)
            log.debug(f"Changed: '{key}' | {b_val!r} -> {c_val!r}")
    return changed


def compare_visual_impact(
    changed_qlik: set[str],
    changed_pbi: set[str],
    test_id: str = "",
) -> tuple[bool, str]:
    """
    Compare which visuals changed on Qlik vs PBI after applying the same filter.

    Pass conditions:
    1. At least one visual changed on BOTH platforms.
    2. Every visual that changed on Qlik also changed on PBI.
       (PBI is allowed MORE changes — migration may add extra visuals.)
       (PBI is NOT allowed FEWER changes — that is a broken propagation.)

    Returns: (passed: bool, detail: str)
    """
    prefix = f"[{test_id}] " if test_id else ""

    if not changed_qlik:
        return False, (
            f"{prefix}FAIL — Filter had NO effect on Qlik. "
            "Check that the filter field name and value are correct."
        )

    if not changed_pbi:
        return False, (
            f"{prefix}FAIL — Filter had NO effect on PBI. "
            "Slicer may not be connected to any visuals on this page."
        )

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.lower().strip())

    norm_qlik = {_norm(t) for t in changed_qlik}
    norm_pbi  = {_norm(t) for t in changed_pbi}
    missing_in_pbi = norm_qlik - norm_pbi

    if missing_in_pbi:
        return False, (
            f"{prefix}FAIL — {len(missing_in_pbi)} Qlik visual(s) did NOT change on PBI: "
            f"{sorted(missing_in_pbi)}. "
            f"Qlik changed: {sorted(changed_qlik)}. PBI changed: {sorted(changed_pbi)}."
        )

    extra = norm_pbi - norm_qlik
    return True, (
        f"{prefix}PASS — {len(changed_qlik)} visual(s) changed on both. "
        f"{len(extra)} extra in PBI (expected for migration). "
        f"Qlik: {sorted(changed_qlik)}. PBI: {sorted(changed_pbi)}."
    )


def compare_single_value(
    pbi_raw: Any,
    source_value: Any,
    tolerance: float = 0.01,
    label: str = "",
) -> tuple[bool, str]:
    """
    Compare an extracted Power BI value against a ground-truth number (from CSV or DB).

    Tolerance: relative error (e.g. 0.01 = 1%).
    """
    prefix = f"[{label}] " if label else ""

    pbi_num = parse_number(str(pbi_raw)) if not isinstance(pbi_raw, (int, float)) else float(pbi_raw)
    try:
        src_num = float(source_value) if source_value is not None else None
    except (ValueError, TypeError):
        src_num = None

    if pbi_num is None:
        return False, f"{prefix}FAIL — Could not parse PBI display value: {pbi_raw!r}"

    if src_num is None:
        return False, f"{prefix}FAIL — Source value is None or not a number: {source_value!r}"

    if src_num == 0:
        match = abs(pbi_num) <= 1e-4
        diff_pct = 0.0 if match else 100.0
    else:
        diff_pct = abs(pbi_num - src_num) / abs(src_num) * 100.0
        match = (diff_pct / 100.0) <= tolerance

    status = "PASS" if match else "FAIL"
    detail = (
        f"{prefix}{status} — "
        f"PBI: {pbi_num:,.2f} ({pbi_raw}) | Source: {src_num:,.2f} | "
        f"Diff: {diff_pct:.2f}% (Tolerance: {tolerance * 100:.1f}%)"
    )
    return match, detail
