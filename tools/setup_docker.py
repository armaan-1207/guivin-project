"""Build Docker and prepare local models/accounts without installing AI packages on the host."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def prepare():
    def script(name, *args):
        run(sys.executable, str(ROOT / 'tools' / name), *args)

    accounts = ROOT / 'tmp/docker/users.json'
    password = ROOT / 'tmp/docker/admin-password.txt'
    if not accounts.exists() and not password.exists():
        script('bootstrap_container.py')
    elif not accounts.is_file() or not password.is_file():
        raise SystemExit('Incomplete account setup; inspect tmp/docker before retrying. No credentials overwritten.')
    script('setup_models.py', '--download')
    bundle = ROOT / 'tmp/fast-alpr-paddle'
    if not bundle.exists():
        detector = ROOT / 'tmp/fast-alpr-large'
        if not (detector / 'manifest.json').is_file():
            script('setup_fast_alpr.py', '--directory', str(detector), '--large')
        script('compare_paddleocr.py', '--download-only')
        script('prepare_paddle_anpr.py', '--detector-bundle', str(detector),
               '--paddle-root', str(ROOT / 'tmp/paddle-evaluation/.paddlex/official_models'),
               '--output', str(bundle))
    sys.path.insert(0, str(ROOT / 'backend'))
    from app.anpr_engine import load_local_engine
    import numpy as np
    load_local_engine(bundle).predict(np.zeros((320, 320, 3), dtype=np.uint8))
    print('Model loading passed. This is a readiness check, not an accuracy benchmark.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside-container', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside_container:
        prepare()
        return
    platform = subprocess.check_output(['docker', 'info', '--format', '{{.OSType}}'], text=True).strip()
    if platform != 'linux':
        raise SystemExit('Select Linux containers in Docker Desktop, then rerun setup.')
    run('docker', 'compose', 'build')
    run('docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}' if hasattr(os, 'getuid') else '0:0',
        '--entrypoint', 'python', '--mount', f'type=bind,source={ROOT},target=/workspace',
        '--workdir', '/workspace',
        '-e', 'HOME=/tmp', '-e', 'GUIVIN_USERS_FILE=', '-e', 'GUIVIN_STATE_DIR=/tmp/setup-state',
        '-e', 'GUIVIN_WARMUP_MODELS=0', '-e', 'PYTHONPATH=/workspace/backend',
        '-e', 'YOLO_MODEL=/workspace/yolov8n.pt', '-e', 'EASYOCR_MODULE_PATH=/workspace/tmp/easyocr',
        '-e', 'PADDLE_PDX_CACHE_HOME=/workspace/tmp/paddle-evaluation/.paddlex',
        'guivin:anpr-paddle', '/workspace/tools/setup_docker.py', '--inside-container')
    print('Setup complete. Run: docker compose up -d')
    print('Open http://localhost:8001 and sign in as local-admin.')
    print('Your password is saved in tmp/docker/admin-password.txt. Existing accounts were preserved.')


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        raise SystemExit(f'Setup stopped: {error}. Resolve the error above, then run setup again.')
