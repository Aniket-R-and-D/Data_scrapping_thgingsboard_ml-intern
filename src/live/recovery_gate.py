"""src/live/recovery_gate.py
Real-Time Telemetry & Attribute Post-Fix Recovery Gate.

Evaluates fresh operational telemetry and server attributes from ThingsBoard
to determine whether a device that was historically flagged as CRITICAL_FAULT
or HIGH_RISK has been physically serviced, repaired, or brought back online.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

logger = logging.getLogger("recovery_gate")


@dataclass
class RecoveryEvaluation:
    device_id: str
    is_recovered: bool
    confidence: float
    status_tier: str  # "SERVICED_RECOVERED" vs "UNVERIFIED" vs "CONFIRMED_FAULT"
    summary_reason: str
    vitals: Dict[str, Any] = field(default_factory=dict)
    active_alarms_to_clear: List[str] = field(default_factory=list)
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "is_recovered": self.is_recovered,
            "confidence": round(self.confidence, 4),
            "status_tier": self.status_tier,
            "summary_reason": self.summary_reason,
            "vitals": self.vitals,
            "active_alarms_to_clear": self.active_alarms_to_clear,
            "evaluated_at": self.evaluated_at,
        }


def _safe_json_parse(value: Any) -> Any:
    """Safely parse nested JSON string or return raw value."""
    if isinstance(value, str):
        trimmed = value.strip()
        if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
            try:
                return json.loads(trimmed)
            except Exception:
                return value
    return value


def _to_bool(val: Any) -> Optional[bool]:
    """Coerce various boolean representations."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "on", "yes", "active"):
        return True
    if s in ("false", "0", "off", "no", "inactive", "n/a", "null", "none"):
        return False
    return None


def _to_float(val: Any) -> Optional[float]:
    """Coerce numeric values safely."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def evaluate_telemetry_recovery(
    device_id: str,
    timeseries: Dict[str, Any],
    attributes: Dict[str, Any],
    now_ts_ms: Optional[int] = None,
    max_inactivity_minutes: float = 60.0
) -> RecoveryEvaluation:
    """Evaluate whether real-time telemetry and attributes confirm post-fix recovery.
    
    Args:
        device_id: UUID of the device
        timeseries: Dictionary of latest timeseries keys -> [{'ts': ..., 'value': ...}]
        attributes: Dictionary of server/shared attributes
        now_ts_ms: Current timestamp in epoch ms (defaults to time.time() * 1000)
        max_inactivity_minutes: Maximum permissible age of last telemetry packet
    """
    if now_ts_ms is None:
        now_ts_ms = int(time.time() * 1000)

    vitals: Dict[str, Any] = {}
    passed_checks: List[str] = []
    failed_checks: List[str] = []

    # 1. Activity & Freshness Check
    last_act_ts = None
    if "lastActivityTime" in attributes:
        last_act_ts = _to_float(attributes["lastActivityTime"])
    elif "lastActivityTime" in timeseries and timeseries["lastActivityTime"]:
        last_act_ts = _to_float(timeseries["lastActivityTime"][0].get("value"))

    # Fallback to newest telemetry ts
    if last_act_ts is None:
        all_ts = [
            v[0].get("ts", 0)
            for v in timeseries.values()
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict)
        ]
        if all_ts:
            last_act_ts = max(all_ts)

    age_minutes = None
    is_active = False
    if last_act_ts and last_act_ts > 0:
        age_minutes = max(0.0, (now_ts_ms - last_act_ts) / (1000.0 * 60.0))
        vitals["last_activity_age_minutes"] = round(age_minutes, 1)
        if age_minutes <= max_inactivity_minutes:
            is_active = True
            passed_checks.append(f"Recent data activity ({round(age_minutes, 1)}m ago)")
        else:
            failed_checks.append(f"Stale data activity ({round(age_minutes, 1)}m ago > {max_inactivity_minutes}m)")
    else:
        vitals["last_activity_age_minutes"] = None
        failed_checks.append("Missing last activity timestamp")

    # 2. Gateway & Power Subsystem Check (gatewayStatus)
    gw_raw = timeseries.get("gatewayStatus")
    gw_val = None
    if gw_raw and isinstance(gw_raw, list) and len(gw_raw) > 0:
        gw_val = _safe_json_parse(gw_raw[0].get("value"))
    elif "gatewayStatus" in attributes:
        gw_val = _safe_json_parse(attributes["gatewayStatus"])

    batt_volt = None
    ac_volt = None
    batt_low = None
    mains_on = None
    sys_on = None
    net_on = None

    if isinstance(gw_val, dict):
        batt_volt = _to_float(gw_val.get("battery_voltage"))
        ac_volt = _to_float(gw_val.get("ac_voltage"))
        batt_low = _to_bool(gw_val.get("BATTERY LOW"))
        mains_on = _to_bool(gw_val.get("MAINS ON"))
        sys_on = _to_bool(gw_val.get("SYSTEM ON"))
        net_on = _to_bool(gw_val.get("NETWORK"))

        vitals["battery_voltage"] = batt_volt
        vitals["ac_voltage"] = ac_volt
        vitals["mains_on"] = mains_on
        vitals["battery_low"] = batt_low

        # Power verification
        if batt_low is False and (batt_volt is None or batt_volt >= 12.0):
            passed_checks.append(f"Battery healthy ({batt_volt or 'OK'}V, not low)")
        elif batt_low is True or (batt_volt is not None and batt_volt < 11.5):
            failed_checks.append(f"Battery degraded ({batt_volt}V, low={batt_low})")

        if mains_on is True or (ac_volt is not None and ac_volt >= 170.0):
            passed_checks.append(f"Mains power restored ({ac_volt or 'Active'}V)")
        elif mains_on is False and (ac_volt is None or ac_volt < 160.0):
            failed_checks.append(f"Mains power outage ({ac_volt}V, mains_on=False)")
    else:
        vitals["gateway_status"] = "Unavailable"

    # 3. Operations Ticket Status Check (ticketStatus)
    tkt_raw = timeseries.get("ticketStatus")
    tkt_val = None
    if tkt_raw and isinstance(tkt_raw, list) and len(tkt_raw) > 0:
        tkt_val = _safe_json_parse(tkt_raw[0].get("value"))
    elif "ticketStatus" in attributes:
        tkt_val = _safe_json_parse(attributes["ticketStatus"])

    active_fault_flags = []
    if isinstance(tkt_val, dict):
        critical_keys = ["POWER_OFF", "BATTERY_LOW", "NVR_OFF", "CAM_DIS", "H_ERROR", "FAS_OFF", "IAS_OFF"]
        for k in critical_keys:
            if _to_bool(tkt_val.get(k)) is True:
                active_fault_flags.append(k)

        vitals["active_ticket_faults"] = active_fault_flags
        if not active_fault_flags:
            passed_checks.append("Zero active ticket faults")
        else:
            failed_checks.append(f"Active ticket faults present: {', '.join(active_fault_flags)}")

    # 4. CCTV Camera Health Check (cameraStatus)
    cam_raw = timeseries.get("cameraStatus")
    cam_val = None
    if cam_raw and isinstance(cam_raw, list) and len(cam_raw) > 0:
        cam_val = _safe_json_parse(cam_raw[0].get("value"))
    if isinstance(cam_val, dict):
        cctv_on = _to_bool(cam_val.get("cctvStatus"))
        hdd_err = _to_bool(cam_val.get("HDD ERROR"))
        cam_dis = _to_bool(cam_val.get("CAMERA DISCONNECT"))
        vitals["cctv_status"] = "On" if cctv_on else "Off"
        if cctv_on is True and hdd_err is False and cam_dis is False:
            passed_checks.append("CCTV online & cameras connected")
        elif hdd_err is True or cam_dis is True:
            failed_checks.append(f"CCTV peripheral issue (hdd_err={hdd_err}, cam_dis={cam_dis})")

    # 5. Subsystem Restoration Attributes (gateway, bas, fas logStatusMode)
    restored_subsystems = []
    for sub in ["gateway", "bas", "fas"]:
        sub_attr = _safe_json_parse(attributes.get(sub))
        if isinstance(sub_attr, dict):
            mode = sub_attr.get("logStatusMode", "")
            alarm_flag = _to_bool(sub_attr.get("alarmFlag"))
            if mode == "Restored" or alarm_flag is False:
                restored_subsystems.append(sub)

    if restored_subsystems:
        passed_checks.append(f"Subsystems restored: {', '.join(restored_subsystems)}")
        vitals["restored_subsystems"] = restored_subsystems

    # Decision Matrix
    # A device is verified recovered if:
    # - It is active (fresh data within threshold)
    # - Zero critical ticket faults (POWER_OFF, BATTERY_LOW, etc.)
    # - Battery is not critically low (batt_low != True or batt_volt >= 12.0)
    # - Mains power is either on, or voltage is healthy
    is_recovered = False
    confidence = 0.0

    total_checks = len(passed_checks) + len(failed_checks)
    if total_checks > 0:
        confidence = len(passed_checks) / total_checks

    # Must satisfy core vitality conditions
    vital_failures = [f for f in failed_checks if "Battery degraded" in f or "Active ticket faults" in f or "Stale data activity" in f]
    
    if is_active and not active_fault_flags and (batt_low is not True) and len(vital_failures) == 0:
        is_recovered = True
        status_tier = "SERVICED_RECOVERED"
        summary_reason = f"Verified operational recovery: {'; '.join(passed_checks)}"
    elif len(failed_checks) > 0 and any("Active ticket faults" in f or "Battery degraded" in f for f in failed_checks):
        status_tier = "CONFIRMED_FAULT"
        summary_reason = f"Ongoing hardware fault: {'; '.join(failed_checks)}"
    else:
        status_tier = "UNVERIFIED"
        summary_reason = f"Inconclusive telemetry status: {'; '.join(passed_checks or failed_checks)}"

    return RecoveryEvaluation(
        device_id=device_id,
        is_recovered=is_recovered,
        confidence=confidence,
        status_tier=status_tier,
        summary_reason=summary_reason,
        vitals=vitals,
    )


def probe_device_telemetry_live(
    device_id: str,
    host: str,
    token: str,
    keys: str = "gatewayStatus,ticketStatus,cameraStatus,active,lastActivityTime",
    timeout: float = 6.0
) -> RecoveryEvaluation:
    """Fetch live timeseries and SERVER_SCOPE attributes from ThingsBoard and evaluate recovery."""
    headers = {"X-Authorization": f"Bearer {token}"}
    host = host.rstrip("/")

    # 1. Fetch timeseries
    timeseries: Dict[str, Any] = {}
    try:
        ts_resp = requests.get(
            f"{host}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries?keys={keys}",
            headers=headers,
            timeout=timeout
        )
        if ts_resp.status_code == 200:
            timeseries = ts_resp.json()
    except Exception as e:
        logger.debug(f"Failed to query timeseries for {device_id}: {e}")

    # 2. Fetch server attributes
    attributes: Dict[str, Any] = {}
    try:
        attr_resp = requests.get(
            f"{host}/api/plugins/telemetry/DEVICE/{device_id}/values/attributes/SERVER_SCOPE",
            headers=headers,
            timeout=timeout
        )
        if attr_resp.status_code == 200:
            for item in attr_resp.json():
                attributes[item.get("key")] = item.get("value")
    except Exception as e:
        logger.debug(f"Failed to query attributes for {device_id}: {e}")

    return evaluate_telemetry_recovery(device_id, timeseries, attributes)


def fetch_fleet_liveness(
    host: str,
    token: str,
    max_inactivity_hours: float = 24.0,
    timeout: float = 10.0,
) -> Dict[str, Dict[str, Any]]:
    """Bulk-fetch live connectivity status for all tenant devices via ThingsBoard entitiesQuery.

    Returns a dictionary mapping device_id -> {
        "device_id": str,
        "device_name": str,
        "active": bool,
        "last_activity_time": Optional[float],
        "age_hours": Optional[float],
        "is_active": bool,
        "inactivity_reason": str,
    }
    """
    headers = {"X-Authorization": f"Bearer {token}"}
    host = host.rstrip("/")

    payload = {
        "entityFilter": {"type": "entityType", "entityType": "DEVICE"},
        "pageLink": {
            "pageSize": 250,
            "page": 0,
            "sortOrder": {"key": {"type": "ENTITY_FIELD", "key": "createdTime"}, "direction": "DESC"}
        },
        "entityFields": [
            {"type": "ENTITY_FIELD", "key": "name"},
            {"type": "ENTITY_FIELD", "key": "type"}
        ],
        "latestValues": [
            {"type": "SERVER_ATTRIBUTE", "key": "active"},
            {"type": "SERVER_ATTRIBUTE", "key": "lastActivityTime"},
            {"type": "SERVER_ATTRIBUTE", "key": "lastConnectTime"},
            {"type": "SERVER_ATTRIBUTE", "key": "lastDisconnectTime"},
            {"type": "SERVER_ATTRIBUTE", "key": "inactivityReason"}
        ]
    }

    liveness_map: Dict[str, Dict[str, Any]] = {}
    try:
        resp = requests.post(
            f"{host}/api/entitiesQuery/find",
            headers=headers,
            json=payload,
            timeout=timeout
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            now_ms = time.time() * 1000
            for d in data:
                dev_id = d.get("entityId", {}).get("id")
                if not dev_id:
                    continue
                name = d.get("latest", {}).get("ENTITY_FIELD", {}).get("name", {}).get("value", "")
                sa = d.get("latest", {}).get("SERVER_ATTRIBUTE", {})

                raw_active = sa.get("active", {}).get("value")
                is_tb_active = str(raw_active).lower() == "true"

                lat_raw = sa.get("lastActivityTime", {}).get("value")
                lat_ms = _to_float(lat_raw)

                age_hours = None
                if lat_ms and lat_ms > 0:
                    age_hours = max(0.0, (now_ms - lat_ms) / 3600000.0)

                reason = sa.get("inactivityReason", {}).get("value", "") or ""

                # Device is considered fresh if TB reports active AND data received within max_inactivity_hours
                is_fresh = is_tb_active and (age_hours is None or age_hours <= max_inactivity_hours)

                liveness_map[dev_id] = {
                    "device_id": dev_id,
                    "device_name": name,
                    "active": is_tb_active,
                    "last_activity_time": lat_ms,
                    "age_hours": round(age_hours, 1) if age_hours is not None else None,
                    "is_active": is_fresh,
                    "inactivity_reason": reason,
                }
            active_count = sum(1 for v in liveness_map.values() if v["is_active"])
            logger.info(f"Retrieved bulk liveness for {len(liveness_map)} devices ({active_count} active).")
        else:
            logger.warning(f"Entities query returned status {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Failed to fetch bulk fleet liveness from ThingsBoard: {e}")

    return liveness_map

