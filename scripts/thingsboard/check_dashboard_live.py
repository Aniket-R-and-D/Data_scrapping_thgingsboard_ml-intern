import os, requests
from dotenv import load_dotenv
load_dotenv('.env')
host = os.getenv('TB_HOST')
email = os.getenv('TB_EMAIL')
pw = os.getenv('TB_PASSWORD')
token = requests.post(f'{host}/api/auth/login', json={'username': email, 'password': pw}).json()['token']
headers = {'X-Authorization': f'Bearer {token}'}

dash_id = '4a25a020-62f7-11f1-9463-fde182b082f4'
r = requests.get(f'{host}/api/dashboard/{dash_id}', headers=headers)
d = r.json()
print('Dashboard Title:', d.get('title'))
cfg = d.get('configuration', {})
widgets = cfg.get('widgets', {})
print('Widgets count:', len(widgets))
for wid, w in widgets.items():
    title = w.get('title')
    fqn = w.get('typeFullFqn')
    sx = w.get('sizeX')
    sy = w.get('sizeY')
    print(f"  - Widget: {title} | Type: {fqn} | Size: {sx}x{sy}")
