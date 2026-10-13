#!/usr/bin/env python
"""Phase 2: Leakage-Free Feature Engineering & Fixed-Grid Resampling (Vectorized).

Computes point-in-time features strictly using data <= anchor t:
- Fixed 15-minute grid alignment
- Explicit missingness & gap encoding (never silently filled)
- Rolling backward statistics: mean, std, min, max, slope over 1h, 6h, 24h for AC and Battery voltage
- Battery discharge rate (1h, 6h)
- Mains loss recency & 7-day cumulative event count
- Rolling alarm counts by severity (Critical, Major, Warning) over 1h, 6h, 24h
- NVR, FAS, and storage fault indicators
- Temporal features (hour of day, day of week)
- Per-device rolling z-score vs device's own 7-day history
- Target: Outage occurrence in (t, t + 24h]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("src.features.build_features")


def compute_slope(values: np.ndarray, times_hours: np.ndarray) -> float:
    """Calculate linear trend slope (dVal / dHour)."""
    valid = ~np.isnan(values) & ~np.isnan(times_hours)
    if np.sum(valid) < 2:
        return 0.0
    x = times_hours[valid]
    y = values[valid]
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    denom = np.sum((x - x_mean) ** 2)
    if denom == 0:
        return 0.0
    return float(np.sum((x - x_mean) * (y - y_mean)) / denom)


def build_device_features(
    device_id: str,
    device_name: str,
    raw_dev_dir: Path,
    outage_spans: List[Tuple[float, float]],
    alarms_dev_df: pd.DataFrame,
    start_dt: datetime,
    end_dt: datetime,
    resample_interval: str = "15min",
) -> pd.DataFrame:
    """Extract strictly causal features for a single device over the fixed grid."""
    grid = pd.date_range(start=start_dt, end=end_dt, freq=resample_interval, tz=timezone.utc)
    if len(grid) == 0:
        return pd.DataFrame()

    def read_ts_series(
        filenames: list[str] | str,
        json_key: str | None = None,
        is_bool: bool = False,
    ) -> pd.Series:
        if isinstance(filenames, str):
            filenames = [filenames]
        dfs = []
        for fn in filenames:
            pfile = raw_dev_dir / fn
            if not pfile.exists():
                continue
            try:
                df = pd.read_parquet(pfile)
                ts_col = "ts" if "ts" in df.columns else "timestamp"
                val_col = "value" if "value" in df.columns else df.columns[1]
                df = df.dropna(subset=[ts_col, val_col]).copy()
                if df.empty:
                    continue
                df["dt"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
                v_str = df[val_col].astype(str).str.strip().str.lower()
                if json_key:
                    pat = rf"{json_key}[\"':\s]+([0-9.]+)"
                    df["val"] = pd.to_numeric(v_str.str.extract(pat, expand=False), errors="coerce")
                elif is_bool:
                    bmap = {"true": 1.0, "false": 0.0, "1": 1.0, "0": 0.0, "t": 1.0, "f": 0.0, "on": 1.0, "off": 0.0}
                    df["val"] = v_str.map(bmap).fillna(pd.to_numeric(df[val_col], errors="coerce"))
                else:
                    df["val"] = pd.to_numeric(df[val_col], errors="coerce")
                df = df.dropna(subset=["dt", "val"])
                if not df.empty:
                    dfs.append(df[["dt", "val"]])
            except Exception:
                continue
        if not dfs:
            return pd.Series(index=grid, dtype=float)
        comb = pd.concat(dfs).sort_values("dt").drop_duplicates(subset=["dt"], keep="last")
        s_resampled = comb.set_index("dt")["val"].resample(resample_interval).last()
        return s_resampled.reindex(grid)

    # 1. Base Series aligned to grid (supporting JSON and boolean telemetry)
    ac_direct = read_ts_series(["ac_voltage.parquet", "ac_result.parquet"])
    ac_json = read_ts_series(["ac_status.parquet"], json_key="ac_voltage")
    ac_raw = ac_direct.combine_first(ac_json)

    batt_direct = read_ts_series(["battery_voltage.parquet"])
    batt_json = read_ts_series(["battery_status.parquet"], json_key="battery_voltage")
    batt_raw = batt_direct.combine_first(batt_json)

    mains_raw = read_ts_series(["statusbox_mains_on.parquet", "power_off_last.parquet"], is_bool=True)
    nvr_raw = read_ts_series(["Nvr_DVR_last.parquet", "dvr_nvr_off.parquet", "heartBeatCCTV.parquet"], is_bool=True)
    fas_raw = read_ts_series(["fasLastHour.parquet", "fasUptime.parquet", "fire_alarm_fault_last.parquet"], is_bool=True)
    storage_raw = read_ts_series(["disk.parquet", "hdd_error_last.parquet", "hddError.parquet"])
    hb_raw = read_ts_series(["uptimeHeartbeat.parquet", "heartbeat.parquet", "gatewayUptime.parquet", "lastActivityTime.parquet"], is_bool=True)

    # 2. Explicit Gap Encoding & Missingness (No silent forward filling)
    # Track elapsed time since last valid observation
    grid_series = pd.Series(grid, index=grid)

    def calc_gap_seconds(s: pd.Series) -> pd.Series:
        last_seen = grid_series.where(s.notna()).ffill()
        gap = (grid_series - last_seen).dt.total_seconds().fillna(86400.0)
        return gap

    any_telem = ac_raw.notna() | batt_raw.notna() | mains_raw.notna() | hb_raw.notna()
    effective_hb = hb_raw.combine_first(grid_series.where(any_telem, np.nan))
    hb_gap = calc_gap_seconds(effective_hb)
    telem_last_seen = grid_series.where(any_telem).ffill()
    telem_age = (grid_series - telem_last_seen).dt.total_seconds().fillna(86400.0)
    missing_indicator = (telem_age > 900.0).astype(int)

    # 3. Capped carry-forward for rolling signals (max 2 hours = 8 steps)
    ac_filled = ac_raw.ffill(limit=8).fillna(0.0)
    batt_filled = batt_raw.ffill(limit=8).fillna(0.0)

    # Steps for 1h, 6h, 24h, 7d
    steps_1h = max(1, int(pd.Timedelta("1h") / pd.Timedelta(resample_interval)))
    steps_6h = max(1, int(pd.Timedelta("6h") / pd.Timedelta(resample_interval)))
    steps_24h = max(1, int(pd.Timedelta("24h") / pd.Timedelta(resample_interval)))
    steps_7d = max(1, int(pd.Timedelta("7D") / pd.Timedelta(resample_interval)))

    # AC rolling stats
    ac_roll_1h = ac_filled.rolling(steps_1h, min_periods=1)
    ac_roll_6h = ac_filled.rolling(steps_6h, min_periods=1)
    ac_roll_24h = ac_filled.rolling(steps_24h, min_periods=1)
    ac_roll_7d = ac_filled.rolling(steps_7d, min_periods=steps_1h)

    ac_mean_1h = ac_roll_1h.mean()
    ac_std_1h = ac_roll_1h.std().fillna(0.0)
    ac_min_1h = ac_roll_1h.min()
    ac_max_1h = ac_roll_1h.max()

    ac_mean_6h = ac_roll_6h.mean()
    ac_std_6h = ac_roll_6h.std().fillna(0.0)
    ac_min_6h = ac_roll_6h.min()
    ac_max_6h = ac_roll_6h.max()

    ac_mean_24h = ac_roll_24h.mean()
    ac_std_24h = ac_roll_24h.std().fillna(0.0)

    ac_slope_6h = (ac_filled - ac_filled.shift(steps_6h)).fillna(0.0) / 6.0
    ac_zscore_7d = ((ac_filled - ac_roll_7d.mean()) / (ac_roll_7d.std() + 1e-5)).fillna(0.0)

    # Battery rolling stats & discharge rate
    batt_roll_1h = batt_filled.rolling(steps_1h, min_periods=1)
    batt_roll_6h = batt_filled.rolling(steps_6h, min_periods=1)
    batt_roll_24h = batt_filled.rolling(steps_24h, min_periods=1)
    batt_roll_7d = batt_filled.rolling(steps_7d, min_periods=steps_1h)

    batt_mean_1h = batt_roll_1h.mean()
    batt_std_1h = batt_roll_1h.std().fillna(0.0)
    batt_min_1h = batt_roll_1h.min()
    batt_max_1h = batt_roll_1h.max()

    batt_mean_6h = batt_roll_6h.mean()
    batt_std_6h = batt_roll_6h.std().fillna(0.0)

    batt_mean_24h = batt_roll_24h.mean()
    batt_std_24h = batt_roll_24h.std().fillna(0.0)

    batt_discharge_1h = (batt_max_1h - batt_filled).clip(lower=0.0)
    batt_discharge_6h = ((batt_roll_6h.max() - batt_filled) / 6.0).clip(lower=0.0)
    batt_zscore_7d = ((batt_filled - batt_roll_7d.mean()) / (batt_roll_7d.std() + 1e-5)).fillna(0.0)

    # 4. Mains Loss History
    mains_lost = (ac_filled < 50.0).astype(int)
    last_mains_lost_dt = grid_series.where(mains_lost == 1).ffill()
    time_since_mains_loss_h = (grid_series - last_mains_lost_dt).dt.total_seconds().fillna(168.0 * 3600) / 3600.0
    mains_loss_transitions = (mains_lost.diff() == 1).astype(int)
    mains_loss_count_7d = mains_loss_transitions.rolling(steps_7d, min_periods=1).sum().fillna(0).astype(int)

    # 5. Alarm Counts by Severity
    crit_counts = pd.Series(0, index=grid)
    major_counts = pd.Series(0, index=grid)
    warn_counts = pd.Series(0, index=grid)

    if not alarms_dev_df.empty:
        s_col = "start" if "start" in alarms_dev_df.columns else "start_ts"
        al_df = alarms_dev_df.copy()
        al_df["dt"] = pd.to_datetime(al_df[s_col], format="ISO8601", utc=True, errors="coerce")
        al_df["sev"] = al_df["severity"].astype(str).str.upper()
        al_df = al_df.dropna(subset=["dt"]).sort_values("dt")

        for sev, s_target in [("CRITICAL", crit_counts), ("MAJOR", major_counts), ("WARNING", warn_counts)]:
            sub = al_df[al_df["sev"] == sev]
            if not sub.empty:
                counts = sub.set_index("dt")["sev"].resample(resample_interval).count().reindex(grid).fillna(0)
                s_target.update(counts)

    crit_1h = crit_counts.rolling(steps_1h, min_periods=1).sum().astype(int)
    crit_6h = crit_counts.rolling(steps_6h, min_periods=1).sum().astype(int)
    crit_24h = crit_counts.rolling(steps_24h, min_periods=1).sum().astype(int)

    major_1h = major_counts.rolling(steps_1h, min_periods=1).sum().astype(int)
    major_6h = major_counts.rolling(steps_6h, min_periods=1).sum().astype(int)
    major_24h = major_counts.rolling(steps_24h, min_periods=1).sum().astype(int)

    warn_1h = warn_counts.rolling(steps_1h, min_periods=1).sum().astype(int)
    warn_6h = warn_counts.rolling(steps_6h, min_periods=1).sum().astype(int)
    warn_24h = warn_counts.rolling(steps_24h, min_periods=1).sum().astype(int)

    # 6. Fault Flags
    nvr_gap = calc_gap_seconds(nvr_raw)
    nvr_fault = (nvr_gap > 1800.0).astype(int)
    fas_fault = (fas_raw.ffill(limit=8).fillna(0.0) > 0).astype(int)
    storage_fault = (storage_raw.ffill(limit=8).fillna(0.0) > 80.0).astype(int)

    # 7. Temporal Indicators
    hours = grid.hour
    sin_hour = np.sin(2 * np.pi * hours / 24.0)
    cos_hour = np.cos(2 * np.pi * hours / 24.0)

    # 8. Target: Outage in (t, t + 24h]
    grid_ms = grid.astype("int64").values // 1000
    outage_starts_ms = np.array([span[0] for span in outage_spans]) if outage_spans else np.array([])
    if len(outage_starts_ms) > 0:
        t_plus_24h_ms = grid_ms + 24 * 3600 * 1000
        g_col = grid_ms[:, None]
        g_next = t_plus_24h_ms[:, None]
        ev_row = outage_starts_ms[None, :]
        is_pos = np.any((ev_row > g_col) & (ev_row <= g_next), axis=1)
        y_outage_24h = is_pos.astype(int)
    else:
        y_outage_24h = np.zeros(len(grid), dtype=int)

    df_out = pd.DataFrame(
        {
            "device_id": device_id,
            "device_name": device_name,
            "timestamp": [t.isoformat() for t in grid],
            "heartbeat_gap_seconds": hb_gap.values.round(1),
            "telemetry_age_seconds": telem_age.values.round(1),
            "missing_indicator": missing_indicator.values,
            "ac_voltage": ac_filled.values.round(2),
            "ac_voltage_mean_1h": ac_mean_1h.values.round(2),
            "ac_voltage_std_1h": ac_std_1h.values.round(2),
            "ac_voltage_min_1h": ac_min_1h.values.round(2),
            "ac_voltage_max_1h": ac_max_1h.values.round(2),
            "ac_voltage_mean_6h": ac_mean_6h.values.round(2),
            "ac_voltage_std_6h": ac_std_6h.values.round(2),
            "ac_voltage_min_6h": ac_min_6h.values.round(2),
            "ac_voltage_max_6h": ac_max_6h.values.round(2),
            "ac_voltage_mean_24h": ac_mean_24h.values.round(2),
            "ac_voltage_std_24h": ac_std_24h.values.round(2),
            "ac_voltage_slope_6h": ac_slope_6h.values.round(4),
            "ac_zscore_7d": ac_zscore_7d.values.round(2),
            "battery_voltage": batt_filled.values.round(2),
            "battery_voltage_mean_1h": batt_mean_1h.values.round(2),
            "battery_voltage_std_1h": batt_std_1h.values.round(2),
            "battery_voltage_min_1h": batt_min_1h.values.round(2),
            "battery_voltage_max_1h": batt_max_1h.values.round(2),
            "battery_voltage_mean_6h": batt_mean_6h.values.round(2),
            "battery_voltage_std_6h": batt_std_6h.values.round(2),
            "battery_voltage_mean_24h": batt_mean_24h.values.round(2),
            "battery_voltage_std_24h": batt_std_24h.values.round(2),
            "battery_discharge_rate_1h": batt_discharge_1h.values.round(3),
            "battery_discharge_rate_6h": batt_discharge_6h.values.round(3),
            "battery_zscore_7d": batt_zscore_7d.values.round(2),
            "time_since_mains_loss_hours": time_since_mains_loss_h.values.round(2),
            "mains_loss_count_7d": mains_loss_count_7d.values,
            "alarms_critical_1h": crit_1h.values,
            "alarms_critical_6h": crit_6h.values,
            "alarms_critical_24h": crit_24h.values,
            "alarms_major_1h": major_1h.values,
            "alarms_major_6h": major_6h.values,
            "alarms_major_24h": major_24h.values,
            "alarms_warning_1h": warn_1h.values,
            "alarms_warning_6h": warn_6h.values,
            "alarms_warning_24h": warn_24h.values,
            "nvr_fault_flag": nvr_fault.values,
            "fas_fault_flag": fas_fault.values,
            "storage_fault_flag": storage_fault.values,
            "hour_of_day": grid.hour,
            "day_of_week": grid.weekday,
            "sin_hour": np.round(sin_hour, 4),
            "cos_hour": np.round(cos_hour, 4),
            "y_outage_24h": y_outage_24h,
        }
    )

    return df_out


def build_all_features(
    config_path: str = "configs/config.yaml",
    max_devices: Optional[int] = None,
    output_parquet: str = "data/processed/features_matrix.parquet",
) -> pd.DataFrame:
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    raw_telemetry = Path(config["paths"]["data_raw_telemetry"])
    raw_alarms = Path(config["paths"]["data_raw_alarms"]) / "alarms.parquet"
    raw_registry = Path(config["paths"]["data_raw_registry"]) / "devices.parquet"
    processed_dir = Path(config["paths"]["data_processed"])
    processed_dir.mkdir(parents=True, exist_ok=True)

    # 1. Device list
    dev_df = pd.read_parquet(raw_registry)
    eligible_devs = dev_df[dev_df["is_target_bank"]].copy()
    if max_devices:
        eligible_devs = eligible_devs.head(max_devices)

    # 2. Outage audit spans
    device_outage_spans: Dict[str, List[Tuple[float, float]]] = {}
    events_parquet = Path(config["paths"]["data_processed"]) / "outage_events.parquet"
    if events_parquet.exists():
        ev_df = pd.read_parquet(events_parquet)
        for _, ev in ev_df.iterrows():
            did = str(ev["device_id"])
            device_outage_spans.setdefault(did, []).append((float(ev["start_ts"]), float(ev["end_ts"])))
        logger.info(f"Loaded {len(ev_df)} outage events across {len(device_outage_spans)} devices from {events_parquet}.")
    else:
        audit_json = Path(config["paths"]["reports_dir"]) / "phase_1_audit_tables.json"
        if audit_json.exists():
            with open(audit_json, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
                sample_events = audit_data.get("outage_audit", {}).get("sample_events", [])
                for ev in sample_events:
                    did = ev["device_id"]
                    device_outage_spans.setdefault(did, []).append((float(ev["start_ts"]), float(ev["end_ts"])))

    # 3. Alarms
    alarms_df = pd.read_parquet(raw_alarms) if raw_alarms.exists() else pd.DataFrame()

    # Time bounds (last 90 days grid)
    end_dt = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=config["ingest"]["lookback_days"])
    resample_freq = config["features"]["resample_interval"]

    logger.info(f"Extracting features for {len(eligible_devs)} devices from {start_dt.isoformat()} to {end_dt.isoformat()} (grid: {resample_freq})...")

    all_dfs = []
    for idx, row in eligible_devs.iterrows():
        did = str(row["device_id"])
        dname = str(row["name"])
        raw_dev_dir = raw_telemetry / did
        if not raw_dev_dir.exists():
            continue

        spans = device_outage_spans.get(did, [])
        alarms_sub = alarms_df[alarms_df["device_id"] == did] if "device_id" in alarms_df.columns else pd.DataFrame()

        df_feat = build_device_features(
            device_id=did,
            device_name=dname,
            raw_dev_dir=raw_dev_dir,
            outage_spans=spans,
            alarms_dev_df=alarms_sub,
            start_dt=start_dt,
            end_dt=end_dt,
            resample_interval=resample_freq,
        )
        if not df_feat.empty:
            all_dfs.append(df_feat)
            logger.info(f"Processed {dname} ({did}): {len(df_feat)} rows, {df_feat['y_outage_24h'].sum()} positive targets.")

    if not all_dfs:
        raise RuntimeError("No features extracted.")

    full_matrix = pd.concat(all_dfs, ignore_index=True)
    out_path = Path(output_parquet)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    full_matrix.to_parquet(out_path, index=False)
    logger.info(f"Feature matrix complete! {len(full_matrix)} rows, {len(full_matrix.columns)} columns saved to {out_path}.")
    return full_matrix


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Leakage-Free Features Matrix")
    parser.add_argument("--config", default="configs/config.yaml", help="Path to config.yaml")
    parser.add_argument("--max-devices", type=int, default=None, help="Limit devices for testing")
    parser.add_argument("--out", default="data/processed/features_matrix.parquet", help="Output parquet path")
    args = parser.parse_args()

    build_all_features(config_path=args.config, max_devices=args.max_devices, output_parquet=args.out)


if __name__ == "__main__":
    main()
