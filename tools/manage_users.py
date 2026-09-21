"""Create/update a local account using an interactive password prompt."""
import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
from app.auth import password_hash
from app.permissions import ROLES, role
parser=argparse.ArgumentParser()
parser.add_argument('--file',default='credentials.json')
parser.add_argument('--username',required=True)
parser.add_argument('--role',choices=sorted(ROLES),required=True)
parser.add_argument('--department',required=True,help='Exact registry department, or * for all')
parser.add_argument('--camera-ids', nargs='*', default=[], help='Required sector camera scope')
a=parser.parse_args()
if role({'role':a.role}) == 'sector_supervisor' and not a.camera_ids:
    parser.error('Sector supervisors require --camera-ids')
if role({'role':a.role}) in ('field_operator','department_head') and a.department == '*':
    parser.error('This role requires an explicit department')
password=getpass.getpass('Password (at least 12 characters): ')
if len(password)<12 or password!=getpass.getpass('Confirm password: '):
    raise SystemExit('Password too short or confirmation does not match')
path=Path(a.file)
users=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
salt=secrets.token_hex(16)
users[a.username]={'salt':salt,'hash':password_hash(password,salt),'role':a.role,'department':a.department,'camera_ids':a.camera_ids}
path.write_text(json.dumps(users,indent=2),encoding='utf-8')
print('Account saved. Set GUIVIN_USERS_FILE to the absolute path before starting the server.')
