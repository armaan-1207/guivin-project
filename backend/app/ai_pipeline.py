"""
GUIVIN — AI Perception Pipeline
Runs YOLO object detection + EasyOCR ANPR on video frames.
Handles: vehicle detection, plate extraction, confidence scoring,
bounding-box annotation, and camera health / tamper detection.
"""
import os
import cv2
import numpy as np
import threading
import logging
import time
import math
from .config import YOLO_MODEL, PLATE_MODEL, PLATE_DETECTION_THRESHOLD, OCR_PREPROCESSING
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

logger = logging.getLogger("guivin.ai")

# ── Lazy-load YOLO & EasyOCR (heavy imports) ─────────────────────────────────
_yolo_model = None
_plate_model = None
_ocr_reader = None
_model_lock = threading.Lock()
_inference_lock = threading.Lock()
_ocr_lock = threading.Lock()
_failures = {}

def model_status():
    plate_status = ('READY' if _plate_model is not None else
                    'FAILED' if 'plate' in _failures else
                    'NOT_CONFIGURED' if not PLATE_MODEL else 'NOT_LOADED')
    return {'yolo': 'READY' if _yolo_model is not None else 'FAILED' if 'yolo' in _failures else 'NOT_LOADED',
            'plate_detector': plate_status,
            'ocr': 'READY' if _ocr_reader is not None else 'FAILED' if 'ocr' in _failures else 'NOT_LOADED',
            'errors': {k: v[1] for k, v in _failures.items()}}


def _get_yolo():
    global _yolo_model
    if 'yolo' in _failures and time.monotonic() - _failures['yolo'][0] < 60:
        return None
    if _yolo_model is None:
        with _model_lock:
            if _yolo_model is None:
                try:
                    from ultralytics import YOLO
                    if not Path(YOLO_MODEL).is_file():
                        raise FileNotFoundError('Run tools/setup_models.py before starting analysis')
                    _yolo_model = YOLO(YOLO_MODEL)
                    _failures.pop('yolo', None)
                    logger.info("[AI] YOLOv8n model loaded")
                except Exception as e:
                    _failures['yolo'] = (time.monotonic(), type(e).__name__)
                    logger.error(f"[AI] YOLO load failed: {e}")
    return _yolo_model


def _get_ocr():
    global _ocr_reader
    if 'ocr' in _failures and time.monotonic() - _failures['ocr'][0] < 60:
        return None
    if _ocr_reader is None:
        with _model_lock:
            if _ocr_reader is None:
                try:
                    import easyocr
                    _ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False, download_enabled=False)
                    _failures.pop('ocr', None)
                    logger.info("[AI] EasyOCR reader loaded")
                except Exception as e:
                    _failures['ocr'] = (time.monotonic(), type(e).__name__)
                    logger.error(f"[AI] EasyOCR load failed: {e}")
    return _ocr_reader


def _get_plate_model():
    """Load an explicitly configured local plate detector; never download it."""
    global _plate_model
    if not PLATE_MODEL:
        return None
    if 'plate' in _failures and time.monotonic() - _failures['plate'][0] < 60:
        return None
    if _plate_model is None:
        with _model_lock:
            if _plate_model is None:
                try:
                    from ultralytics import YOLO
                    if not Path(PLATE_MODEL).is_file():
                        raise FileNotFoundError('Configured plate model does not exist')
                    _plate_model = YOLO(PLATE_MODEL)
                    _failures.pop('plate', None)
                    logger.info('[AI] Plate detector loaded')
                except Exception as error:
                    _failures['plate'] = (time.monotonic(), type(error).__name__)
                    logger.error('[AI] Plate detector load failed: %s', type(error).__name__)
    return _plate_model


def warmup_models() -> dict:
    """Load configured local models and run bounded blank-frame smoke checks.

    This is deliberately separate from model installation: startup never
    downloads weights. Failures are captured in ``model_status`` so the API can
    report an unavailable analysis path without preventing camera registry use.
    """
    # Leave CPU capacity for capture, web requests and the operator's browser.
    import torch
    threads = max(1, min(4, int(os.getenv('GUIVIN_AI_THREADS', '2'))))
    torch.set_num_threads(threads)
    cv2.setNumThreads(threads)
    blank = np.zeros((320, 320, 3), dtype=np.uint8)
    yolo = _get_yolo()
    if yolo is not None:
        try:
            with _inference_lock:
                yolo(blank, verbose=False)
        except Exception as error:
            _failures['yolo'] = (time.monotonic(), type(error).__name__)
            logger.error('[AI] YOLO warm-up failed: %s', type(error).__name__)
    ocr = _get_ocr()
    if ocr is not None:
        try:
            with _ocr_lock:
                ocr.readtext(np.full((96, 320), 255, dtype=np.uint8),
                             allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
        except Exception as error:
            _failures['ocr'] = (time.monotonic(), type(error).__name__)
            logger.error('[AI] OCR warm-up failed: %s', type(error).__name__)
    plate = _get_plate_model()
    if plate is not None:
        try:
            with _inference_lock:
                plate(blank, conf=PLATE_DETECTION_THRESHOLD, verbose=False)
        except Exception as error:
            _failures['plate'] = (time.monotonic(), type(error).__name__)
            logger.error('[AI] Plate detector warm-up failed: %s', type(error).__name__)
    return model_status()


# ── Vehicle classes YOLO reports ─────────────────────────────────────────────
VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle"}
PERSON_CLASSES = {"person"}

# Department-specific colours for annotations
DEPT_COLOURS = {
    "Home Department":        (0, 100, 255),
    "RTO Gujarat":            (0, 200, 80),
    "Food & Civil Supplies":  (255, 160, 0),
    "Default":                (30, 160, 255),
}


# ── Core detection function ───────────────────────────────────────────────────
def detect_frame(frame: np.ndarray, camera_id: str = "",
                 department: str = "Default",
                 conf_threshold: float = 0.45, origin: str = 'LIVE') -> Tuple[np.ndarray, List[dict]]:
    """
    Run YOLO on a single frame.
    Returns: (annotated_frame, list_of_detection_dicts)
    Each detection: {class, confidence, bbox, is_vehicle, is_person}
    """
    model = _get_yolo()
    if model is None:
        raise RuntimeError('YOLO unavailable; run model setup and inspect readiness')

    colour = DEPT_COLOURS.get(department, DEPT_COLOURS["Default"])
    detections = []

    try:
        with _inference_lock:
            results = model(frame, conf=conf_threshold, verbose=False)[0]
    except Exception as e:
        logger.warning(f"[AI] YOLO inference error: {e}")
        raise RuntimeError('YOLO inference failed') from e

    for box in results.boxes:
        cls_name = results.names[int(box.cls[0])].lower()
        conf = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        is_vehicle = cls_name in VEHICLE_CLASSES
        is_person = cls_name in PERSON_CLASSES

        # Draw bounding box
        cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
        label = f"{cls_name} {conf:.2f}"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (x1, y1 - lh - 6), (x1 + lw, y1), colour, -1)
        cv2.putText(frame, label, (x1, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

        detections.append({
            "class": cls_name,
            "confidence": conf,
            "bbox": (x1, y1, x2 - x1, y2 - y1),
            "is_vehicle": is_vehicle,
            "is_person": is_person,
        })

    # Draw camera ID overlay
    _draw_overlay(frame, camera_id, origin)
    return frame, detections


def _vehicle_crop(frame: np.ndarray, bbox: tuple) -> np.ndarray:
    x, y, w, h = bbox
    h_img, w_img = frame.shape[:2]
    x1 = max(0, x - 5)
    y1 = max(0, y)
    x2 = min(w_img, x + w + 5)
    y2 = min(h_img, y + h + 20)
    return frame[y1:y2, x1:x2]


def rectify_plate(crop: np.ndarray) -> np.ndarray:
    """Rectify a quadrilateral plate when visible; otherwise normalize safely."""
    if crop is None or crop.size == 0:
        return crop
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = crop.shape[:2]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
        area = cv2.contourArea(polygon)
        if len(polygon) != 4 or area < width * height * 0.08:
            continue
        points = polygon.reshape(4, 2).astype('float32')
        ordered = _order_quad(points)
        target_w, target_h = 320, 96
        destination = np.array([[0, 0], [target_w - 1, 0],
                                [target_w - 1, target_h - 1], [0, target_h - 1]], dtype='float32')
        matrix = cv2.getPerspectiveTransform(ordered, destination)
        return cv2.warpPerspective(crop, matrix, (target_w, target_h))
    return cv2.resize(crop, (320, 96), interpolation=cv2.INTER_CUBIC)


def _order_quad(points):
    ordered = np.zeros((4, 2), dtype='float32')
    sums, differences = points.sum(axis=1), np.diff(points, axis=1).ravel()
    ordered[0], ordered[2] = points[np.argmin(sums)], points[np.argmax(sums)]
    ordered[1], ordered[3] = points[np.argmin(differences)], points[np.argmax(differences)]
    return ordered


def localize_plate(frame: np.ndarray, bbox: tuple):
    """Return a plate crop and provenance for the selected localization path."""
    vehicle = _vehicle_crop(frame, bbox)
    plate_model = _get_plate_model()
    if PLATE_MODEL:
        if plate_model is None:
            raise RuntimeError('Plate detector unavailable; inspect model readiness')
        try:
            with _inference_lock:
                result = plate_model(vehicle, conf=PLATE_DETECTION_THRESHOLD, verbose=False)[0]
        except Exception as error:
            raise RuntimeError('Plate detector inference failed') from error
        boxes = getattr(result, 'boxes', [])
        if not len(boxes):
            return np.empty((0, 0, 3), dtype=np.uint8), 'PLATE_MODEL_NO_DETECTION'
        best = max(boxes, key=lambda box: float(box.conf[0]))
        x1, y1, x2, y2 = map(int, best.xyxy[0].tolist())
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(vehicle.shape[1], x2), min(vehicle.shape[0], y2)
        return vehicle[y1:y2, x1:x2], 'PLATE_MODEL'

    # Until a plate detector is configured, retain the explicit legacy fallback.
    x, y, w, h = bbox
    h_img, w_img = frame.shape[:2]
    x1 = max(0, x - 5)
    y1 = max(0, y + int(h * 0.55))
    x2 = min(w_img, x + w + 5)
    y2 = min(h_img, y + h + 20)
    return frame[y1:y2, x1:x2], 'VEHICLE_BOTTOM_FALLBACK'


def extract_plate_observation(frame: np.ndarray, bbox: tuple, *, ocr_scale: float = 2.5, preprocessing=None) -> dict:
    """
    Localize/rectify a plate candidate and run EasyOCR on the normalized crop.
    Returns a structured observation with plate text, confidence, localization
    source, and whether geometric rectification was applied.
    """
    if not math.isfinite(ocr_scale) or not 1.0 <= ocr_scale <= 2.5:
        raise ValueError('OCR scale must be between 1.0 and 2.5')
    crop, localization = localize_plate(frame, bbox)
    return read_plate_crop(crop, localization=localization, ocr_scale=ocr_scale,
                           preprocessing=preprocessing or OCR_PREPROCESSING)


def read_plate_crop(crop: np.ndarray, *, localization='ANNOTATED_PLATE_CROP', ocr_scale=2.5, preprocessing='legacy'):
    """OCR a supplied plate crop; annotated crops do not test plate detection."""
    if not math.isfinite(ocr_scale) or not 1.0 <= ocr_scale <= 2.5:
        raise ValueError('OCR scale must be between 1.0 and 2.5')
    ocr = _get_ocr()
    if ocr is None:
        raise RuntimeError('OCR unavailable; run model setup and inspect readiness')
    if crop.size == 0:
        return {'plate': '', 'confidence': 0.0, 'localization': localization, 'rectified': False}

    if preprocessing in ('preserve', 'preserve_direct', 'preserve_beam'):
        # Preserve all characters and multi-line layout; no guessed quadrilateral.
        height, width = crop.shape[:2]
        factor = min(480 / width, 256 / height)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (max(1, round(width*factor)), max(1, round(height*factor))),
                          interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
        gray = cv2.copyMakeBorder(gray, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
        from .plate_text import assemble_plate
        try:
            with _ocr_lock:
                if (preprocessing == 'preserve_direct' and width / height >= 2.5
                        and localization != 'VEHICLE_BOTTOM_FALLBACK'):
                    results = ocr.recognize(gray, decoder='beamsearch', beamWidth=5,
                                            allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
                else:
                    results = ocr.readtext(gray, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-',
                                           decoder='beamsearch' if preprocessing == 'preserve_beam' else 'greedy',
                                           beamWidth=5)
        except Exception as error:
            raise RuntimeError('OCR inference failed') from error
        text, confidence = assemble_plate(results)
        return {'plate': text, 'confidence': confidence, 'localization': localization,
                'rectified': False, 'preprocessing': 'ASPECT_PRESERVED_GRAYSCALE'}
    if preprocessing != 'legacy':
        raise ValueError('Unknown OCR preprocessing mode')

    crop = rectify_plate(crop)

    # Pre-process for better OCR
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=ocr_scale, fy=ocr_scale, interpolation=cv2.INTER_LINEAR)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        with _ocr_lock:
            results = ocr.readtext(thresh, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
    except Exception as e:
        logger.warning("[AI] OCR inference failed: %s", type(e).__name__)
        raise RuntimeError("OCR inference failed") from e

    best_text, best_conf = "", 0.0
    for (_, text, conf) in results:
        clean = text.replace(" ", "").upper()
        # Heuristic: Indian plates are 6–12 chars, at least 2 digits
        if 5 <= len(clean) <= 12 and sum(c.isdigit() for c in clean) >= 2:
            if conf > best_conf:
                best_text = clean
                best_conf = conf

    return {'plate': best_text, 'confidence': best_conf,
            'localization': localization, 'rectified': True}


def extract_plate(frame: np.ndarray, bbox: tuple) -> Tuple[str, float]:
    """Backward-compatible OCR API returning (plate, confidence)."""
    observation = extract_plate_observation(frame, bbox)
    return observation['plate'], observation['confidence']


def detect_camera_health(frame: np.ndarray,
                          prev_frame: Optional[np.ndarray] = None,
                          prev_luminance: Optional[float] = None
                          ) -> Tuple[str, str]:
    """
    Detect camera tampering / health issues.
    Returns: (status, reason)  status ∈ {OPERATIONAL, DEGRADED, TAMPERED}
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    luminance = float(np.mean(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # Blackout detection
    if luminance < 8:
        return "TAMPERED", f"Blackout detected (luminance={luminance:.1f})"

    # Whiteout / blinding
    if luminance > 248:
        return "TAMPERED", f"Lens blinded (luminance={luminance:.1f})"

    # Severe blur
    if sharpness < 15:
        return "DEGRADED", f"Severe blur (sharpness={sharpness:.1f})"

    # Sudden luminance drop vs previous frame
    if prev_luminance is not None and abs(luminance - prev_luminance) > 60:
        return "TAMPERED", f"Abrupt luminance change ({prev_luminance:.0f}→{luminance:.0f})"

    return "OPERATIONAL", "OK"


def save_frame(frame: np.ndarray, alert_id: str) -> str:
    """Save annotated key frame for evidence."""
    from .config import EVIDENCE_DIR
    path = EVIDENCE_DIR / "frames" / f"{alert_id}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise OSError('Evidence JPEG encoding failed')
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encoded.tobytes())
    temporary.replace(path)
    return str(path)


def _draw_overlay(frame: np.ndarray, camera_id: str, origin='LIVE'):
    """Draw GUIVIN watermark, camera ID, and timestamp on frame."""
    h, w = frame.shape[:2]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Semi-transparent top bar
    # Only the top 29 rows change; avoid copying/blending the entire HD frame.
    bar = frame[:29]
    overlay = np.full_like(bar, (10, 20, 40))
    cv2.addWeighted(overlay, 0.75, bar, 0.25, 0, dst=bar)

    cv2.putText(frame, f"GUIVIN | {camera_id} | {ts}",
                (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                (30, 160, 255), 1, cv2.LINE_AA)

    # Source provenance, not merely the transport's connection state.
    badge = origin if origin in {'LIVE', 'RECORDED', 'SIMULATED'} else 'UNKNOWN'
    cv2.circle(frame, (w - 22, h - 12), 6, (0, 0, 220), -1)
    badge_width = cv2.getTextSize(badge, cv2.FONT_HERSHEY_SIMPLEX, .42, 1)[0][0]
    cv2.putText(frame, badge, (max(0, w - badge_width - 34), h - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
