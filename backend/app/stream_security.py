import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException
from .auth import USER_FILE

def validate_source(source):
    if not source or len(source) > 2048:
        raise HTTPException(422, 'Invalid source')
    url = urlsplit(source)
    if url.scheme in ('http','https','rtsp','rtsps'):
        if not url.hostname:
            raise HTTPException(422, 'Source needs a hostname')
        allowed = {h.strip().lower() for h in os.getenv('GUIVIN_STREAM_HOSTS','cctv.corp8.cloud,103.250.160.189').split(',')}
        if USER_FILE and url.hostname.lower() not in allowed:
            raise HTTPException(403, 'Source host is not in GUIVIN_STREAM_HOSTS')
    elif USER_FILE:
        root = os.getenv('GUIVIN_RECORDINGS_DIR','')
        if not root or not Path(source).resolve().is_relative_to(Path(root).resolve()):
            raise HTTPException(403, 'Recording is outside the configured directory')
    if url.scheme not in ('http','https','rtsp','rtsps') and not Path(source).is_file():
        raise HTTPException(422, 'Recording not found')
