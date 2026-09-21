"""Explicit model setup and inference smoke check; no downloads during stream requests."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app import config

parser = argparse.ArgumentParser()
parser.add_argument('--download', action='store_true', help='Download official YOLO and EasyOCR assets if absent')
args = parser.parse_args()
if args.download:
    from ultralytics import YOLO
    import easyocr
    if not Path(config.YOLO_MODEL).exists():
        import urllib.request
        url = 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt'
        target = Path(config.YOLO_MODEL)
        target.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, target.with_suffix('.download'))
        target.with_suffix('.download').replace(target)
    easyocr.Reader(['en'], gpu=False, verbose=False, download_enabled=True)
from app.ai_pipeline import _get_yolo, _get_ocr, model_status
import numpy as np
model, reader = _get_yolo(), _get_ocr()
if model is None or reader is None:
    print(json.dumps(model_status()))
    raise SystemExit('Models unavailable. Run again with --download during setup.')
model(np.zeros((320,320,3),dtype=np.uint8), verbose=False)
reader.readtext(np.full((64,256),255,dtype=np.uint8))
manifest = {'status':model_status(), 'smoke_test':'blank-image detection and OCR passed; not an accuracy benchmark',
    'sources':['https://github.com/ultralytics/assets', 'https://github.com/JaidedAI/EasyOCR'],
    'licenses':'Review Ultralytics AGPL-3.0/Enterprise and EasyOCR Apache-2.0 terms for intended distribution',
    'files':{str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
             for path in [Path(config.YOLO_MODEL), *Path(config.os.environ['EASYOCR_MODULE_PATH']).rglob('*.pth')]}}
output = ROOT / 'tmp' / 'model-manifest.json'
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest,indent=2))
