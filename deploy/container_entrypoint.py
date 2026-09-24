"""Fail closed when a container is launched without real account configuration."""
import json
import os
from pathlib import Path

path = os.getenv('GUIVIN_USERS_FILE', '')
if not path or not Path(path).is_file():
    raise SystemExit('Mount GUIVIN_USERS_FILE before starting this container')
try:
    accounts = json.loads(Path(path).read_text(encoding='utf-8'))
except (ValueError, OSError):
    raise SystemExit('Account file could not be read')
if not isinstance(accounts, dict) or not accounts:
    raise SystemExit('At least one configured account is required')
os.execvp('python', ['python', '-m', 'uvicorn', 'app.main:app', '--app-dir', '/app/backend',
                    '--host', '0.0.0.0', '--port', '8000', '--workers', '1'])
