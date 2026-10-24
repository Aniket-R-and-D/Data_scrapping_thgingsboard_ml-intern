"""Live verification script to audit the 5 recovered devices directly against ThingsBoard API."""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL", "info@seple.in")
TB_PASSWORD = os.getenv("TB_PASSWORD", "")

RECOVERED_DEVICES = [
    {"name": "BOI-BABAI", "id": "5472ea20-aa78-11f0-b8ab-b38af83f6c9c"},
    {"name": "BOI-MISROD", "id": "aed666e0-aa69-11f0-b150-2710a8915e1d"},
    {"name": "DX2", "id": "630c1480-63e6-11f1-9555-295f91c5b7c8"},
    {"name": "BOI-SALTLAKE", "id": "6287e400-a823-11f0-b150-2710a8915e1d"},
    {"name": "CANARA-BARUIPUR", "id": "0b7bc7d0-671d-11f1-8c55-5bb8284c6aef"}
]


def audit_devices():
    print("=" * 70)
    print("LIVE AUDIT: Verifying Recovered Devices on ThingsBoard")
    print("=" * 70)

    # 1. Login
    resp = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD}, timeout=10)
    if resp.status_code != 200:
        print(f"FAILED to login: {resp.status_code}")
        sys.exit(1)
    token = resp.json()["token"]
    headers = {"X-Authorization": f"Bearer {token}"}

    for dev in RECOVERED_DEVICES:
        name = dev["name"]
        dev_id = dev["id"]
        print(f"\n--- Checking Device: {name} ({dev_id}) ---")

        # 2. Check SERVER_SCOPE attributes
        attr_resp = requests.get(
            f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/attributes/SERVER_SCOPE",
            headers=headers,
            timeout=10
        )
        if attr_resp.status_code == 200:
            attrs = {a["key"]: a["value"] for a in attr_resp.json()}
            print(f"  SERVER_SCOPE:")
            print(f"    ai_health_tier:       {attrs.get('ai_health_tier')}")
            print(f"    ai_outage_risk_prob:  {attrs.get('ai_outage_risk_prob')}")
            print(f"    ai_last_evaluated:    {attrs.get('ai_last_evaluated')}")
            print(f"    ai_primary_issue:     {attrs.get('ai_primary_issue')[:70]}...")
            print(f"    ai_recommended_action:{attrs.get('ai_recommended_action')}")
        else:
            print(f"  Failed to get SERVER_SCOPE attributes: {attr_resp.status_code}")

        # 3. Check SHARED_SCOPE attributes
        attr_shared = requests.get(
            f"{TB_HOST}/api/plugins/telemetry/DEVICE/{dev_id}/values/attributes/SHARED_SCOPE",
            headers=headers,
            timeout=10
        )
        if attr_shared.status_code == 200:
            shared_attrs = {a["key"]: a["value"] for a in attr_shared.json()}
            print(f"  SHARED_SCOPE:")
            print(f"    ai_health_tier:       {shared_attrs.get('ai_health_tier')}")
        else:
            print(f"  Failed to get SHARED_SCOPE attributes: {attr_shared.status_code}")

        # 4. Check Alarms on this device
        alarm_resp = requests.get(
            f"{TB_HOST}/api/alarm/DEVICE/{dev_id}?pageSize=10&page=0",
            headers=headers,
            timeout=10
        )
        if alarm_resp.status_code == 200:
            alarms = alarm_resp.json().get("data", [])
            print(f"  Alarms (Total: {len(alarms)}):")
            for a in alarms[:3]:
                a_type = a.get("type")
                status = a.get("status")
                severity = a.get("severity")
                clear_ts = a.get("clearTs", 0)
                print(f"    - [{status}] Type: {a_type} | Severity: {severity} | Cleared: {clear_ts > 0}")
        else:
            print(f"  Failed to get alarms: {alarm_resp.status_code}")

    print("\n" + "=" * 70)
    print("AUDIT COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    audit_devices()
