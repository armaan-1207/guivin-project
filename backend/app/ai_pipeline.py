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
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple, List

logger = logging.getLogger("guivin.ai")

# ── Lazy-load YOLO & EasyOCR (heavy imports) ─────────────────────────────────
_yolo_model = None
_ocr_reader = None
_model_lock = threading.Lock()


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        with _model_lock:
            if _yolo_model is None:
                try:
                    from ultralytics import YOLO
                    _yolo_model = YOLO("yolov8n.pt")   # downloads on first run
                    logger.info("[AI] YOLOv8n model loaded")
                except Exception as e:
                    logger.error(f"[AI] YOLO load failed: {e}")
    return _yolo_model


def _get_ocr():
    global _ocr_reader
    if _ocr_reader is None:
        with _model_lock:
            if _ocr_reader is None:
                try:
                    import easyocr
                    _ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
                    logger.info("[AI] EasyOCR reader loaded")
                except Exception as e:
                    logger.error(f"[AI] EasyOCR load failed: {e}")
    return _ocr_reader


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
                 conf_threshold: float = 0.45) -> Tuple[np.ndarray, List[dict]]:
    """
    Run YOLO on a single frame.
    Returns: (annotated_frame, list_of_detection_dicts)
    Each detection: {class, confidence, bbox, is_vehicle, is_person}
    """
    model = _get_yolo()
    if model is None:
        return frame, []

    colour = DEPT_COLOURS.get(department, DEPT_COLOURS["Default"])
    detections = []

    try:
        results = model(frame, conf=conf_threshold, verbose=False)[0]
    except Exception as e:
        logger.warning(f"[AI] YOLO inference error: {e}")
        return frame, []

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
    _draw_overlay(frame, camera_id)
    return frame, detections


def extract_plate(frame: np.ndarray, bbox: tuple) -> Tuple[str, float]:
    """
    Crop vehicle region from frame and run EasyOCR to extract plate text.
    Returns: (plate_text, confidence)
    """
    ocr = _get_ocr()
    if ocr is None:
        return "", 0.0

    x, y, w, h = bbox
    h_img, w_img = frame.shape[:2]

    # Expand bbox slightly to include plate area below vehicle
    x1 = max(0, x - 5)
    y1 = max(0, y + int(h * 0.55))   # Bottom half likely has plate
    x2 = min(w_img, x + w + 5)
    y2 = min(h_img, y + h + 20)

    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return "", 0.0

    # Pre-process for better OCR
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_LINEAR)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    try:
        results = ocr.readtext(thresh, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-')
    except Exception as e:
        logger.debug(f"[AI] OCR error: {e}")
        return "", 0.0

    best_text, best_conf = "", 0.0
    for (_, text, conf) in results:
        clean = text.replace(" ", "").upper()
        # Heuristic: Indian plates are 6–12 chars, at least 2 digits
        if 5 <= len(clean) <= 12 and sum(c.isdigit() for c in clean) >= 2:
            if conf > best_conf:
                best_text = clean
                best_conf = conf

    return best_text, best_conf


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
    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return str(path)


def _draw_overlay(frame: np.ndarray, camera_id: str):
    """Draw GUIVIN watermark, camera ID, and timestamp on frame."""
    h, w = frame.shape[:2]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Semi-transparent top bar
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 28), (10, 20, 40), -1)
    frame[:] = cv2.addWeighted(overlay, 0.75, frame, 0.25, 0)

    cv2.putText(frame, f"GUIVIN | {camera_id} | {ts}",
                (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                (30, 160, 255), 1, cv2.LINE_AA)

    # Bottom-right: LIVE badge
    cv2.circle(frame, (w - 22, h - 12), 6, (0, 0, 220), -1)
    cv2.putText(frame, "LIVE", (w - 50, h - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
