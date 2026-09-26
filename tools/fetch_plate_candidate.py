"""Explicitly cache a pinned research plate detector; never enable it in the app."""
import hashlib
import json
from pathlib import Path
import requests

REPO = 'Koushim/yolov8-license-plate-detection'
REVISION = '9aaa5cd490abe0c165882ba87f4f62658ab54d01'
ROOT = Path(__file__).resolve().parents[1] / 'tmp' / 'plate-candidate'


def main():
    metadata = requests.get(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true', timeout=30)
    metadata.raise_for_status()
    files = {item['rfilename']: item for item in metadata.json()['siblings']}
    expected = files['best.pt']['lfs']['sha256']
    ROOT.mkdir(parents=True, exist_ok=True)
    for name in ('README.md', 'best.pt'):
        path = ROOT / name
        if path.exists():
            if name == 'best.pt' and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Existing candidate model hash differs from the pinned publisher file')
            continue
        response = requests.get(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}', timeout=60)
        response.raise_for_status()
        if len(response.content) > 15_000_000:
            raise ValueError('Candidate exceeds the download budget')
        if name == 'best.pt' and hashlib.sha256(response.content).hexdigest() != expected:
            raise ValueError('Downloaded model does not match publisher hash')
        path.write_bytes(response.content)
    provenance = {'source': f'https://huggingface.co/{REPO}', 'revision': REVISION,
        'sha256': expected, 'publisher_license': 'MIT in model card; Ultralytics has separate terms',
        'limitations': 'Training data and geographic coverage not documented; offline research candidate only.'}
    (ROOT/'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()
