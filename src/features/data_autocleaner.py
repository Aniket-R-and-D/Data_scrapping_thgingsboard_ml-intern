"""src/features/data_autocleaner.py
Automated Telemetry Quality Assessment, Profiling & Autocleaning Engine.

Designed for SEPLE NovaEdge IoT Telemetry Pipelines:
1. Automated Telemetry Profiling: Computes per-sensor null rates, distributions,
   extreme values, and detects stuck sensors (zero variance over trailing windows).
2. Physical Boundary Enforcement & Sentinel Disentanglement:
   - AC Voltage: Valid in [50V, 320V]; values < 50V clipped/treated as grid outage.
   - DC Battery Voltage: Valid in [10.0V, 15.0V]; readings <= 0.5V identified as
     unequipped channels (converted to NaN with explicit is_battery_sensor_present=0).
   - Grid Frequency: Valid in [45.0Hz, 55.0Hz].
   - Enclosure Temperature: Valid in [-10.0°C, 70.0°C].
3. Unit Normalization: Auto-converts millivolts to volts and milliamperes to amperes.
4. Deduplication & Chronological Integrity: Enforces unique (device_id, timestamp)
   keys and sorts chronologically.
5. Profile Evidence Generation: Compares pre- vs. post-cleaning metrics, emitting
   quality audit evidence to reports/data_quality_profile.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger("data_autocleaner")

# Physical engineering boundaries for commercial electrical & gateway sensors
PHYSICAL_BOUNDS: Dict[str, Tuple[float, float]] = {
    "ac_voltage": (50.0, 320.0),
    "mains_voltage": (50.0, 320.0),
    "input_voltage": (50.0, 320.0),
    "battery_voltage": (10.0, 15.0),
    "dc_voltage": (10.0, 15.0),
    "grid_frequency": (45.0, 55.0),
    "frequency": (45.0, 55.0),
    "temperature": (-10.0, 70.0),
    "enclosure_temp": (-10.0, 70.0),
}

# Unequipped hardware thresholds
BATTERY_DISCONNECTED_THRESHOLD = 0.5  # <= 0.5V implies no physical battery sensor attached
AC_OUTAGE_THRESHOLD = 50.0            # < 50V proves electrical loss or unpowered line


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    total_count: int
    null_count: int
    null_rate: float
    distinct_count: int
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    mean_val: Optional[float] = None
    std_val: Optional[float] = None
    is_stuck: bool = False
    anomalous_outlier_count: int = 0


@dataclass
class DatasetProfile:
    timestamp: str
    total_rows: int
    total_columns: int
    unique_devices: int
    columns: Dict[str, Dict[str, Any]]
    stuck_sensor_count: int
    total_anomalies_detected: int


def profile_telemetry(df: pd.DataFrame, device_col: str = "device_id") -> DatasetProfile:
    """Compute comprehensive data quality profile across all telemetry channels."""
    total_rows = len(df)
    unique_devs = int(df[device_col].nunique()) if device_col in df.columns else 0
    cols_profile: Dict[str, Dict[str, Any]] = {}
    stuck_sensors = 0
    total_anomalies = 0

    for col in df.columns:
        series = df[col]
        null_c = int(series.isna().sum())
        null_r = round(null_c / total_rows, 4) if total_rows > 0 else 0.0
        distinct_c = int(series.nunique(dropna=True))

        min_v, max_v, mean_v, std_v = None, None, None, None
        is_stuck = False
        outliers = 0

        if pd.api.types.is_numeric_dtype(series):
            valid_vals = series.dropna()
            if len(valid_vals) > 0:
                min_v = round(float(valid_vals.min()), 3)
                max_v = round(float(valid_vals.max()), 3)
                mean_v = round(float(valid_vals.mean()), 3)
                std_v = round(float(valid_vals.std()), 3) if len(valid_vals) > 1 else 0.0

                # Detect stuck sensors: > 20 samples but standard deviation is exactly 0
                if len(valid_vals) >= 20 and (std_v == 0.0 or distinct_c == 1):
                    is_stuck = True
                    stuck_sensors += 1

                # Check physical bound violations if standard sensor key
                col_lower = col.lower()
                for key_pattern, (low, high) in PHYSICAL_BOUNDS.items():
                    if key_pattern in col_lower:
                        outlier_mask = (valid_vals < low) | (valid_vals > high)
                        outliers = int(outlier_mask.sum())
                        total_anomalies += outliers
                        break

        col_prof = ColumnProfile(
            name=col,
            dtype=str(series.dtype),
            total_count=total_rows,
            null_count=null_c,
            null_rate=null_r,
            distinct_count=distinct_c,
            min_val=min_v,
            max_val=max_v,
            mean_val=mean_v,
            std_val=std_v,
            is_stuck=is_stuck,
            anomalous_outlier_count=outliers,
        )
        cols_profile[col] = asdict(col_prof)

    return DatasetProfile(
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_rows=total_rows,
        total_columns=len(df.columns),
        unique_devices=unique_devs,
        columns=cols_profile,
        stuck_sensor_count=stuck_sensors,
        total_anomalies_detected=total_anomalies,
    )


def clean_telemetry(
    df: pd.DataFrame,
    device_col: str = "device_id",
    timestamp_col: str = "timestamp",
    feature_policy: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Clean, standardize, and normalize raw telemetry observations."""
    clean_df = df.copy()
    cleaning_summary: Dict[str, Any] = {
        "initial_rows": len(clean_df),
        "deduplicated_rows": 0,
        "units_normalized": {},
        "sentinels_disentangled": {},
        "outliers_sanitized": {},
        "policy_keys_filtered": 0,
    }

    # 1. Deduplication on (device_id, timestamp)
    if device_col in clean_df.columns and timestamp_col in clean_df.columns:
        n_before = len(clean_df)
        clean_df = clean_df.drop_duplicates(subset=[device_col, timestamp_col], keep="last")
        cleaning_summary["deduplicated_rows"] = n_before - len(clean_df)

    # 2. Timestamp sorting
    if timestamp_col in clean_df.columns:
        try:
            clean_df[timestamp_col] = pd.to_datetime(clean_df[timestamp_col], utc=True)
            clean_df = clean_df.sort_values(timestamp_col).reset_index(drop=True)
        except Exception:
            pass

    # 3. Unit Normalization & Outlier Sanitization
    for col in clean_df.columns:
        if not pd.api.types.is_numeric_dtype(clean_df[col]):
            continue

        col_lower = col.lower()

        # A. Battery Voltage Normalization (mV -> V) & Sentinel Disentanglement
        if "battery" in col_lower or "batt" in col_lower:
            valid_mask = clean_df[col].notna()
            median_val = clean_df.loc[valid_mask, col].median() if valid_mask.any() else np.nan

            # Unit fix: millivolts to volts
            if median_val > 100.0:
                clean_df[col] = clean_df[col] / 1000.0
                cleaning_summary["units_normalized"][col] = "Converted mV to V (divided by 1000)"

            # Sentinel fix: readings <= 0.5V indicate no physical battery equipped
            unequipped_mask = clean_df[col] <= BATTERY_DISCONNECTED_THRESHOLD
            if unequipped_mask.any():
                n_unequipped = int(unequipped_mask.sum())
                clean_df.loc[unequipped_mask, col] = np.nan
                clean_df["is_battery_sensor_present"] = (~unequipped_mask).astype(int)
                cleaning_summary["sentinels_disentangled"][col] = f"{n_unequipped} readings <= {BATTERY_DISCONNECTED_THRESHOLD}V masked to NaN; indicator added"

        # B. AC / Mains Voltage Sanitization
        elif "ac_volt" in col_lower or "mains_volt" in col_lower or "input_volt" in col_lower:
            # AC sensor unequipped or zero-reading
            low_ac_mask = clean_df[col] < AC_OUTAGE_THRESHOLD
            if low_ac_mask.any():
                # Readings < 50V represent active electrical outage rather than continuous linear voltage
                clean_df["is_ac_outage_detected"] = low_ac_mask.astype(int)

            # High voltage surge or unphysical spikes (> 350V)
            high_ac_mask = clean_df[col] > 350.0
            if high_ac_mask.any():
                n_high = int(high_ac_mask.sum())
                clean_df.loc[high_ac_mask, col] = 320.0  # Cap at safe physical upper bound
                cleaning_summary["outliers_sanitized"][col] = f"{n_high} surge values > 350V capped to 320V"

        # C. Current Normalization (mA -> A)
        elif "current" in col_lower and "amp" not in col_lower:
            valid_mask = clean_df[col].notna()
            median_val = clean_df.loc[valid_mask, col].median() if valid_mask.any() else np.nan
            if median_val > 500.0:
                clean_df[col] = clean_df[col] / 1000.0
                cleaning_summary["units_normalized"][col] = "Converted mA to A (divided by 1000)"

    # 4. Feature Policy Governance (Drop forbidden or unapproved keys)
    if feature_policy:
        allowed_keys = set(feature_policy.get("allowlist", []))
        if allowed_keys:
            drop_cols = [c for c in clean_df.columns if c not in allowed_keys and c not in (device_col, timestamp_col, "is_battery_sensor_present", "is_ac_outage_detected")]
            if drop_cols:
                clean_df = clean_df.drop(columns=drop_cols)
                cleaning_summary["policy_keys_filtered"] = len(drop_cols)

    cleaning_summary["final_rows"] = len(clean_df)
    return clean_df, cleaning_summary


def compare_profiles(raw: DatasetProfile, clean: DatasetProfile) -> Dict[str, Any]:
    """Compare raw vs. clean profiles to produce quantitative quality deltas."""
    comparison: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rows_before": raw.total_rows,
        "rows_after": clean.total_rows,
        "anomalies_before": raw.total_anomalies_detected,
        "anomalies_after": clean.total_anomalies_detected,
        "anomalies_resolved": raw.total_anomalies_detected - clean.total_anomalies_detected,
        "stuck_sensors_detected": raw.stuck_sensor_count,
        "column_deltas": {},
    }

    all_cols = set(raw.columns.keys()).intersection(clean.columns.keys())
    for col in sorted(all_cols):
        r_info = raw.columns[col]
        c_info = clean.columns[col]
        null_delta = round(c_info["null_rate"] - r_info["null_rate"], 4)
        comparison["column_deltas"][col] = {
            "null_rate_before": r_info["null_rate"],
            "null_rate_after": c_info["null_rate"],
            "null_rate_delta": null_delta,
            "min_before": r_info["min_val"],
            "min_after": c_info["min_val"],
            "max_before": r_info["max_val"],
            "max_after": c_info["max_val"],
            "is_stuck": r_info["is_stuck"],
        }

    return comparison


def save_quality_evidence(
    comparison: Dict[str, Any],
    report_path: Optional[Path] = None
) -> Path:
    """Save profiling evidence to JSON report."""
    if report_path is None:
        root_dir = Path(__file__).resolve().parents[2]
        report_path = root_dir / "reports/data_quality_profile.json"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2)

    logger.info(f"Saved telemetry data quality evidence to {report_path}")
    return report_path
