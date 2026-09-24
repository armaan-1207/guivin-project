"""Authenticated HLS proxy and token-protected loopback relay for OpenCV."""
import posixpath
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit, unquote, quote, parse_qs
from fastapi import HTTPException

def camera_base(camera_id):
    cid = camera_id.removeprefix('SENTINEL-').lower()
    if not re.fullmatch(r'[a-z0-9_-]+', cid):
        raise HTTPException(422, 'Invalid camera ID')
    return f'https://cctv.corp8.cloud/{cid}/'

def validate_asset(camera_id, url):
    parsed = urlsplit(url)
    base = urlsplit(camera_base(camera_id))
    path = posixpath.normpath(unquote(parsed.path))
    if (parsed.scheme != 'https' or parsed.netloc != base.netloc or parsed.username or
            not path.startswith(base.path) or '\\' in path or parsed.fragment):
        raise HTTPException(403, 'Asset outside camera source')
    return url

def rewrite_manifest(text, source, camera_id, endpoint):
    def remap(value):
        target=validate_asset(camera_id, urljoin(source,value))
        return endpoint + '?u=' + quote(target, safe='')
    output=[]
    for line in text.splitlines():
        stripped=line.strip()
        if stripped and not stripped.startswith('#'):
            output.append(remap(stripped))
        elif 'URI="' in line:
            output.append(re.sub(r'URI="([^"]+)"', lambda m:'URI="'+remap(m.group(1))+'"',line))
        else:
            output.append(line)
    return '\n'.join(output).encode()

def fetch_asset(session, camera_id, url, endpoint):
    validate_asset(camera_id,url)
    try:
        with session.get(url,timeout=(5,15),stream=True,allow_redirects=False) as response:
            if response.status_code != 200:
                raise HTTPException(502, 'Upstream HLS unavailable')
            data=bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data)>16*1024*1024:
                    raise HTTPException(502,'Upstream asset too large')
            content=bytes(data)
            kind=response.headers.get('Content-Type','application/octet-stream').split(';')[0]
        if content.lstrip().startswith(b'#EXTM3U'):
            return rewrite_manifest(content.decode('utf-8'),url,camera_id,endpoint),'application/vnd.apple.mpegurl'
        if kind.startswith('text/html'):
            raise HTTPException(502, 'Upstream returned a login page')
        return content,kind
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502,'HLS source request failed')

class Relay:
    def __init__(self):
        self.server=None
        self.routes={}
        self.lock=threading.RLock()

    def register(self,camera_id,session):
        with self.lock:
            if not self.server:
                owner=self
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self,*args):
                        pass
                    def do_GET(self):
                        token=urlsplit(self.path).path.strip('/')
                        route=owner.routes.get(token)
                        if not route:
                            self.send_error(404)
                            return
                        cam,sess=route
                        url=parse_qs(urlsplit(self.path).query).get('u',[camera_base(cam)+'index.m3u8'])[0]
                        try:
                            data,kind=fetch_asset(sess,cam,url,'/'+token)
                            self.send_response(200)
                            self.send_header('Content-Type',kind)
                            self.send_header('Content-Length',str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                        except HTTPException as error:
                            self.send_error(error.status_code)
                        except (BrokenPipeError,ConnectionResetError):
                            pass
                self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
                self.server.daemon_threads=True
                threading.Thread(target=self.server.serve_forever,daemon=True).start()
            token=secrets.token_urlsafe(32)
            self.routes[token]=(camera_id,session)
            return f'http://127.0.0.1:{self.server.server_port}/{token}'

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server=None
        self.routes.clear()

relay=Relay()
