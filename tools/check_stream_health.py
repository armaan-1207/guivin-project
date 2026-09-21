"""Sample one already-running Docker camera without starting or stopping it.

Uses the generated local account, prints only health counters (no stream URLs).
Run from the project root: python tools/check_stream_health.py [camera-id]
"""
import http.cookiejar
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request


def main():
    base = 'http://127.0.0.1:8001'
    camera = sys.argv[1] if len(sys.argv) > 1 else 'SENTINEL-CAM01'
    accounts = json.loads(Path('tmp/docker/users.json').read_text(encoding='utf-8'))
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    payload = {'username': next(iter(accounts)),
               'password': Path('tmp/docker/admin-password.txt').read_text(encoding='utf-8')}
    request = urllib.request.Request(base + '/api/auth/login',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with client.open(request, timeout=15):
        pass
    keys = ('status', 'ai_status', 'frames', 'processed_frames', 'dropped_frames',
            'ocr_attempts', 'ocr_deferred', 'last_inference_seconds', 'last_stage_seconds',
            'queue_depth', 'timing_status')
    samples = []
    start = time.monotonic()
    for index in range(4):
        if index:
            time.sleep(10)
        with client.open(base + '/api/stream/' + urllib.parse.quote(camera, safe='')
                         + '/health', timeout=15) as response:
            health = json.load(response)
        sample = {'elapsed_seconds': round(time.monotonic() - start, 3),
                  **{key: health.get(key) for key in keys}}
        samples.append(sample)
        print(json.dumps(sample), flush=True)
        if health.get('status') == 'NOT_STREAMING':
            break
    if len(samples) > 1:
        elapsed = samples[-1]['elapsed_seconds'] - samples[0]['elapsed_seconds']
        first, last = samples[0]['processed_frames'], samples[-1]['processed_frames']
        if isinstance(first, int) and isinstance(last, int) and last >= first:
            print(json.dumps({'processed_frames_per_second': round((last-first)/elapsed, 3),
                              'note': 'Observed sample only; not an accuracy or capacity benchmark.'}))


if __name__ == '__main__':
    main()
