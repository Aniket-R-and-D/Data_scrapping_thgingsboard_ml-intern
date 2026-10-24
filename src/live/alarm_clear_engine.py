"""src/live/alarm_clear_engine.py
ThingsBoard Alarm Clearance and Lifecycle Management Engine.

Provides automated clearing of active AI alarms and synchronization
of recovered operational states across ThingsBoard SHARED_SCOPE,
SERVER_SCOPE, and timeseries streams.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from src.live.recovery_gate import RecoveryEvaluation

logger = logging.getLogger("alarm_clear_engine")

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
