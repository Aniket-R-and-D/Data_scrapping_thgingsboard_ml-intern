import os
import requests
from dotenv import load_dotenv

load_dotenv(".env")
TB_HOST = os.getenv("TB_HOST", "https://seple.iot-private.cloud").rstrip("/")
TB_EMAIL = os.getenv("TB_EMAIL")
TB_PASSWORD = os.getenv("TB_PASSWORD")

res = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_EMAIL, "password": TB_PASSWORD})
token = res.json()["token"]
headers = {"X-Authorization": f"Bearer {token}"}

rc_res = requests.get(f"{TB_HOST}/api/ruleChains?pageSize=100&page=0", headers=headers)
data = rc_res.json()
total = data.get("totalElements", len(data.get("data", [])))
print(f"Total rule chains: {total}")
for idx, rc in enumerate(data.get("data", [])):
    rc_id = rc["id"]["id"]
    name = rc["name"]
    rc_type = rc.get("type")
    is_root = rc.get("root")
    print(f"{idx+1:2d}. [{rc_id}] root={is_root} type={rc_type} : {name}")
