"""Single-process sessions and department-scoped access. Unconfigured mode is loopback-only."""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from contextvars import ContextVar
from urllib.parse import urlsplit
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from .permissions import permitted, role, ROLES

principal = ContextVar('principal', default=None)
USER_FILE = os.getenv('GUIVIN_USERS_FILE', '')
SESSIONS = {}
ATTEMPTS = {}
LOCAL = {'username': 'local-developer', 'role': 'admin', 'department': '*'}
SESSION_SECONDS = 15 * 60

def users():
    return json.loads(Path(USER_FILE).read_text(encoding='utf-8')) if USER_FILE else {}

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()

def identity(connection):
    if not USER_FILE:
        host = connection.client.host if connection.client else ''
        return LOCAL if host in ('127.0.0.1', '::1', 'testclient') and connection.url.hostname in ('localhost', '127.0.0.1', '::1', 'testserver') else None
    session = SESSIONS.get(connection.cookies.get('guivin_session', ''))
    if session and session['expires'] > time.time():
        user = users().get(session['username'])
        if user:
            who = dict(username=session['username'], role=user['role'], department=user['department'],
                       camera_ids=user.get('camera_ids', []))
            if who['role'] not in ROLES:
                return None
            if role(who) in ('field_operator', 'department_head') and who['department'] == '*':
                return None
            if role(who) == 'sector_supervisor' and not who['camera_ids']:
                return None
            return who
    return None

def require_department(department):
    who = principal.get() or LOCAL
    if who['department'] != '*' and who['department'] != department:
        raise HTTPException(403, 'Department access denied')

class AccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        # Origin check blocks cross-site mutations, including loopback demo mode.
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
            if urlsplit(origin).netloc != request.url.netloc:
                return JSONResponse({'detail':'Cross-origin mutation denied'},status_code=403)
        if path in ('/login', '/api/auth/login') or path.startswith('/static/'):
            return await call_next(request)
        who = identity(request)
        if not who:
            if path == '/':
                return RedirectResponse('/login', status_code=303)
            return JSONResponse({'detail':'Authentication required'},status_code=401)
        mutating = request.method not in ('GET','HEAD','OPTIONS')
        camera_config = (request.method == 'PATCH' and path.startswith('/api/cameras/')) or (request.method == 'POST' and path.startswith('/api/cameras/') and path.endswith('/archive'))
        if not permitted(who, request.method, path):
            return JSONResponse({'detail':'Role does not permit this operation'},status_code=403)
        token = principal.set(who)
        try:
            response = await call_next(request)
            registry_write = camera_config or (request.method == 'POST' and path in ('/api/cameras', '/api/cameras/bulk'))
            if mutating and response.status_code < 400 and path != '/api/auth/logout' and not registry_write:
                from .database import SessionLocal, AuditDB
                with SessionLocal() as db:
                    db.add(AuditDB(actor=who['username'], department=who['department'],
                                   action=request.method, resource=path))
                    db.commit()
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
            response.headers['Cache-Control'] = 'no-store'
            return response
        finally:
            principal.reset(token)

def register_auth(app):
    @app.get('/api/auth/me')
    def me():
        return dict(principal.get() or LOCAL, mode='authenticated' if USER_FILE else 'local-only')

    @app.post('/api/auth/login')
    async def login(request: Request):
        if not USER_FILE:
            raise HTTPException(403, 'User accounts are not configured; use the loopback launcher')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(413, 'Request too large')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, 'Invalid login request')
        if not isinstance(data, dict) or not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
            raise HTTPException(422, 'Username and password must be strings')
        host = request.client.host
        now=time.time()
        for key in list(ATTEMPTS):
            if not ATTEMPTS[key] or ATTEMPTS[key][-1] < now - 300:
                ATTEMPTS.pop(key, None)
        if host not in ATTEMPTS and len(ATTEMPTS) >= 10000:
            raise HTTPException(429, 'Login capacity reached; try later')
        ATTEMPTS[host]=[t for t in ATTEMPTS.get(host,[]) if now-t<300]
        if len(ATTEMPTS[host]) >= 10:
            raise HTTPException(429, 'Too many attempts; try in five minutes')
        ATTEMPTS[host].append(now)
        name, password = data.get('username',''), data.get('password','')
        user=users().get(name)
        salt=user['salt'] if user else '0'*32
        calculated=password_hash(str(password)[:1024],salt)
        if not user or not hmac.compare_digest(calculated,user['hash']):
            raise HTTPException(401, 'Invalid credentials')
        ATTEMPTS.pop(host,None)
        for key in list(SESSIONS):
            if SESSIONS[key]['expires'] <= now:
                SESSIONS.pop(key,None)
        token=secrets.token_urlsafe(32)
        SESSIONS[token]={'username':name,'expires':now+SESSION_SECONDS}
        response=JSONResponse({'authenticated':True})
        response.set_cookie('guivin_session',token,httponly=True,samesite='strict',
                            secure=request.url.scheme=='https',max_age=SESSION_SECONDS)
        return response

    @app.post('/api/auth/logout')
    def logout(request: Request):
        SESSIONS.pop(request.cookies.get('guivin_session',''),None)
        response=JSONResponse({'logged_out':True})
        response.delete_cookie('guivin_session')
        return response

    @app.get('/login', response_class=HTMLResponse)
    def login_page():
        return '''<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>GUIVIN sign in</title></head>
<body style="font:16px system-ui;background:#101c30;color:white;max-width:400px;margin:10vh auto;padding:24px">
<h1>GUIVIN</h1><p>Sign in to your department workspace.</p><form id="login">
<p><label>Username <input name="username" autocomplete="username" required></label></p>
<p><label>Password <input name="password" type="password" autocomplete="current-password" required></label></p>
<button>Sign in</button><p id="error" role="alert"></p></form><script>
document.querySelector('form').onsubmit=async e=>{e.preventDefault();const body=Object.fromEntries(new FormData(e.target));
const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
if(r.ok)location.href='/';else document.getElementById('error').textContent=(await r.json()).detail;};
</script></body></html>'''
