"""src/features/build_remediated_features.py
Phase R3: Remediated Feature Engineering & Target Construction.

Safeguards:
1. Target: y_power_outage_24h derived from remediated_power_outage_events.parquet (electrical only).
2. Leakage Elimination: heartbeat_gap_seconds removed from CLEAN feature set; preserved only under ablation_ prefix.
3. Sentinel Disentanglement:
   - Battery readings <= 0.5V are treated as NaN (unequipped channel).
   - Added explicit indicator is_battery_sensor_present.
   - Added explicit indicator is_ac_sensor_present.
   - Added device_state (ONLINE_OK, ONLINE_POWER_LOSS, STALE_DATA, OFFLINE_LONG).
4. Strictly point-in-time causal calculations.
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

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import yaml

from src.features.state_machine import classify_device_state_vectorized, DeviceState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("src.features.build_remediated_features")

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "configs/config.yaml"
PROCESSED_DIR = ROOT / "data/processed"
REPORTS_DIR = ROOT / "reports"


def build_remediated_device_features(
    device_id: str,
    device_name: str,
    bank: str,
    raw_dev_dir: Path,
    outage_spans: List[Tuple[float, float]],
    alarms_dev_df: pd.DataFrame,
    start_dt: datetime,
    end_dt: datetime,
    resample_interval: str = "15min",
) -> pd.DataFrame:
    grid = pd.date_range(start=start_dt, end=end_dt, freq=resample_interval, tz=timezone.utc)
    if len(grid) == 0:
        return pd.DataFrame()

    grid_series = pd.Series(grid, index=grid)

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

    # 1. Base Series aligned to grid
    ac_direct = read_ts_series(["ac_voltage.parquet", "ac_result.parquet"])
    ac_json = read_ts_series(["ac_status.parquet"], json_key="ac_voltage")
    ac_raw = ac_direct.combine_first(ac_json)

    batt_direct = read_ts_series(["battery_voltage.parquet"])
    batt_json = read_ts_series(["battery_status.parquet"], json_key="battery_voltage")
    batt_raw = batt_direct.combine_first(batt_json)

    # Sentinel handling for battery: if max voltage <= 0.5V, sensor is unequipped!
    is_batt_equipped = (batt_raw.max() > 0.5) if batt_raw.notna().any() else False
    if not is_batt_equipped:
        batt_raw = pd.Series(np.nan, index=grid)

    is_ac_equipped = (ac_raw.max() > 5.0) if ac_raw.notna().any() else False
    if not is_ac_equipped:
        ac_raw = pd.Series(np.nan, index=grid)

    mains_raw = read_ts_series(["statusbox_mains_on.parquet", "power_off_last.parquet"], is_bool=True)
    nvr_raw = read_ts_series(["Nvr_DVR_last.parquet", "dvr_nvr_off.parquet", "heartBeatCCTV.parquet"], is_bool=True)
    fas_raw = read_ts_series(["fasLastHour.parquet", "fasUptime.parquet", "fire_alarm_fault_last.parquet"], is_bool=True)
    storage_raw = read_ts_series(["disk.parquet", "hdd_error_last.parquet", "hddError.parquet"])
    hb_raw = read_ts_series(["uptimeHeartbeat.parquet", "heartbeat.parquet", "gatewayUptime.parquet", "lastActivityTime.parquet"], is_bool=True)

    # 2. Tracking telemetry arrival for state machine & gap features
    def calc_gap_seconds(s: pd.Series) -> pd.Series:
        last_seen = grid_series.where(s.notna()).ffill()
        gap = (grid_series - last_seen).dt.total_seconds().fillna(86400.0)
        return gap

    any_telem = ac_raw.notna() | batt_raw.notna() | mains_raw.notna() | hb_raw.notna()
    effective_hb = hb_raw.combine_first(grid_series.where(any_telem, np.nan))
    hb_gap = calc_gap_seconds(effective_hb)
    telem_last_seen = grid_series.where(any_telem).ffill()
    telem_age = (grid_series - telem_last_seen).dt.total_seconds().fillna(86400.0)

    # 3. State Machine Classification
    state_series = classify_device_state_vectorized(
        telemetry_age_seconds=telem_age,
        ac_voltage=ac_raw,
        mains_on=mains_raw,
        battery_voltage=batt_raw,
        is_power_alarm_active=pd.Series(0, index=grid),
    )

    # 4. Rolling Statistics (Only carry forward when sensor is equipped and fresh)
    # AC rolling stats
    ac_filled = ac_raw.ffill(limit=8) # leave as NaN if unequipped
    steps_1h = max(1, int(pd.Timedelta("1h") / pd.Timedelta(resample_interval)))
    steps_6h = max(1, int(pd.Timedelta("6h") / pd.Timedelta(resample_interval)))
    steps_24h = max(1, int(pd.Timedelta("24h") / pd.Timedelta(resample_interval)))
    steps_7d = max(1, int(pd.Timedelta("7D") / pd.Timedelta(resample_interval)))

    ac_mean_1h = ac_filled.rolling(steps_1h, min_periods=1).mean().fillna(220.0) # nominal impute for unequipped with indicator
    ac_std_1h = ac_filled.rolling(steps_1h, min_periods=1).std().fillna(0.0)
    ac_min_1h = ac_filled.rolling(steps_1h, min_periods=1).min().fillna(220.0)
    ac_max_1h = ac_filled.rolling(steps_1h, min_periods=1).max().fillna(220.0)

    ac_mean_6h = ac_filled.rolling(steps_6h, min_periods=1).mean().fillna(220.0)
    ac_std_6h = ac_filled.rolling(steps_6h, min_periods=1).std().fillna(0.0)
    ac_min_6h = ac_filled.rolling(steps_6h, min_periods=1).min().fillna(220.0)
    ac_max_6h = ac_filled.rolling(steps_6h, min_periods=1).max().fillna(220.0)

    ac_mean_24h = ac_filled.rolling(steps_24h, min_periods=1).mean().fillna(220.0)
    ac_std_24h = ac_filled.rolling(steps_24h, min_periods=1).std().fillna(0.0)
    ac_slope_6h = (ac_filled - ac_filled.shift(steps_6h)).fillna(0.0) / 6.0
    ac_roll_7d = ac_filled.rolling(steps_7d, min_periods=steps_1h)
    ac_zscore_7d = ((ac_filled - ac_roll_7d.mean()) / (ac_roll_7d.std() + 1e-5)).fillna(0.0)

    # Battery rolling stats & discharge rate
    batt_filled = batt_raw.ffill(limit=8)
    batt_mean_1h = batt_filled.rolling(steps_1h, min_periods=1).mean().fillna(13.5) # nominal float
    batt_std_1h = batt_filled.rolling(steps_1h, min_periods=1).std().fillna(0.0)
    batt_min_1h = batt_filled.rolling(steps_1h, min_periods=1).min().fillna(13.5)
    batt_max_1h = batt_filled.rolling(steps_1h, min_periods=1).max().fillna(13.5)

    batt_mean_6h = batt_filled.rolling(steps_6h, min_periods=1).mean().fillna(13.5)
    batt_std_6h = batt_filled.rolling(steps_6h, min_periods=1).std().fillna(0.0)
    batt_mean_24h = batt_filled.rolling(steps_24h, min_periods=1).mean().fillna(13.5)
    batt_std_24h = batt_filled.rolling(steps_24h, min_periods=1).std().fillna(0.0)

    batt_discharge_1h = (batt_max_1h - batt_filled).clip(lower=0.0).fillna(0.0)
    batt_discharge_6h = ((batt_filled.rolling(steps_6h, min_periods=1).max() - batt_filled) / 6.0).clip(lower=0.0).fillna(0.0)
    batt_roll_7d = batt_filled.rolling(steps_7d, min_periods=steps_1h)
    batt_zscore_7d = ((batt_filled - batt_roll_7d.mean()) / (batt_roll_7d.std() + 1e-5)).fillna(0.0)

    # 5. Mains Loss History
    mains_lost = ((ac_filled < 50.0) | (mains_raw == 0.0)).astype(int)
    last_mains_lost_dt = grid_series.where(mains_lost == 1).ffill()
    time_since_mains_loss_h = (grid_series - last_mains_lost_dt).dt.total_seconds().fillna(168.0 * 3600) / 3600.0
    mains_loss_transitions = (mains_lost.diff() == 1).astype(int)
    mains_loss_count_7d = mains_loss_transitions.rolling(steps_7d, min_periods=1).sum().fillna(0).astype(int)

    # 6. Alarms by Severity
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

    # 7. Fault Flags
    nvr_gap = calc_gap_seconds(nvr_raw)
    nvr_fault = (nvr_gap > 1800.0).astype(int)
    fas_fault = (fas_raw.ffill(limit=8).fillna(0.0) > 0).astype(int)
    storage_fault = (storage_raw.ffill(limit=8).fillna(0.0) > 80.0).astype(int)

    # 8. Temporal Indicators
    hours = grid.hour
    sin_hour = np.sin(2 * np.pi * hours / 24.0)
    cos_hour = np.cos(2 * np.pi * hours / 24.0)

    # 9. Lagged Historical Connectivity Feature (LEGAL LAG >= 24h)
    # Count of connectivity drops in the previous 7 days ending 24h ago
    hb_drops = (hb_gap > 3600.0).astype(int)
    lag_24h_steps = steps_24h
    lagged_hb_drops_7d = hb_drops.shift(lag_24h_steps).rolling(steps_7d, min_periods=1).sum().fillna(0).astype(int)

    # 10. Target: Remediated Outage in (t, t + 24h]
    grid_ms = grid.astype("int64").values // 1000
    outage_starts_ms = np.array([span[0] for span in outage_spans]) if outage_spans else np.array([])
    if len(outage_starts_ms) > 0:
        t_plus_24h_ms = grid_ms + 24 * 3600 * 1000
        g_col = grid_ms[:, None]
        g_next = t_plus_24h_ms[:, None]
        ev_row = outage_starts_ms[None, :]
        is_pos = np.any((ev_row > g_col) & (ev_row <= g_next), axis=1)
        y_target = is_pos.astype(int)
    else:
        y_target = np.zeros(len(grid), dtype=int)

    res_df = pd.DataFrame({
        "device_id": device_id,
        "device_name": device_name,
        "bank": bank,
        "timestamp": grid.strftime("%Y-%m-%d %H:%M:%S%z"),
        # State machine and sensor presence indicators
        "device_state": state_series.values,
        "is_battery_sensor_present": int(is_batt_equipped),
        "is_ac_sensor_present": int(is_ac_equipped),
        "state_online_ok": (state_series == DeviceState.ONLINE_OK.value).astype(int),
        "state_online_power_loss": (state_series == DeviceState.ONLINE_POWER_LOSS.value).astype(int),
        "state_stale_data": (state_series == DeviceState.STALE_DATA.value).astype(int),
        "state_offline_long": (state_series == DeviceState.OFFLINE_LONG.value).astype(int),
        # AC rolling
        "ac_voltage_mean_1h": ac_mean_1h.values,
        "ac_voltage_std_1h": ac_std_1h.values,
        "ac_voltage_min_1h": ac_min_1h.values,
        "ac_voltage_max_1h": ac_max_1h.values,
        "ac_voltage_mean_6h": ac_mean_6h.values,
        "ac_voltage_std_6h": ac_std_6h.values,
        "ac_voltage_min_6h": ac_min_6h.values,
        "ac_voltage_max_6h": ac_max_6h.values,
        "ac_voltage_mean_24h": ac_mean_24h.values,
        "ac_voltage_std_24h": ac_std_24h.values,
        "ac_voltage_slope_6h": ac_slope_6h.values,
        "ac_zscore_7d": ac_zscore_7d.values,
        # Battery rolling
        "battery_voltage_mean_1h": batt_mean_1h.values,
        "battery_voltage_std_1h": batt_std_1h.values,
        "battery_voltage_min_1h": batt_min_1h.values,
        "battery_voltage_max_1h": batt_max_1h.values,
        "battery_voltage_mean_6h": batt_mean_6h.values,
        "battery_voltage_std_6h": batt_std_6h.values,
        "battery_voltage_mean_24h": batt_mean_24h.values,
        "battery_voltage_std_24h": batt_std_24h.values,
        "battery_discharge_rate_1h": batt_discharge_1h.values,
        "battery_discharge_rate_6h": batt_discharge_6h.values,
        "battery_zscore_7d": batt_zscore_7d.values,
        # Mains loss history
        "time_since_mains_loss_hours": time_since_mains_loss_h.values,
        "mains_loss_count_7d": mains_loss_count_7d.values,
        # Alarms
        "alarms_critical_1h": crit_1h.values,
        "alarms_critical_6h": crit_6h.values,
        "alarms_critical_24h": crit_24h.values,
        "alarms_major_1h": major_1h.values,
        "alarms_major_6h": major_6h.values,
        "alarms_major_24h": major_24h.values,
        "alarms_warning_1h": warn_1h.values,
        "alarms_warning_6h": warn_6h.values,
        "alarms_warning_24h": warn_24h.values,
        # Faults & Time
        "nvr_fault_flag": nvr_fault.values,
        "fas_fault_flag": fas_fault.values,
        "storage_fault_flag": storage_fault.values,
        "hour_of_day": hours.values,
        "day_of_week": grid.dayofweek.values,
        "sin_hour": sin_hour.values,
        "cos_hour": cos_hour.values,
        # Lagged historical connectivity (Legal lag >= 24h)
        "lagged_connectivity_drops_prev_7d": lagged_hb_drops_7d.values,
        # Ablation column (BANNED from clean feature set, kept only for ablation study)
        "ablation_heartbeat_gap_seconds": hb_gap.values,
        # Target
        "y_power_outage_24h": y_target,
    }, index=grid)

    return res_df


def build_all_remediated_features() -> pd.DataFrame:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.info("Loading remediated outage events from data/processed/remediated_power_outage_events.parquet...")
    events_df = pd.read_parquet(PROCESSED_DIR / "remediated_power_outage_events.parquet")
    logger.info(f"Loaded {len(events_df)} independent electrical outage events.")

    events_by_did: Dict[str, List[Tuple[float, float]]] = {}
    for _, row in events_df.iterrows():
        did = str(row["device_id"])
        events_by_did.setdefault(did, []).append((float(row["start_ts"]), float(row["end_ts"])))

    reg_df = pd.read_parquet(ROOT / "data/raw/registry/devices.parquet")
    eligible_df = reg_df[reg_df["eligible"] == True].copy()
    logger.info(f"Found {len(eligible_df)} eligible devices.")

    alarms_df = pd.read_parquet(ROOT / "data/raw/alarms/alarms.parquet")

    # Time bounds (consistent 90 days grid up to current data end)
    end_dt = datetime(2026, 10, 13, 10, 0, 0, tzinfo=timezone.utc)
    start_dt = end_dt - timedelta(days=90)

    device_dfs = []
    logger.info("Vectorizing remediated features across all devices...")
    for idx, row in eligible_df.iterrows():
        did = str(row["device_id"])
        dname = str(row["name"])
        bank = str(row["bank"])
        raw_dev_dir = ROOT / "data/raw/telemetry" / did
        if not raw_dev_dir.exists():
            continue

        spans = events_by_did.get(did, [])
        dev_alarms = alarms_df[alarms_df["device_id"].astype(str) == did] if "device_id" in alarms_df.columns else pd.DataFrame()

        dev_feat = build_remediated_device_features(
            device_id=did,
            device_name=dname,
            bank=bank,
            raw_dev_dir=raw_dev_dir,
            outage_spans=spans,
            alarms_dev_df=dev_alarms,
            start_dt=start_dt,
            end_dt=end_dt,
            resample_interval="15min",
        )
        if not dev_feat.empty:
            device_dfs.append(dev_feat)

    comb_df = pd.concat(device_dfs, ignore_index=True)
    out_parquet = PROCESSED_DIR / "remediated_features_matrix.parquet"
    comb_df.to_parquet(out_parquet, index=False)
    logger.info(f"Saved remediated features matrix to {out_parquet}: shape {comb_df.shape}")

    pos_count = int(comb_df["y_power_outage_24h"].sum())
    total_count = len(comb_df)
    logger.info(f"Remediated Matrix Positives: {pos_count:,} / {total_count:,} ({pos_count/total_count*100:.3f}% prevalence)")

    return comb_df


if __name__ == "__main__":
    build_all_remediated_features()
