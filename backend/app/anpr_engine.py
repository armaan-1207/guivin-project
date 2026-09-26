"""Local FastALPR adapter; model installation is an explicit offline setup step."""
import math
import re
import threading
import time
import hashlib
import json
from pathlib import Path

from .plate_text import is_valid_plate


def load_local_engine(directory, threads=2):
    root = Path(directory)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    paths = {}
    for key in manifest['files']:
        entry = manifest['files'][key]
        path = (root / entry['name']).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Missing local ANPR artifact')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('ANPR artifact hash mismatch')
        paths[key] = path
    from fast_alpr import ALPR
    from open_image_models import create_detector
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, int(threads)))
    options.inter_op_num_threads = 1
    detector = create_detector(paths['detector'], backend='yolo_v9',
                               class_labels=('License Plate',), conf_thresh=.4,
                               providers=['CPUExecutionProvider'], sess_options=options)
    if manifest.get('recognizer') == 'paddle':
        from .paddle_anpr import PaddlePlateReader, MODEL_NAMES, MODEL_FILES
        required = {(root / 'paddle' / name / file).resolve() for name in MODEL_NAMES for file in MODEL_FILES}
        if not required.issubset(set(paths.values())):
            raise ValueError('PaddleOCR artifacts must all be hash-verified')
        return ALPR(detector=detector, ocr=PaddlePlateReader(root / 'paddle'))
    return ALPR(detector=detector, ocr_model=None, ocr_device='cpu',
                ocr_model_path=paths['ocr'], ocr_config_path=paths['config'],
                ocr_providers=['CPUExecutionProvider'], ocr_sess_options=options)


def select_observation(results):
    empty = {'plate': '', 'confidence': 0.0, 'localization': 'FAST_ALPR',
             'rectified': False, 'preprocessing': 'PRETRAINED_PLATE_OCR'}
    candidates = []
    for result in results:
        if result.ocr is None:
            continue
        text = re.sub('[^A-Z0-9]', '', result.ocr.text.upper())
        values = result.ocr.confidence
        values = values if isinstance(values, (list, tuple)) else [values]
        try:
            scores = [float(value) for value in values]
            detection_score = float(result.detection.confidence)
        except (ValueError, TypeError):
            continue
        if (not scores or not is_valid_plate(text) or
                not all(math.isfinite(v) and 0 <= v <= 1 for v in [*scores, detection_score])):
            continue
        confidence = min(*scores, detection_score)
        if confidence >= .3:
            candidates.append((text, confidence))
    # A vehicle crop can include a neighbour: never arbitrarily assign its plate.
    identities = {text for text, _ in candidates}
    if len(identities) != 1:
        return empty
    text, confidence = max(candidates, key=lambda item: item[1])
    return dict(empty, plate=text, confidence=confidence)


class Engine:
    def __init__(self, factory):
        self.factory = factory
        self.lock = threading.Lock()
        self.model = None
        self.error = None
        self.retry_after = 0

    def status(self):
        return 'FAILED' if self.error else 'READY' if self.model is not None else 'NOT_LOADED'

    def predict(self, crop):
        with self.lock:
            if self.error and time.monotonic() < self.retry_after:
                raise RuntimeError('FastALPR unavailable; inspect model setup')
            try:
                if self.model is None:
                    self.model = self.factory()
                result = self.model.predict(crop)
                self.error = None
                return result
            except Exception as error:
                self.model = None
                self.error = type(error).__name__
                self.retry_after = time.monotonic() + 60
                raise RuntimeError('FastALPR inference unavailable') from error

    def observe(self, frame, bbox):
        x, y, w, h = (int(value) for value in bbox)
        height, width = frame.shape[:2]
        left, top = max(0, x), max(0, y)
        right, bottom = min(width, x + w), min(height, y + h)
        if right <= left or bottom <= top:
            return select_observation([])
        return select_observation(self.predict(frame[top:bottom, left:right]))
