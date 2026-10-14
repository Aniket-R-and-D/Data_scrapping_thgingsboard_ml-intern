"""src/eval/build_splits.py — Stage D clean evaluation split builder + frozen manifests.

Produces three strictly disjoint splits from the incident table built by
src/eval/build_incidents.py:

  1. TRAIN (data/eval/train.jsonl, manifest_train.json)
     - Known devices only (108 of 135 fleet devices).
     - Only incidents whose incident_end_utc <= TRAIN_CUTOFF_UTC
       (2026-10-08T00:00:00+00:00).

  2. FUTURE_INCIDENTS_KNOWN_DEVICES
     (data/eval/future_known_devices.jsonl, manifest_future_known_devices.json)
     - Known devices (may appear in TRAIN).
     - Only incidents whose incident_start_utc >= TRAIN_CUTOFF_UTC + PURGE_WINDOW
       (2026-10-09T00:00:00+00:00, after a 24-hour purge window matching the
       24-hour prediction horizon).
     - Tests adaptation to future incidents at known branch sites.

  3. UNSEEN_DEVICES
     (data/eval/unseen_devices.jsonl, manifest_unseen_devices.json)
     - 27 whole devices held out across all 4 banks (BOI 20, SBI 4, BOB 2,
       CANARA 1). Zero records, incidents, or payloads from these devices
       appear in TRAIN or FUTURE_INCIDENTS_KNOWN_DEVICES.
     - Tests generalization to new branch sites.

Incidents that straddle TRAIN_CUTOFF_UTC or start inside the 24-hour purge
window on known devices are excluded from all splits and recorded with an
explicit exclusion reason in incidents_table.parquet and split_build_summary.json.

Canonical payload deduplication:
- Every candidate row is converted into a validated Stage B/C TicketInput and
  rendered by the deterministic renderer (src/tickets/renderer.py).
- The canonical payload hash is computed BEFORE writing splits; exact duplicate
  payload hashes within a split are removed (counted per split), and any
  evaluation record whose canonical payload hash also occurred in TRAIN is
  removed (counted) so cross-split payload overlap is strictly zero.
- Completion (ticket text without the per-record Ticket ID / hash lines, plus
  full-text completion) overlap is reported separately: template structure
  alone is not leakage, and is reported transparently.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.tickets.field_validator import validate_candidate
from src.tickets.policy import POLICY_VERSION, format_probability
from src.tickets.renderer import canonical_payload_hash, render_ticket
from src.tickets.schema import Measurement, TicketInput

EVAL_DIR = ROOT / "data" / "eval"
INCIDENTS_PARQUET = EVAL_DIR / "incidents_table.parquet"
ROWS_PARQUET = EVAL_DIR / "rows_with_incidents.parquet"

TRAIN_CUTOFF_UTC = datetime(2026, 10, 8, 0, 0, 0, tzinfo=timezone.utc)
PURGE_WINDOW = timedelta(hours=24)
EVAL_START_UTC = TRAIN_CUTOFF_UTC + PURGE_WINDOW  # 2026-10-09T00:00:00+00:00

# Stratified unseen-device quota per bank (27 of 135 devices = 20.0%).
UNSEEN_QUOTA_BY_BANK = {"BOI": 20, "SBI": 4, "BOB": 2, "CANARA": 1}

CREATION_COMMAND = "python src/eval/build_splits.py"


def _select_unseen_devices(inc_df: pd.DataFrame) -> set[str]:
    """Deterministic, stratified whole-device holdout across all 4 banks.

    Within each bank, devices are ordered by (has_power_incident desc,
    device_id asc) and every k-th device is picked so the unseen set contains
    both active-outage sites and steady/connectivity sites.
    """
    unseen: set[str] = set()
    for bank, quota in UNSEEN_QUOTA_BY_BANK.items():
        sub = inc_df[inc_df["bank"] == bank]
        power_devs = set(sub[sub["regime"] == "POWER"]["device_id"].unique())
        all_devs = sorted(sub["device_id"].unique())
        ordered = sorted(all_devs, key=lambda d: (0 if d in power_devs else 1, d))
        step = max(1, len(ordered) // quota)
        picked = [ordered[i * step] for i in range(quota)]
        unseen.update(picked)
    return unseen


def _row_to_ticket_input(row: pd.Series) -> TicketInput:
    pred_ts = datetime.fromisoformat(str(row["timestamp"]))
    if pred_ts.tzinfo is None:
        pred_ts = pred_ts.replace(tzinfo=timezone.utc)

    state = str(row["device_state"])
    tier = str(row["risk_tier"])
    is_conn = state in ("STALE_DATA", "OFFLINE_LONG")
    obs_ts = pred_ts - timedelta(hours=2) if is_conn else pred_ts - timedelta(minutes=5)
    fresh = "STALE" if is_conn else "CURRENT"

    ac_val = round(float(row["ac_voltage_mean_1h"]), 1)
    mains_h = round(float(row["time_since_mains_loss_hours"]), 1)
    is_batt = bool(row["is_battery_sensor_present"])
    batt_raw = row["battery_voltage_mean_1h"]
    if is_batt and pd.notna(batt_raw) and float(batt_raw) > 0.0:
        batt_meas = Measurement("battery_voltage", round(float(batt_raw), 2), "V", obs_ts, fresh)
    else:
        batt_meas = Measurement("battery_voltage", None, None, None, "UNAVAILABLE")

    measurements = [
        Measurement("ac_voltage", ac_val, "V", obs_ts, fresh),
        batt_meas,
        Measurement("time_since_mains_loss", mains_h, "hours", obs_ts, fresh),
    ]

    rules = json.loads(row["rules_triggered_json"])
    prob: Optional[float] = None if is_conn else round(float(row["calibrated_prob"]), 4)

    # Observed facts, hypotheses, and bounded actions derived deterministically
    # from the row's validated state/tier/telemetry — no invented claims.
    if is_conn:
        facts = [
            f"Device state is {state}; no fresh telemetry within the 60-minute freshness window."
        ]
        causes = [
            "Gateway cellular link or SIM connectivity interruption (unconfirmed).",
            "Site power interruption affecting the gateway supply (unconfirmed).",
        ]
        actions = [
            "inspect-gateway-power-and-wiring",
            "check-sim-and-network-signal",
            "verify-site-power-at-premises",
        ]
    elif state == "ONLINE_POWER_LOSS":
        facts = [
            f"Gateway reporting online in state ONLINE_POWER_LOSS with ac_voltage {ac_val} V "
            f"and time_since_mains_loss {mains_h} hours."
        ]
        causes = [
            "Upstream utility feeder outage (hypothesis only — unconfirmed).",
            "Site distribution-board input trip (hypothesis only — unconfirmed).",
        ]
        actions = [
            "inspect-distribution-board-and-breakers",
            "verify-mains-contactor-position",
            "check-inverter-and-ups-status",
        ]
    elif tier in ("HIGH", "CRITICAL"):
        facts = [
            f"Gateway online in state {state}; assigned risk tier is {tier} "
            f"({format_probability(prob)})."
        ]
        if mains_h > 0.0:
            causes = [
                "Recent AC voltage instability or ongoing mains loss (hypothesis only — unconfirmed)."
            ]
        else:
            causes = [
                "AC line voltage depression or historical instability signature (hypothesis only — unconfirmed)."
            ]
        actions = [
            "inspect-distribution-board-and-breakers",
            "check-inverter-and-ups-status",
            "monitor-ac-voltage-6h",
        ]
    elif tier == "MEDIUM":
        facts = [
            f"Gateway online in state {state}; assigned risk tier is MEDIUM "
            f"({format_probability(prob)})."
        ]
        causes = [
            "Mild AC voltage deviation on recent telemetry window (hypothesis only — unconfirmed)."
        ]
        actions = ["monitor-ac-voltage-6h", "schedule-battery-load-test-offpeak"]
    else:
        facts = [
            f"Gateway online in state {state}; telemetry within normal operating band."
        ]
        causes = ["No abnormal condition suspected; routine monitoring applies."]
        actions = ["continue-automated-telemetry-polling"]

    return TicketInput.build(
        device_id=str(row["device_id"]),
        device_name=str(row["device_name"]),
        bank=str(row["bank"]),
        prediction_timestamp_utc=pred_ts,
        model_version=str(row["model_version"]),
        policy_version=POLICY_VERSION,
        device_state=state,
        measurements=measurements,
        calibrated_probability=prob,
        risk_tier=tier,
        triggered_rules=rules,
        observed_facts=facts,
        possible_causes=causes,
        approved_investigation_actions=actions,
    )


def _completion_template_signature(ticket_text: str) -> str:
    """Normalize ticket text by stripping per-record identity/timestamp/hash lines
    to measure structural template repetition separately from exact completion text."""
    Variable_prefixes = (
        "Ticket ID:", "Branch Site:", "Device ID:", "Prediction Timestamp (UTC):",
        "Input Payload SHA-256:", "- ac_voltage:", "- battery_voltage:",
        "- time_since_mains_loss:", "24-Hour Outage Probability:",
        "- Gateway online in state", "- Gateway reporting online",
    )
    kept = [
        line for line in ticket_text.splitlines()
        if not line.strip().startswith(Variable_prefixes)
    ]
    return "\n".join(kept)


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _content_payload_hash(ti: TicketInput) -> str:
    """SHA-256 of canonical payload content excluding clock timestamps.

    Ensures two snapshots on the same device with identical state, tier,
    calibrated_probability, triggered_rules, and measurement values/units/
    freshness are treated as duplicate payloads even if their clock timestamps
    differ by 15 minutes.
    """
    content = {
        "device_id": ti.device_id,
        "device_name": ti.device_name,
        "bank": ti.bank,
        "model_version": ti.model_version,
        "policy_version": ti.policy_version,
        "device_state": ti.device_state,
        "measurements": [
            {
                "field": m.field,
                "value": m.value,
                "unit": m.unit,
                "freshness": m.freshness,
            }
            for m in sorted(ti.measurements, key=lambda x: x.field)
        ],
        "calibrated_probability": ti.calibrated_probability,
        "risk_tier": ti.risk_tier,
        "triggered_rules": sorted(ti.triggered_rules),
    }
    return _sha256_bytes(json.dumps(content, sort_keys=True).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def build_splits() -> Dict[str, Any]:
    inc_df = pd.read_parquet(INCIDENTS_PARQUET)
    rows_df = pd.read_parquet(ROWS_PARQUET)

    unseen_devices = _select_unseen_devices(inc_df)
    known_devices = set(inc_df["device_id"].unique()) - unseen_devices
    assert len(unseen_devices) == sum(UNSEEN_QUOTA_BY_BANK.values()) == 27
    assert len(known_devices) == 108
    assert not (unseen_devices & known_devices)

    # Assign each incident to a split (or PURGED_WINDOW with explicit reason).
    inc_splits: List[str] = []
    inc_reasons: List[str] = []
    for _, inc in inc_df.iterrows():
        dev = inc["device_id"]
        s = datetime.fromisoformat(inc["incident_start_utc"])
        e = datetime.fromisoformat(inc["incident_end_utc"])
        if dev in unseen_devices:
            inc_splits.append("UNSEEN_DEVICES")
            inc_reasons.append("whole-device holdout")
        else:
            if e <= TRAIN_CUTOFF_UTC:
                inc_splits.append("TRAIN")
                inc_reasons.append("known device; incident_end_utc <= train cutoff")
            elif s >= EVAL_START_UTC:
                inc_splits.append("FUTURE_INCIDENTS_KNOWN_DEVICES")
                inc_reasons.append("known device; incident_start_utc >= cutoff + 24h purge")
            elif s < TRAIN_CUTOFF_UTC and e > TRAIN_CUTOFF_UTC:
                inc_splits.append("EXCLUDED_STRADDLES_CUTOFF")
                inc_reasons.append("excluded: incident straddles train cutoff")
            else:
                inc_splits.append("EXCLUDED_PURGE_WINDOW")
                inc_reasons.append("excluded: incident starts inside 24h purge window")

    inc_df["split"] = inc_splits
    inc_df["split_reason"] = inc_reasons
    inc_df.to_parquet(INCIDENTS_PARQUET, index=False)

    inc_to_split = dict(zip(inc_df["incident_id"], inc_df["split"]))
    rows_df["split"] = rows_df["incident_id"].map(inc_to_split)

    # Representative snapshots per incident: up to 2 snapshots (first + peak-prob
    # if distinct timestamp/payload) for POWER/CONNECTIVITY incidents, and 1
    # snapshot (first) for NORMAL_SEGMENT incidents, so every eligible incident
    # is represented while keeping adjacent-snapshot redundancy bounded.
    rows_df["dt"] = pd.to_datetime(rows_df["timestamp"], utc=True)
    rows_df = rows_df.sort_values(["incident_id", "dt"]).reset_index(drop=True)

    candidate_rows: Dict[str, List[pd.Series]] = {
        "TRAIN": [],
        "FUTURE_INCIDENTS_KNOWN_DEVICES": [],
        "UNSEEN_DEVICES": [],
    }
    for inc_id, grp in rows_df.groupby("incident_id", sort=False):
        sp = grp["split"].iloc[0]
        if sp not in candidate_rows:
            continue
        first_row = grp.iloc[0]
        candidate_rows[sp].append(first_row)
        if len(grp) > 2:
            mid_row = grp.iloc[len(grp) // 2]
            candidate_rows[sp].append(mid_row)
        if grp["regime"].iloc[0] in ("POWER", "CONNECTIVITY") and len(grp) > 1:
            peak_idx = grp["calibrated_prob"].astype(float).idxmax()
            peak_row = grp.loc[peak_idx]
            candidate_rows[sp].append(peak_row)

    # Canonicalize, render, validate, and deduplicate per split.
    split_files = {
        "TRAIN": EVAL_DIR / "train.jsonl",
        "FUTURE_INCIDENTS_KNOWN_DEVICES": EVAL_DIR / "future_known_devices.jsonl",
        "UNSEEN_DEVICES": EVAL_DIR / "unseen_devices.jsonl",
    }
    manifest_files = {
        "TRAIN": EVAL_DIR / "manifest_train.json",
        "FUTURE_INCIDENTS_KNOWN_DEVICES": EVAL_DIR / "manifest_future_known_devices.json",
        "UNSEEN_DEVICES": EVAL_DIR / "manifest_unseen_devices.json",
    }
    purposes = {
        "TRAIN": (
            "Training/reference split on known devices (108 sites) strictly on or "
            "before 2026-10-08T00:00:00Z. Grouped by incident_id; deduplicated by "
            "canonical payload hash."
        ),
        "FUTURE_INCIDENTS_KNOWN_DEVICES": (
            "Clean temporal evaluation split on known devices: incidents starting "
            "at or after 2026-10-09T00:00:00Z (after a 24-hour purge window). "
            "Tests adaptation to future incidents at known sites. Devices are "
            "known (shared with TRAIN); incidents and payloads are strictly disjoint."
        ),
        "UNSEEN_DEVICES": (
            "Clean spatial evaluation split on 27 whole held-out devices across "
            "BOI (20), SBI (4), BOB (2), CANARA (1). Zero devices, incidents, or "
            "payloads shared with TRAIN or FUTURE_INCIDENTS_KNOWN_DEVICES. Tests "
            "generalization to unseen branch sites."
        ),
    }

    train_payload_hashes: set[str] = set()
    train_content_hashes: set[str] = set()
    split_stats: Dict[str, Any] = {}

    for sp in ("TRAIN", "FUTURE_INCIDENTS_KNOWN_DEVICES", "UNSEEN_DEVICES"):
        seen_hashes: set[str] = set()
        seen_content_hashes: set[str] = set()
        within_split_dups = 0
        cross_train_dups = 0
        records_out: List[Dict[str, Any]] = []
        render_failures = 0
        validator_failures = 0

        for idx, row in enumerate(candidate_rows[sp]):
            try:
                ti = _row_to_ticket_input(row)
                rendered = render_ticket(ti)
            except Exception:
                render_failures += 1
                continue
            p_hash = rendered.payload_hash
            c_hash = _content_payload_hash(ti)
            if p_hash in seen_hashes or c_hash in seen_content_hashes:
                within_split_dups += 1
                continue
            if sp != "TRAIN" and (p_hash in train_payload_hashes or c_hash in train_content_hashes):
                cross_train_dups += 1
                continue
            seen_hashes.add(p_hash)
            seen_content_hashes.add(c_hash)
            if sp == "TRAIN":
                train_payload_hashes.add(p_hash)
                train_content_hashes.add(c_hash)

            vres = validate_candidate(rendered.text, ti)
            if not vres.is_valid:
                validator_failures += 1

            canonical_payload = {
                "device_id": ti.device_id,
                "device_name": ti.device_name,
                "bank": ti.bank,
                "prediction_timestamp_utc": ti.prediction_timestamp_utc.isoformat(),
                "model_version": ti.model_version,
                "policy_version": ti.policy_version,
                "device_state": ti.device_state,
                "measurements": [
                    {
                        "field": m.field,
                        "value": m.value,
                        "unit": m.unit,
                        "observed_at": m.observed_at.isoformat() if m.observed_at else None,
                        "freshness": m.freshness,
                    }
                    for m in sorted(ti.measurements, key=lambda x: x.field)
                ],
                "calibrated_probability": ti.calibrated_probability,
                "risk_tier": ti.risk_tier,
                "triggered_rules": sorted(ti.triggered_rules),
                "observed_facts": list(ti.observed_facts),
                "possible_causes": list(ti.possible_causes),
                "approved_investigation_actions": list(ti.approved_investigation_actions),
            }

            inc_row = inc_df[inc_df["incident_id"] == row["incident_id"]].iloc[0]
            rec = {
                "record_id": f"{sp[:4]}-{len(records_out)+1:04d}",
                "split": sp,
                "incident_id": str(row["incident_id"]),
                "incident_regime": str(inc_row["regime"]),
                "incident_start_utc": str(inc_row["incident_start_utc"]),
                "incident_end_utc": str(inc_row["incident_end_utc"]),
                "device_id": ti.device_id,
                "device_name": ti.device_name,
                "bank": ti.bank,
                "prediction_timestamp_utc": ti.prediction_timestamp_utc.isoformat(),
                "device_state": ti.device_state,
                "risk_tier": ti.risk_tier,
                "calibrated_probability": ti.calibrated_probability,
                "triggered_rules": sorted(ti.triggered_rules),
                "policy_version": POLICY_VERSION,
                "ticket_id": rendered.ticket_id,
                "canonical_payload_hash": p_hash,
                "content_payload_hash": c_hash,
                "canonical_payload": canonical_payload,
                "deterministic_ticket": rendered.text,
                "completion_template_signature_hash": _sha256_bytes(
                    _completion_template_signature(rendered.text).encode("utf-8")
                ),
                "validator_accepted": vres.is_valid,
                "validator_violations": vres.violations,
            }
            records_out.append(rec)

        data_path = split_files[sp]
        with open(data_path, "w", encoding="utf-8") as f:
            for r in records_out:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        data_sha = _sha256_file(data_path)

        unique_payloads = len({r["canonical_payload_hash"] for r in records_out})
        unique_completions = len({r["deterministic_ticket"] for r in records_out})
        unique_template_sigs = len({r["completion_template_signature_hash"] for r in records_out})
        dev_ids = sorted({r["device_id"] for r in records_out})
        inc_ids = sorted({r["incident_id"] for r in records_out})
        ts_list = sorted(r["prediction_timestamp_utc"] for r in records_out)
        tiers = dict(Counter(r["risk_tier"] for r in records_out))
        states = dict(Counter(r["device_state"] for r in records_out))
        banks = dict(Counter(r["bank"] for r in records_out))
        conn_vs_power = {
            "connectivity_tickets": sum(
                1 for r in records_out if r["device_state"] in ("STALE_DATA", "OFFLINE_LONG")
            ),
            "power_diagnostic_tickets": sum(
                1 for r in records_out if r["device_state"] in ("ONLINE_OK", "ONLINE_POWER_LOSS")
            ),
        }

        manifest_body = {
            "manifest_version": "stage-d-v1",
            "frozen": True,
            "freeze_notice": (
                "Frozen Stage D manifest. Future model selection must not tune on "
                "this split; any change requires a new manifest_version and an "
                "explicit documented reason."
            ),
            "split_name": sp,
            "purpose": purposes[sp],
            "policy_version": POLICY_VERSION,
            "creation_command": CREATION_COMMAND,
            "train_cutoff_utc": TRAIN_CUTOFF_UTC.isoformat(),
            "purge_window_hours": 24,
            "eval_start_utc": EVAL_START_UTC.isoformat(),
            "data_file": str(data_path.relative_to(ROOT)).replace("\\", "/"),
            "data_file_sha256": data_sha,
            "record_count": len(records_out),
            "unique_payload_count": unique_payloads,
            "unique_completion_count": unique_completions,
            "unique_template_signature_count": unique_template_sigs,
            "within_split_duplicate_payloads_removed": within_split_dups,
            "cross_train_duplicate_payloads_removed": cross_train_dups,
            "render_failures": render_failures,
            "validator_failures": validator_failures,
            "device_count": len(dev_ids),
            "incident_count": len(inc_ids),
            "date_range_utc": {
                "min_prediction_timestamp_utc": ts_list[0] if ts_list else None,
                "max_prediction_timestamp_utc": ts_list[-1] if ts_list else None,
            },
            "tier_distribution": tiers,
            "device_state_distribution": states,
            "bank_distribution": banks,
            "ticket_type_distribution": conn_vs_power,
            "record_ids": [r["record_id"] for r in records_out],
            "device_ids": dev_ids,
            "incident_ids": inc_ids,
        }
        canonical_manifest_bytes = json.dumps(
            manifest_body, sort_keys=True, indent=2
        ).encode("utf-8")
        manifest_sha = _sha256_bytes(canonical_manifest_bytes)
        manifest_with_hash = dict(manifest_body)
        manifest_with_hash["manifest_sha256"] = manifest_sha

        m_path = manifest_files[sp]
        with open(m_path, "w", encoding="utf-8") as f:
            json.dump(manifest_with_hash, f, indent=2, sort_keys=True)

        split_stats[sp] = {
            "record_count": len(records_out),
            "unique_payload_count": unique_payloads,
            "unique_completion_count": unique_completions,
            "unique_template_signature_count": unique_template_sigs,
            "within_split_duplicates_removed": within_split_dups,
            "cross_train_duplicates_removed": cross_train_dups,
            "device_count": len(dev_ids),
            "incident_count": len(inc_ids),
            "render_success_rate": 1.0 if render_failures == 0 else round(
                len(records_out) / (len(records_out) + render_failures), 4
            ),
            "validator_acceptance_rate": round(
                sum(1 for r in records_out if r["validator_accepted"]) / max(1, len(records_out)), 4
            ),
            "tier_distribution": tiers,
            "device_state_distribution": states,
            "bank_distribution": banks,
            "ticket_type_distribution": conn_vs_power,
            "date_range_utc": manifest_body["date_range_utc"],
            "data_file_sha256": data_sha,
            "manifest_sha256": manifest_sha,
        }

    # Alert-volume metric on the full 149,175-row shadow window under ticket-policy-v1:
    # Under policy, an actionable power alert is risk_tier in ('HIGH', 'CRITICAL')
    # on an online device; consecutive alert rows on the same device within 6h are
    # one alert episode (POWER_INCIDENT with HIGH/CRITICAL).
    span_days = (
        pd.to_datetime(rows_df["timestamp"], utc=True).max()
        - pd.to_datetime(rows_df["timestamp"], utc=True).min()
    ).total_seconds() / 86400.0
    span_weeks = span_days / 7.0
    n_devices_total = int(rows_df["device_id"].nunique())
    power_alert_rows = rows_df[rows_df["risk_tier"].isin(["HIGH", "CRITICAL"])]
    power_alert_incidents = inc_df[
        inc_df["risk_tiers_seen"].str.contains("HIGH|CRITICAL", regex=True)
    ]
    conn_incidents = inc_df[inc_df["regime"] == "CONNECTIVITY"]

    exclusion_counts = inc_df["split"].value_counts().to_dict()
    excluded_rows_count = int((~rows_df["split"].isin(split_files.keys())).sum())

    summary = {
        "policy_version": POLICY_VERSION,
        "train_cutoff_utc": TRAIN_CUTOFF_UTC.isoformat(),
        "purge_window_hours": 24,
        "eval_start_utc": EVAL_START_UTC.isoformat(),
        "incident_split_counts": exclusion_counts,
        "excluded_incidents_detail": {
            "straddles_cutoff": int((inc_df["split"] == "EXCLUDED_STRADDLES_CUTOFF").sum()),
            "inside_24h_purge_window": int((inc_df["split"] == "EXCLUDED_PURGE_WINDOW").sum()),
            "excluded_15min_rows_total": excluded_rows_count,
        },
        "splits": split_stats,
        "fleet_alert_volume_under_policy_v1": {
            "window_days": round(span_days, 2),
            "window_weeks": round(span_weeks, 4),
            "fleet_devices": n_devices_total,
            "full_window_tier_row_counts": rows_df["risk_tier"].value_counts().to_dict(),
            "high_or_critical_power_alert_rows": int(len(power_alert_rows)),
            "high_or_critical_power_alert_episodes": int(len(power_alert_incidents)),
            "power_alert_episodes_per_device_per_week": round(
                len(power_alert_incidents) / (n_devices_total * span_weeks), 4
            ),
            "connectivity_episodes_total": int(len(conn_incidents)),
            "connectivity_episodes_per_device_per_week": round(
                len(conn_incidents) / (n_devices_total * span_weeks), 4
            ),
        },
    }
    with open(EVAL_DIR / "split_build_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    build_splits()
    sys.exit(0)
