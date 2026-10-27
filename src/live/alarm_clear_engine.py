"""src/live/alarm_clear_engine.py
ThingsBoard Alarm Clearance and Lifecycle Management Engine.

Provides automated clearing of active AI alarms and synchronization
of recovered operational states across ThingsBoard SHARED_SCOPE,
SERVER_SCOPE, and timeseries streams.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

import requests

from src.live.recovery_gate import RecoveryEvaluation

logger = logging.getLogger("alarm_clear_engine")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEBOUNCE_STATE_PATH = Path(
    os.getenv(
        "RECOVERY_DEBOUNCE_STATE_PATH",
        str(ROOT / "reports/recovery_debounce_state.json"),
    )
)
DEFAULT_REQUIRED_CYCLES = 3
DEFAULT_CYCLE_INTERVAL_MINUTES = 15

# A single sag/offline observation resets the confirmation counter, even if the
# aggregate recovery gate still considers the device recovered.
AC_SAG_THRESHOLD_V = 170.0
BATTERY_SAG_THRESHOLD_V = 11.5

STATUS_AWAITING = "AWAITING_NOMINAL"
STATUS_PENDING = "PENDING_CONFIRMATION"
STATUS_CONFIRMED = "CONFIRMED_RECOVERED"
STATUS_RESET = "RESET_FAULT"
STATUS_FAULT = "FAULT_CONFIRMED"


AI_ALARM_TYPES = {
    "AI_CRITICAL_ANOMALY",
    "AI_PREDICTIVE_OUTAGE_RISK",
    "AI_OPERATIONAL_RISK",
    "AI Operational Risk Alert"
}


def get_active_ai_alarms(
    device_id: str,
    host: str,
    token: str,
    timeout: float = 6.0
) -> List[Dict[str, Any]]:
    """Retrieve active AI alarms for a device from ThingsBoard."""
    headers = {"X-Authorization": f"Bearer {token}"}
    host = host.rstrip("/")
    url = f"{host}/api/alarm/DEVICE/{device_id}?searchStatus=ACTIVE&pageSize=50&page=0"

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.debug(f"Failed to fetch alarms for {device_id}: {resp.status_code}")
            return []

        data = resp.json()
        alarms = data.get("data", [])
        ai_alarms = [
            a for a in alarms
            if a.get("type") in AI_ALARM_TYPES or "AI_" in a.get("type", "")
        ]
        return ai_alarms
    except Exception as e:
        logger.debug(f"Error querying active alarms for {device_id}: {e}")
        return []


def clear_ai_alarm(
    alarm_id: str,
    host: str,
    token: str,
    timeout: float = 6.0
) -> bool:
    """Clear an active alarm on ThingsBoard."""
    headers = {"X-Authorization": f"Bearer {token}"}
    host = host.rstrip("/")
    url = f"{host}/api/alarm/{alarm_id}/clear"

    try:
        resp = requests.post(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            logger.info(f"Successfully cleared ThingsBoard alarm {alarm_id}")
            return True
        else:
            logger.warning(f"Failed to clear alarm {alarm_id}: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.warning(f"Exception clearing alarm {alarm_id}: {e}")
        return False


def sync_recovery_attributes(
    device_id: str,
    eval_res: RecoveryEvaluation,
    host: str,
    token: str,
    timeout: float = 6.0
) -> bool:
    """Sync recovered health attributes to ThingsBoard SHARED_SCOPE, SERVER_SCOPE, and timeseries."""
    headers = {"X-Authorization": f"Bearer {token}"}
    host = host.rstrip("/")
    now_iso = datetime.now(timezone.utc).isoformat()

    recovery_payload = {
        "health_status": "HEALTHY",
        "risk_tier": "SERVICED_RECOVERED",
        "anomaly_score": 0.05,
        "predicted_outage_risk_24h": 0.02,
        "primary_issue": "Hardware restored; real-time telemetry verified nominal",
        "recommended_action": "None — Post-service operational stability verified",
        "model_evaluated_at": now_iso,
        "last_recovery_verified_at": now_iso,
        "vitals_summary": str(eval_res.vitals)
    }

    success = True
    # 1. SHARED_SCOPE
    try:
        r1 = requests.post(
            f"{host}/api/plugins/telemetry/DEVICE/{device_id}/SHARED_SCOPE",
            json=recovery_payload,
            headers=headers,
            timeout=timeout
        )
        if r1.status_code != 200:
            success = False
    except Exception as e:
        logger.debug(f"Failed to update SHARED_SCOPE for {device_id}: {e}")
        success = False

    # 2. SERVER_SCOPE
    try:
        r2 = requests.post(
            f"{host}/api/plugins/telemetry/DEVICE/{device_id}/SERVER_SCOPE",
            json=recovery_payload,
            headers=headers,
            timeout=timeout
        )
        if r2.status_code != 200:
            success = False
    except Exception as e:
        logger.debug(f"Failed to update SERVER_SCOPE for {device_id}: {e}")
        success = False

    # 3. Timeseries
    try:
        r3 = requests.post(
            f"{host}/api/plugins/telemetry/DEVICE/{device_id}/timeseries/any",
            json=recovery_payload,
            headers=headers,
            timeout=timeout
        )
        if r3.status_code != 200:
            success = False
    except Exception as e:
        logger.debug(f"Failed to update timeseries for {device_id}: {e}")
        success = False

    return success


def process_device_recovery_lifecycle(
    device_id: str,
    eval_res: RecoveryEvaluation,
    host: str,
    token: str
) -> Dict[str, Any]:
    """Execute complete recovery lifecycle: clear active alarms and sync healthy attributes."""
    cleared_alarm_ids = []
    if eval_res.is_recovered:
        active_alarms = get_active_ai_alarms(device_id, host, token)
        for alarm in active_alarms:
            alarm_id = alarm.get("id", {}).get("id")
            if alarm_id:
                if clear_ai_alarm(alarm_id, host, token):
                    cleared_alarm_ids.append(alarm_id)

        sync_recovery_attributes(device_id, eval_res, host, token)

    return {
        "device_id": device_id,
        "is_recovered": eval_res.is_recovered,
        "cleared_alarms_count": len(cleared_alarm_ids),
        "cleared_alarm_ids": cleared_alarm_ids
    }


# ===========================================================================
# Debounced alarm-recovery gate (3 consecutive 15-minute nominal cycles)
# ===========================================================================
@dataclass
class DebounceDecision:
    """Outcome of one debounce-gate evaluation for a single device."""

    device_id: str
    device_name: str
    bank: str
    status: str
    eligible_for_clear: bool
    consecutive_nominal_cycles: int
    required_cycles: int
    cycle_interval_minutes: float
    elapsed_minutes: Optional[float]
    reason: str
    first_flagged_at: Optional[str] = None
    first_nominal_at: Optional[str] = None
    last_evaluated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "bank": self.bank,
            "status": self.status,
            "eligible_for_clear": self.eligible_for_clear,
            "consecutive_nominal_cycles": self.consecutive_nominal_cycles,
            "required_cycles": self.required_cycles,
            "cycle_interval_minutes": self.cycle_interval_minutes,
            "elapsed_minutes": (
                None if self.elapsed_minutes is None else round(self.elapsed_minutes, 2)
            ),
            "reason": self.reason,
            "first_flagged_at": self.first_flagged_at,
            "first_nominal_at": self.first_nominal_at,
            "last_evaluated_at": self.last_evaluated_at,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).astimezone(timezone.utc).isoformat()


def _empty_debounce_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "required_cycles": DEFAULT_REQUIRED_CYCLES,
        "cycle_interval_minutes": DEFAULT_CYCLE_INTERVAL_MINUTES,
        "min_elapsed_minutes": (
            DEFAULT_REQUIRED_CYCLES - 1
        ) * DEFAULT_CYCLE_INTERVAL_MINUTES,
        "updated_at": _iso(),
        "devices": {},
        "mttr_records": [],
    }


def load_debounce_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the persisted debounce state (never raises on corrupt/missing file)."""
    target = Path(path) if path is not None else DEFAULT_DEBOUNCE_STATE_PATH
    state = _empty_debounce_state()
    if not target.exists():
        return state
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Could not read debounce state {target}: {exc}; starting fresh.")
        return state
    if not isinstance(loaded, dict):
        logger.warning(f"Debounce state {target} is not an object; starting fresh.")
        return state

    state.update({k: v for k, v in loaded.items() if k in state})
    if not isinstance(state.get("devices"), dict):
        state["devices"] = {}
    if not isinstance(state.get("mttr_records"), list):
        state["mttr_records"] = []
    return state


def save_debounce_state(
    state: Dict[str, Any],
    path: Optional[Path] = None,
) -> Path:
    """Persist debounce state as JSON (atomic replace)."""
    target = Path(path) if path is not None else DEFAULT_DEBOUNCE_STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _iso()
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


def _ensure_device_record(
    state: Dict[str, Any],
    device_id: str,
    device_name: Optional[str],
    bank: Optional[str],
    now_iso: str,
    first_flagged_at: Optional[str] = None,
) -> Dict[str, Any]:
    devices = state.setdefault("devices", {})
    record = devices.get(device_id)
    if record is None:
        record = {
            "device_id": device_id,
            "device_name": device_name or device_id,
            "bank": bank or "UNKNOWN",
            "consecutive_nominal_cycles": 0,
            "first_flagged_at": first_flagged_at or now_iso,
            "first_nominal_at": None,
            "last_evaluated_at": None,
            "last_status": STATUS_AWAITING,
            "last_reason": "No nominal cycle observed yet.",
            "cleared_at": None,
            "last_cleared_at": None,
            "mttr_recorded": False,
            "history": [],
        }
        devices[device_id] = record
    else:
        if device_name:
            record["device_name"] = device_name
        if bank and bank != "UNKNOWN":
            record["bank"] = bank
        if first_flagged_at and not record.get("first_flagged_at"):
            record["first_flagged_at"] = first_flagged_at
        record.setdefault("consecutive_nominal_cycles", 0)
        record.setdefault("history", [])
        record.setdefault("mttr_recorded", False)
    return record


def voltage_sag_detected(eval_res: RecoveryEvaluation) -> bool:
    """True when live vitals show an AC or battery voltage sag."""
    vitals = eval_res.vitals or {}
    ac_voltage = vitals.get("ac_voltage")
    battery_voltage = vitals.get("battery_voltage")
    if isinstance(ac_voltage, (int, float)) and ac_voltage < AC_SAG_THRESHOLD_V:
        return True
    if isinstance(battery_voltage, (int, float)) and battery_voltage < BATTERY_SAG_THRESHOLD_V:
        return True
    if vitals.get("mains_on") is False:
        return True
    return False


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse ISO strings / epoch seconds / epoch milliseconds into aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def compute_mttr_minutes(started_at: Any, recovered_at: Any) -> Optional[float]:
    """Minutes between the incident start and confirmed recovery."""
    start = _parse_timestamp(started_at)
    end = _parse_timestamp(recovered_at)
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds() / 60.0), 2)


def evaluate_debounce_gate(
    state: Dict[str, Any],
    device_id: str,
    eval_res: RecoveryEvaluation,
    device_name: Optional[str] = None,
    bank: Optional[str] = None,
    now: Optional[datetime] = None,
    required_cycles: Optional[int] = None,
    cycle_interval_minutes: Optional[float] = None,
    first_flagged_at: Optional[str] = None,
) -> DebounceDecision:
    """Advance the debounce state machine by one probe cycle.

    A device becomes eligible for alarm clearance only after
    ``required_cycles`` consecutive nominal probes *and* at least
    ``(required_cycles - 1) * cycle_interval_minutes`` of wall-clock time have
    elapsed. Any fault, offline observation, or voltage sag resets the counter.
    """
    now = now or _utc_now()
    now_iso = _iso(now)
    required = int(required_cycles or state.get("required_cycles") or DEFAULT_REQUIRED_CYCLES)
    interval = float(
        cycle_interval_minutes
        or state.get("cycle_interval_minutes")
        or DEFAULT_CYCLE_INTERVAL_MINUTES
    )
    min_elapsed = float(
        state.get("min_elapsed_minutes", (required - 1) * interval)
    )

    record = _ensure_device_record(
        state, device_id, device_name, bank, now_iso, first_flagged_at=first_flagged_at
    )
    record["required_cycles"] = required
    record["cycle_interval_minutes"] = interval

    sag = voltage_sag_detected(eval_res)
    nominal = bool(eval_res.is_recovered) and not sag

    if nominal:
        previous = int(record.get("consecutive_nominal_cycles", 0))
        record["consecutive_nominal_cycles"] = previous + 1
        if previous == 0 or not record.get("first_nominal_at"):
            record["first_nominal_at"] = now_iso

        first_nominal = _parse_timestamp(record.get("first_nominal_at"))
        elapsed = (
            (now - first_nominal).total_seconds() / 60.0
            if first_nominal is not None
            else 0.0
        )
        consecutive = int(record["consecutive_nominal_cycles"])

        if consecutive >= required and elapsed >= min_elapsed:
            status = STATUS_CONFIRMED
            eligible = True
            reason = (
                f"{consecutive} consecutive nominal cycles over {elapsed:.0f} minutes "
                f"(required {required} cycles / {min_elapsed:.0f} minutes)."
            )
        else:
            status = STATUS_PENDING
            eligible = False
            if consecutive >= required:
                reason = (
                    f"{consecutive} nominal cycles observed but only {elapsed:.0f} of "
                    f"{min_elapsed:.0f} required minutes elapsed."
                )
            else:
                reason = (
                    f"Nominal cycle {consecutive}/{required} confirmed "
                    f"({elapsed:.0f} minutes into the debounce window)."
                )
    else:
        previous = int(record.get("consecutive_nominal_cycles", 0))
        record["consecutive_nominal_cycles"] = 0
        record["first_nominal_at"] = None
        record["mttr_recorded"] = False
        elapsed = 0.0
        consecutive = 0
        eligible = False
        status = STATUS_RESET if previous > 0 else STATUS_FAULT
        if sag:
            reason = f"Voltage sag/offline observation reset the confirmation counter. {eval_res.summary_reason}"
        else:
            reason = f"Non-nominal cycle reset the confirmation counter. {eval_res.summary_reason}"

    record["last_evaluated_at"] = now_iso
    record["last_status"] = status
    record["last_reason"] = reason

    history = record.setdefault("history", [])
    history.append(
        {
            "evaluated_at": now_iso,
            "is_recovered": bool(eval_res.is_recovered),
            "voltage_sag": sag,
            "consecutive_nominal_cycles": consecutive,
            "status": status,
        }
    )
    if len(history) > 12:
        del history[:-12]

    return DebounceDecision(
        device_id=device_id,
        device_name=str(record.get("device_name") or device_id),
        bank=str(record.get("bank") or "UNKNOWN"),
        status=status,
        eligible_for_clear=eligible,
        consecutive_nominal_cycles=consecutive,
        required_cycles=required,
        cycle_interval_minutes=interval,
        elapsed_minutes=elapsed,
        reason=reason,
        first_flagged_at=record.get("first_flagged_at"),
        first_nominal_at=record.get("first_nominal_at"),
        last_evaluated_at=now_iso,
    )


def _earliest_alarm_start(alarms: List[Dict[str, Any]]) -> Optional[datetime]:
    """Earliest start timestamp across active alarms (startTs/createdTime)."""
    candidates: List[datetime] = []
    for alarm in alarms:
        for key in ("startTs", "createdTime", "createdAt", "startedAt"):
            parsed = _parse_timestamp(alarm.get(key))
            if parsed is not None:
                candidates.append(parsed)
                break
    return min(candidates) if candidates else None


def clear_confirmed_recovery(
    device_id: str,
    eval_res: RecoveryEvaluation,
    host: str,
    token: str,
    device_name: Optional[str] = None,
    bank: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
    required_cycles: Optional[int] = None,
    cycle_interval_minutes: Optional[float] = None,
    first_flagged_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Debounced single clearance authority used by every recovery path.

    Alarms are cleared and ``SERVICED_RECOVERED`` attributes written only when
    the device has passed the 3-cycle / 45-minute confirmation gate.
    """
    owns_state = state is None
    state = state if state is not None else load_debounce_state(state_path)
    now = now or _utc_now()

    decision = evaluate_debounce_gate(
        state=state,
        device_id=device_id,
        eval_res=eval_res,
        device_name=device_name,
        bank=bank,
        now=now,
        required_cycles=required_cycles,
        cycle_interval_minutes=cycle_interval_minutes,
        first_flagged_at=first_flagged_at,
    )

    result: Dict[str, Any] = decision.to_dict()
    result.update(
        {
            "cleared_alarms_count": 0,
            "cleared_alarm_ids": [],
            "mttr_minutes": None,
            "mttr_started_at": None,
            "recovery_confirmed": False,
            "attributes_synced": False,
        }
    )

    if not decision.eligible_for_clear:
        if owns_state:
            save_debounce_state(state, state_path)
        return result

    record = state["devices"][device_id]
    record["last_clear_attempt_at"] = _iso(now)

    active_alarms = get_active_ai_alarms(device_id, host, token)
    started_at = _earliest_alarm_start(active_alarms)
    if started_at is None:
        started_at = _parse_timestamp(record.get("first_flagged_at"))

    cleared_ids: List[str] = []
    for alarm in active_alarms:
        alarm_id = (alarm.get("id") or {}).get("id")
        if alarm_id and clear_ai_alarm(alarm_id, host, token):
            cleared_ids.append(alarm_id)

    synced = sync_recovery_attributes(device_id, eval_res, host, token)
    mttr = compute_mttr_minutes(started_at, now)

    record["cleared_at"] = _iso(now)
    record["last_cleared_at"] = record["cleared_at"]
    record["last_status"] = STATUS_CONFIRMED

    if not record.get("mttr_recorded"):
        state.setdefault("mttr_records", []).append(
            {
                "device_id": device_id,
                "device_name": decision.device_name,
                "bank": decision.bank,
                "started_at": started_at.isoformat() if started_at else None,
                "recovered_at": _iso(now),
                "mttr_minutes": mttr,
                "cleared_alarms_count": len(cleared_ids),
            }
        )
        record["mttr_recorded"] = True

    result.update(
        {
            "cleared_alarms_count": len(cleared_ids),
            "cleared_alarm_ids": cleared_ids,
            "mttr_minutes": mttr,
            "mttr_started_at": started_at.isoformat() if started_at else None,
            "recovery_confirmed": True,
            "attributes_synced": bool(synced),
        }
    )

    logger.info(
        f"✅ Confirmed recovery for {decision.device_name} ({device_id}) after "
        f"{decision.consecutive_nominal_cycles} nominal cycles; cleared "
        f"{len(cleared_ids)} alarm(s); MTTR={mttr} min."
    )

    if owns_state:
        save_debounce_state(state, state_path)
    return result


def pending_recovery_devices(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Devices that still need probing until their debounce window completes."""
    pending: List[Dict[str, Any]] = []
    for device_id, record in (state.get("devices") or {}).items():
        if record.get("cleared_at"):
            continue
        status = record.get("last_status")
        if status in (STATUS_CONFIRMED,):
            continue
        pending.append(
            {
                "device_id": device_id,
                "device_name": record.get("device_name") or device_id,
                "bank": record.get("bank") or "UNKNOWN",
                "consecutive_nominal_cycles": int(
                    record.get("consecutive_nominal_cycles", 0)
                ),
                "first_flagged_at": record.get("first_flagged_at"),
                "last_status": status,
            }
        )
    return pending


def summarize_mttr_by_bank(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """MTTR cohorts by bank (BOI / SBI / BOB / Canara) plus a fleet rollup."""
    buckets: Dict[str, List[float]] = {}
    for record in state.get("mttr_records") or []:
        mttr = record.get("mttr_minutes")
        if not isinstance(mttr, (int, float)):
            continue
        bank = str(record.get("bank") or "UNKNOWN").upper()
        buckets.setdefault(bank, []).append(float(mttr))
        buckets.setdefault("ALL", []).append(float(mttr))

    summary: Dict[str, Dict[str, Any]] = {}
    for bank, values in buckets.items():
        ordered = sorted(values)
        summary[bank] = {
            "recoveries": len(ordered),
            "mean_mttr_minutes": round(sum(ordered) / len(ordered), 2),
            "median_mttr_minutes": round(float(median(ordered)), 2),
            "max_mttr_minutes": round(ordered[-1], 2),
        }
    return summary


def build_recovery_dispatch_payload(
    cleared_devices: List[Dict[str, Any]],
    state: Optional[Dict[str, Any]] = None,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build Slack Block-Kit and email payloads for confirmed recoveries."""
    generated_at = generated_at or _utc_now()
    stamp = generated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    devices = [d for d in cleared_devices if d.get("recovery_confirmed")]

    mttr_values = [
        float(d["mttr_minutes"])
        for d in devices
        if isinstance(d.get("mttr_minutes"), (int, float))
    ]
    mean_mttr = round(sum(mttr_values) / len(mttr_values), 2) if mttr_values else None
    bank_summary = summarize_mttr_by_bank(state or {})

    lines = [
        f"• {d.get('device_name', d.get('device_id'))} ({d.get('bank', 'UNKNOWN')}) — "
        f"MTTR {d.get('mttr_minutes', 'n/a')} min, "
        f"{d.get('cleared_alarms_count', 0)} alarm(s) cleared"
        for d in devices
    ]
    summary_line = (
        f"{len(devices)} branch(es) confirmed recovered after 3 consecutive "
        f"15-minute nominal cycles"
    )
    if mean_mttr is not None:
        summary_line += f" · mean MTTR {mean_mttr} min"

    slack_blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ Debounced Branch Recovery Confirmed"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{summary_line}*\n" + "\n".join(lines)},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Gate: 3 × 15-min nominal cycles · Generated {stamp} · "
                        "Alarms cleared and SERVICED_RECOVERED attributes synced."
                    ),
                }
            ],
        },
    ]

    email_lines = [
        summary_line,
        "",
        *lines,
        "",
        "Confirmation policy: alarms are cleared only after three consecutive",
        "15-minute nominal telemetry cycles with no offline event or voltage sag.",
        f"Generated: {stamp}",
    ]
    email_html = (
        "<h2>Branch recovery confirmed</h2>"
        f"<p>{summary_line}</p><ul>"
        + "".join(f"<li>{line.lstrip('• ')}</li>" for line in lines)
        + "</ul><p>Gate: 3 × 15-minute nominal telemetry cycles.</p>"
    )

    return {
        "generated_at": _iso(generated_at),
        "confirmed_count": len(devices),
        "devices": devices,
        "mean_mttr_minutes": mean_mttr,
        "mttr_by_bank": bank_summary,
        "slack": {
            "text": f"✅ {summary_line}",
            "blocks": slack_blocks,
        },
        "email": {
            "subject": f"✅ {len(devices)} branch(es) confirmed recovered — {stamp}",
            "text": "\n".join(email_lines),
            "html": email_html,
        },
    }


__all__ = [
    "AI_ALARM_TYPES",
    "DebounceDecision",
    "build_recovery_dispatch_payload",
    "clear_ai_alarm",
    "clear_confirmed_recovery",
    "compute_mttr_minutes",
    "evaluate_debounce_gate",
    "get_active_ai_alarms",
    "load_debounce_state",
    "pending_recovery_devices",
    "process_device_recovery_lifecycle",
    "save_debounce_state",
    "summarize_mttr_by_bank",
    "sync_recovery_attributes",
    "voltage_sag_detected",
]
