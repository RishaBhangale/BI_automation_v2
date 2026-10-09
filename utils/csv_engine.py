"""
csv_engine.py — Dynamic, config-driven aggregation engine for CSV source datasets.

Adapts automatically to different reports and schemas:
  - Reads data_source configuration from YAML (fact table, dimensions, join keys)
  - Dynamically joins dimensions onto the fact table without hardcoded columns
  - Flexible filter matching: supports arbitrary column filters, lists, and auto-detects column names
  - Configurable aggregation value column (defaults to config or detects numeric column)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from config.settings import SCENARIO2_CSV_DIR
from utils.logger import get_logger

log = get_logger("csv_engine")


class CSVEngine:
    """Config-driven in-memory query engine for tabular CSV datasets."""

    def __init__(
        self,
        config: Optional[dict] = None,
        csv_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.config = config or {}
        ds_cfg = self.config.get("data_source", {}) if self.config else {}

        # Resolve directory: explicit arg -> YAML config -> settings default
        configured_dir = ds_cfg.get("csv_dir") if ds_cfg else None
        target_dir = csv_dir or configured_dir or SCENARIO2_CSV_DIR
        self.csv_dir = Path(target_dir)

        self._fact_table_name = ds_cfg.get("fact_table") or "facts.csv"
        self._dimensions_config = ds_cfg.get("dimensions", [])
        self._value_col_name = ds_cfg.get("value_column", "# Value")

        self._df: Optional[pd.DataFrame] = None
        self._loaded: bool = False

    def load_data(self) -> None:
        """Load and pre-join fact table with dimension tables dynamically."""
        if self._loaded and self._df is not None:
            return

        if not self.csv_dir.exists():
            raise FileNotFoundError(f"CSV data directory not found: {self.csv_dir}")

        log.info(f"Loading CSV dataset from: {self.csv_dir}")

        # 1. Locate and read Fact table
        fact_path = self.csv_dir / self._fact_table_name
        if not fact_path.exists():
            # Fallback: search for any file containing 'fact' or use first CSV found
            csv_files = list(self.csv_dir.glob("*.csv"))
            fact_candidates = [f for f in csv_files if "fact" in f.name.lower()]
            if fact_candidates:
                fact_path = fact_candidates[0]
            elif csv_files:
                # pick largest file by size as likely fact table
                fact_path = max(csv_files, key=lambda f: f.stat().st_size)
            else:
                raise FileNotFoundError(f"No CSV files found in {self.csv_dir}")

        log.info(f"Loading fact table: {fact_path.name}")
        df = pd.read_csv(fact_path, low_memory=False)

        # 2. Join dimensions if configured, or auto-discover joinable dimensions
        dims_to_join = self._dimensions_config
        if not dims_to_join:
            # Auto-discovery: inspect other CSV files in the folder
            for f in self.csv_dir.glob("*.csv"):
                if f.name == fact_path.name:
                    continue
                # sample headers to find potential key matches
                try:
                    dim_sample = pd.read_csv(f, nrows=1)
                    common_keys = [k for k in dim_sample.columns if k in df.columns and "%" in k or "key" in k.lower() or "id" in k.lower()]
                    if common_keys:
                        dims_to_join.append({"file": f.name, "key": common_keys[0]})
                except Exception:
                    continue

        for dim in dims_to_join:
            dim_file = dim.get("file")
            join_key = dim.get("key")
            if not dim_file:
                continue

            dim_path = self.csv_dir / dim_file
            if not dim_path.exists():
                log.warning(f"Dimension file not found: {dim_path} — skipping")
                continue

            try:
                dim_df = pd.read_csv(dim_path, low_memory=False)
                if join_key and join_key in df.columns and join_key in dim_df.columns:
                    # Deduplicate dimension table by join key so fact rows are never multiplied
                    dim_df = dim_df.drop_duplicates(subset=[join_key])
                    # Drop duplicate attribute columns that already exist in df to prevent suffix clutter
                    dup_cols = [c for c in dim_df.columns if c in df.columns and c != join_key]
                    if dup_cols:
                        dim_df = dim_df.drop(columns=dup_cols)
                    df = df.merge(dim_df, on=join_key, how="left")
                    log.info(f"Joined dimension: {dim_file} on '{join_key}' ({len(dim_df):,} distinct keys)")
                elif not join_key:
                    # Find candidate key columns starting with % or containing id/key
                    candidate_keys = [
                        c for c in dim_df.columns
                        if c in df.columns and (c.startswith("%") or "id" in c.lower() or "key" in c.lower())
                    ]
                    if candidate_keys:
                        k = candidate_keys[0]
                        dim_df = dim_df.drop_duplicates(subset=[k])
                        dup_cols = [c for c in dim_df.columns if c in df.columns and c != k]
                        if dup_cols:
                            dim_df = dim_df.drop(columns=dup_cols)
                        df = df.merge(dim_df, on=k, how="left")
                        log.info(f"Joined dimension: {dim_file} on auto-detected key '{k}'")
            except Exception as e:
                log.warning(f"Could not join dimension {dim_file}: {e}")

        self._df = df
        self._loaded = True
        log.info(f"CSVEngine successfully loaded and joined {len(self._df):,} rows")

    @property
    def data(self) -> pd.DataFrame:
        if not self._loaded:
            self.load_data()
        return self._df

    def _resolve_column(self, candidate_name: str) -> Optional[str]:
        """Find matching column name in DataFrame (exact or case-insensitive)."""
        df = self.data
        if candidate_name in df.columns:
            return candidate_name
        cand_lower = candidate_name.lower().strip()
        for col in df.columns:
            if col.lower().strip() == cand_lower:
                return col
        # Substring search
        for col in df.columns:
            if cand_lower in col.lower().strip():
                return col
        return None

    def get_aggregated_sum(
        self,
        measure: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        value_column: Optional[str] = None,
        year_month: Optional[str] = None,
        geo_level3: Optional[Union[str, List[str]]] = None,
        past_due: Optional[bool] = None,
        seven_days_only: Optional[bool] = None,
        missing_pn_only: Optional[bool] = None,
        **extra_filters: Any,
    ) -> float:
        """
        Generic, config-driven aggregation method.

        Args:
            measure: Target measure name (filtered against 'Measure' column if present)
            filters: Dictionary mapping column names to expected values or lists of values
            value_column: The numeric column to sum (auto-detects if None)
            year_month: Optional convenience filter for fiscal/calendar period
            geo_level3: Optional convenience filter for region
            past_due: Optional convenience boolean filter for past due
            seven_days_only: Optional convenience boolean filter
            missing_pn_only: Optional convenience boolean filter
            extra_filters: Additional column=value keyword arguments
        """
        df = self.data
        mask = pd.Series(True, index=df.index)

        all_filters: Dict[str, Any] = {}
        if filters:
            all_filters.update(filters)
        if extra_filters:
            all_filters.update(extra_filters)

        # Handle Measure filter
        if measure:
            meas_col = self._resolve_column("Measure")
            if meas_col:
                mask &= df[meas_col].astype(str).str.lower() == str(measure).lower()

        # Handle YearMonth filter
        if year_month:
            ym_col = self._resolve_column("Calendar YearMonth") or self._resolve_column("YearMonth") or self._resolve_column("%Date")
            if ym_col:
                mask &= df[ym_col].astype(str).str.startswith(str(year_month))

        # Handle GEO filter
        if geo_level3:
            geo_col = self._resolve_column("GEO Level3") or self._resolve_column("Region") or self._resolve_column("Market")
            if geo_col:
                if isinstance(geo_level3, str):
                    geo_list = [g.strip().upper() for g in geo_level3.split(",") if g.strip()]
                else:
                    geo_list = [str(g).strip().upper() for g in geo_level3]
                mask &= df[geo_col].astype(str).str.upper().isin(geo_list)

        # Handle Past Due
        if past_due is True:
            pd_col = self._resolve_column("Past Due")
            if pd_col:
                mask &= df[pd_col] == -1

        # Handle 7 Days
        if seven_days_only is True:
            day_col = (
                self._resolve_column("Opportunity Closed Date - Opportunity Created Date")
                or self._resolve_column("Days To Close")
            )
            if day_col:
                mask &= df[day_col] <= 7

        # Handle Missing PN
        if missing_pn_only is True:
            pn_col = self._resolve_column("%Opportunity Product ID") or self._resolve_column("Product ID")
            if pn_col:
                mask &= df[pn_col].isna()

        # Process arbitrary dictionary filters
        for col_name, val in all_filters.items():
            if val is None:
                continue
            if col_name.lower().strip() in ["missing pn", "missing_pn"]:
                pn_col = self._resolve_column("%Opportunity Product ID") or self._resolve_column("Product ID")
                if pn_col:
                    mask &= df[pn_col].isna()
                continue
            resolved = self._resolve_column(col_name)
            if not resolved:
                log.warning(f"Filter column '{col_name}' not found in dataset — skipping filter")
                continue

            # Support comparison operators in string values (e.g. '<= 7', '> 100')
            str_val = str(val).strip()
            op_match = re.match(r"^([<>=!]+)\s*(.*)$", str_val)
            if op_match and not isinstance(val, (list, tuple, set)):
                op, op_str = op_match.groups()
                try:
                    num_val = float(op_str)
                    if op == "<=":
                        mask &= df[resolved] <= num_val
                        continue
                    elif op == ">=":
                        mask &= df[resolved] >= num_val
                        continue
                    elif op == "<":
                        mask &= df[resolved] < num_val
                        continue
                    elif op == ">":
                        mask &= df[resolved] > num_val
                        continue
                    elif op in ("!=", "<>"):
                        mask &= df[resolved] != num_val
                        continue
                except ValueError:
                    pass

            if str_val.lower() in ("isna", "nan", "null", "none"):
                mask &= df[resolved].isna()
                continue
            elif str_val.lower() in ("notna", "notnull"):
                mask &= df[resolved].notna()
                continue

            if isinstance(val, (list, tuple, set)):
                if pd.api.types.is_numeric_dtype(df[resolved]):
                    try:
                        num_list = [float(v) for v in val]
                        mask &= df[resolved].isin(num_list)
                    except (ValueError, TypeError):
                        val_list = [str(v).strip().lower() for v in val]
                        mask &= df[resolved].astype(str).str.strip().str.lower().isin(val_list)
                else:
                    val_list = [str(v).strip().lower() for v in val]
                    mask &= df[resolved].astype(str).str.strip().str.lower().isin(val_list)
            elif isinstance(val, bool):
                mask &= df[resolved] == (-1 if -1 in df[resolved].values else val)
            elif isinstance(val, (int, float)):
                mask &= df[resolved] == float(val)
            elif pd.api.types.is_numeric_dtype(df[resolved]):
                try:
                    mask &= df[resolved] == float(val)
                except (ValueError, TypeError):
                    mask &= df[resolved].astype(str).str.strip().str.lower() == str(val).strip().lower()
            else:
                mask &= df[resolved].astype(str).str.strip().str.lower() == str(val).strip().lower()

        # Determine target numeric value column
        target_val_col = value_column or self._value_col_name
        resolved_val_col = self._resolve_column(target_val_col)
        if not resolved_val_col:
            # Fallback to first numeric column
            numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
            if numeric_cols:
                resolved_val_col = numeric_cols[0]
            else:
                raise ValueError(f"No numeric value column found to aggregate in dataset")

        filtered = df[mask]
        total = float(filtered[resolved_val_col].sum())
        log.debug(
            f"CSVEngine aggregation: val_col='{resolved_val_col}', "
            f"rows={len(filtered)}/{len(df)} -> total={total:,.2f}"
        )
        return total


# Module singleton cache
_engine_cache: Dict[str, CSVEngine] = {}


def get_csv_engine(
    config: Optional[dict] = None,
    csv_dir: Optional[Union[str, Path]] = None,
) -> CSVEngine:
    """Return a cached CSVEngine instance configured for the requested directory/config."""
    key = str(csv_dir) if csv_dir else "default"
    if key not in _engine_cache:
        engine = CSVEngine(config=config, csv_dir=csv_dir)
        engine.load_data()
        _engine_cache[key] = engine
    return _engine_cache[key]
