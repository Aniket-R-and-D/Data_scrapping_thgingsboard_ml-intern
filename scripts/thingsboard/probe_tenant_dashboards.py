"""scripts/probe_tenant_dashboards.py
Inspect actual dashboards on ThingsBoard tenant to confirm dashboard identities.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL")
TB_PASSWORD = os.getenv("TB_PASSWORD")

res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD}, timeout=10)
token = res.json()["token"]
headers = {"X-Authorization": f"Bearer {token}"}

# 1. Fetch the target dashboard specifically
TARGET_ID = "4a25a020-62f7-11f1-9463-fde182b082f4"
target_res = requests.get(f"{TB_HOST}/api/dashboard/{TARGET_ID}", headers=headers)
print(f"Target Dashboard HTTP Status: {target_res.status_code}")
if target_res.status_code == 200:
    target = target_res.json()
    print("=" * 65)
    print("CURRENT LIVE TARGET DASHBOARD DETAILS")
    print("=" * 65)
    print(f"ID: {target['id']['id']}")
    print(f"Title: {target.get('title')}")
    cfg = target.get("configuration", {})
    print(f"Widgets Count: {len(cfg.get('widgets', {}))}")
    for wid, w in cfg.get("widgets", {}).items():
        print(f"  • Widget [{wid}]: {w.get('typeFullFqn')} | Title: \"{w.get('title')}\" (row={w.get('row')}, col={w.get('col')}, sizeX={w.get('sizeX')}, sizeY={w.get('sizeY')})")
    aliases = cfg.get("entityAliases", {})
    print(f"Entity Aliases Count: {len(aliases)}")
    for aid, a in aliases.items():
        print(f"  • Alias [{aid}]: \"{a.get('alias')}\" -> Type: {a.get('filter', {}).get('entityType')}")
    print(f"Settings: {cfg.get('settings', {})}")

# 2. Check all other dashboards on tenant
dashs_res = requests.get(f"{TB_HOST}/api/tenant/dashboards?pageSize=100&page=0", headers=headers)
data = dashs_res.json().get("data", [])
print("\n" + "=" * 65)
print(f"ALL TENANT DASHBOARDS (Total: {len(data)})")
print("=" * 65)
for d in data:
    did = d["id"]["id"]
    title = d.get("title", "")
    print(f"ID: {did} | Title: {title}")
