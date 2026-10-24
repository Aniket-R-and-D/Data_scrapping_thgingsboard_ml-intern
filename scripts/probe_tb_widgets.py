import requests, os
from dotenv import load_dotenv
load_dotenv()

tb_host = os.getenv('TB_HOST')
token = requests.post(f'{tb_host}/api/auth/login', json={'username': os.getenv('TB_EMAIL'), 'password': os.getenv('TB_PASSWORD')}).json()['token']
headers = {'X-Authorization': f'Bearer {token}'}

for b in ['tables', 'cards']:
    print(f"=== Bundle: {b} ===")
    r = requests.get(f'{tb_host}/api/widgetTypes?isSystem=true&bundleAlias={b}', headers=headers)
    for w in r.json():
        print(f"  - {w.get('name')} | alias: {w.get('alias')} | type: {w.get('descriptor', {}).get('type')}")
