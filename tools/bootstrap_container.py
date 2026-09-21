"""Create local container credentials without printing the generated password."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.auth import password_hash

def bootstrap(destination, username='local-admin'):
    root=Path(destination)
    root.mkdir(parents=True,exist_ok=True)
    accounts, password_file = root/'users.json', root/'admin-password.txt'
    if accounts.exists() or password_file.exists():
        raise ValueError('Credentials already exist; use manage_users.py to change accounts')
    password, salt = secrets.token_urlsafe(32), secrets.token_hex(16)
    user={username:{'role':'scrb_admin','department':'*','camera_ids':[],
        'salt':salt,'hash':password_hash(password,salt)}}
    for path,content in [(accounts,json.dumps(user,indent=2)),(password_file,password)]:
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            output.write(content)
    return accounts,password_file

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default='tmp/docker')
    parser.add_argument('--username',default='local-admin')
    args=parser.parse_args()
    accounts,password=bootstrap(args.directory,args.username)
    print(f'Account: {args.username}\nAccount file: {accounts}\nPassword saved locally: {password}')
