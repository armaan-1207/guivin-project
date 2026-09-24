"""Read-only local container smoke test using the generated account file."""
import json
from pathlib import Path
import urllib.request
import urllib.error
import http.cookiejar
import http.client
import time

base='http://127.0.0.1:8001'
deadline = time.monotonic() + 60
while True:
    try:
        urllib.request.urlopen(base+'/api/health',timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code==401, 'Unexpected unauthenticated response'
        break
    except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError):
        if time.monotonic() >= deadline:
            raise SystemExit('Container did not become reachable within 60 seconds')
        time.sleep(1)
    else:
        raise SystemExit('Container API accepted an unauthenticated request')
accounts=json.loads(Path('tmp/docker/users.json').read_text(encoding='utf-8'))
username=next(iter(accounts))
password=Path('tmp/docker/admin-password.txt').read_text(encoding='utf-8')
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
request=urllib.request.Request(base+'/api/auth/login',data=json.dumps({'username':username,'password':password}).encode(),headers={'Content-Type':'application/json'})
with client.open(request,timeout=15) as response:
    assert response.status==200
with client.open(base+'/api/health',timeout=15) as response:
    health=json.load(response)
assert health['readiness']['analysis']=='READY', 'Models are not ready'
with client.open(base+'/api/cameras',timeout=15) as response:
    cameras=json.load(response)
assert len(cameras)>=12
print(json.dumps({'anonymous_access':'DENIED','authenticated_access':'OK','analysis':health['readiness']['analysis'],
                  'models':health['models'],'cameras':len(cameras)},indent=2))
