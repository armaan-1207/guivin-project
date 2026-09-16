#!/usr/bin/env python3
"""
GUIVIN — Standalone ANPR + Vehicle Detection Script
Processes a video file or RTSP stream and produces the Sentinel submission CSV report.

Usage:
  python anpr_runner.py --source /path/to/video.mp4 --camera-id DEMO-001 --output report.csv
  python anpr_runner.py --source rtsp://host:8554/stream/1 --camera-id SENTINEL-001

Requirements: pip install ultralytics easyocr opencv-python-headless
"""
import os, sys, csv, re, hashlib, argparse
from datetime import datetime
from pathlib import Path

# ── Args ─────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="GUIVIN ANPR Runner")
parser.add_argument("--source",    default="0",          help="Video file path, RTSP URL, or 0 for webcam")
parser.add_argument("--camera-id", default="DEMO-001",   help="Camera ID label")
parser.add_argument("--dept",      default="Home Department", help="Department name")
parser.add_argument("--district",  default="Ahmedabad",  help="District")
parser.add_argument("--output",    default="GUIVIN_Detection_Report.csv", help="Output CSV filename")
parser.add_argument("--every",     default=5, type=int,  help="Run AI every N frames (default 5)")
parser.add_argument("--conf",      default=0.45, type=float, help="YOLO confidence threshold")
parser.add_argument("--max-frames",default=0, type=int,  help="Stop after N frames (0 = unlimited)")
args = parser.parse_args()

# ── Watchlist (mirrors data/watchlist.json) ───────────────────────────────────
WATCHLIST = {
    "GJ01AB1234": ("STOLEN",  "VAHAN"),
    "GJ01CD5678": ("WANTED",  "eGujCop/CCTNS"),
    "GJ05EF9012": ("BLACKLISTED", "VAHAN"),
    "GJ18GH3456": ("STOLEN",  "VAHAN"),
    "MH12IJ7890": ("WANTED",  "eGujCop/CCTNS"),
    "GJ27KL2345": ("MISSING_LINK", "Missing Persons"),
    "RJ14OP1111": ("STOLEN",  "VAHAN"),
}

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle", "bicycle"}
PLATE_RE = re.compile(r'[^A-Z0-9]')

def normalise(plate: str) -> str:
    return PLATE_RE.sub('', plate.upper())

def check_watchlist(plate: str):
    n = normalise(plate)
    return WATCHLIST.get(n)

def sha256_frame(frame) -> str:
    import cv2
    _, buf = cv2.imencode('.jpg', frame)
    return hashlib.sha256(buf.tobytes()).hexdigest()

# ── Load models ───────────────────────────────────────────────────────────────
print("[GUIVIN] Loading YOLOv8n...", flush=True)
try:
    from ultralytics import YOLO
    yolo = YOLO("yolov8n.pt")
    print("[GUIVIN] YOLOv8n ready", flush=True)
except Exception as e:
    print(f"[GUIVIN] YOLO load failed: {e}. Running without detection.", flush=True)
    yolo = None

print("[GUIVIN] Loading EasyOCR...", flush=True)
try:
    import easyocr
    ocr = easyocr.Reader(['en'], gpu=False, verbose=False)
    print("[GUIVIN] EasyOCR ready", flush=True)
except Exception as e:
    print(f"[GUIVIN] EasyOCR load failed: {e}. Running without ANPR.", flush=True)
    ocr = None

import cv2
import numpy as np

# ── Connect to source ─────────────────────────────────────────────────────────
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
source = args.source
if source.isdigit():
    source = int(source)

cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
if not cap.isOpened():
    print(f"[GUIVIN] ERROR: Cannot open source: {args.source}")
    sys.exit(1)

fps_src = cap.get(cv2.CAP_PROP_FPS) or 25
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
print(f"[GUIVIN] Source opened | FPS: {fps_src:.1f} | Total frames: {total or 'live'}", flush=True)

# ── Output CSV ────────────────────────────────────────────────────────────────
output_path = Path(args.output)
csv_file = open(output_path, "w", newline="", encoding="utf-8")
writer = csv.DictWriter(csv_file, fieldnames=[
    "Timestamp (IST)", "Camera ID", "Department", "District",
    "Vehicle Class", "Detected Plate", "Plate Confidence (%)",
    "Watchlist Match", "Watchlist Source", "Risk Score",
    "Frame Hash (SHA-256)"
])
writer.writeheader()

# ── Processing loop ───────────────────────────────────────────────────────────
frame_count = 0
detections_written = 0

print(f"\n[GUIVIN] Processing — camera: {args.camera_id} | Every {args.every} frames | Ctrl+C to stop\n", flush=True)

try:
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_count += 1
        if args.max_frames and frame_count > args.max_frames:
            break
        if frame_count % args.every != 0:
            continue

        ts_ist = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        frame_hash = sha256_frame(frame)

        # ── YOLO detection ────────────────────────────────────────────────────
        vehicles_found = []
        if yolo is not None:
            try:
                results = yolo(frame, conf=args.conf, verbose=False)[0]
                for box in results.boxes:
                    cls_name = results.names[int(box.cls[0])].lower()
                    conf_v = float(box.conf[0])
                    if cls_name in VEHICLE_CLASSES:
                        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                        vehicles_found.append((cls_name, conf_v, x1, y1, x2, y2))
                        # Draw box
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (30, 144, 255), 2)
                        cv2.putText(frame, f"{cls_name} {conf_v:.2f}",
                                    (x1, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.5, (255, 255, 255), 1, cv2.LINE_AA)
            except Exception as e:
                print(f"[YOLO] Error frame {frame_count}: {e}", flush=True)

        # ── ANPR per vehicle ──────────────────────────────────────────────────
        plates_this_frame = []
        if ocr is not None and vehicles_found:
            h_img, w_img = frame.shape[:2]
            for cls_name, conf_v, x1, y1, x2, y2 in vehicles_found:
                # Crop bottom-half of vehicle (likely plate zone)
                crop_y1 = max(0, y1 + int((y2 - y1) * 0.55))
                crop_y2 = min(h_img, y2 + 20)
                crop = frame[crop_y1:crop_y2, max(0, x1-5):min(w_img, x2+5)]
                if crop.size == 0:
                    continue
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, None, fx=2.5, fy=2.5,
                                  interpolation=cv2.INTER_LINEAR)
                _, thresh = cv2.threshold(gray, 0, 255,
                                          cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                try:
                    ocr_results = ocr.readtext(
                        thresh,
                        allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'
                    )
                    for (_, text, conf_p) in ocr_results:
                        clean = text.replace(" ", "").upper()
                        if 5 <= len(clean) <= 12 and sum(c.isdigit() for c in clean) >= 2:
                            plates_this_frame.append((cls_name, conf_v, clean, conf_p))
                            # Annotate plate on frame
                            cv2.putText(frame, clean,
                                        (x1, y2 + 18),
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.7, (0, 255, 100), 2, cv2.LINE_AA)
                except Exception:
                    pass

        # ── Write rows ────────────────────────────────────────────────────────
        if plates_this_frame:
            for (cls_name, conf_v, plate, conf_p) in plates_this_frame:
                wl = check_watchlist(plate)
                wl_match = "YES" if wl else "NO"
                wl_source = wl[1] if wl else ""
                risk = 70 if (wl and wl[0] in ("STOLEN", "WANTED")) else (40 if wl else 0)

                row = {
                    "Timestamp (IST)":       ts_ist,
                    "Camera ID":             args.camera_id,
                    "Department":            args.dept,
                    "District":              args.district,
                    "Vehicle Class":         cls_name,
                    "Detected Plate":        plate,
                    "Plate Confidence (%)":  f"{conf_p*100:.1f}",
                    "Watchlist Match":       wl_match,
                    "Watchlist Source":      wl_source,
                    "Risk Score":            risk,
                    "Frame Hash (SHA-256)":  frame_hash,
                }
                writer.writerow(row)
                csv_file.flush()
                detections_written += 1

                alert_sym = "🚨" if wl else "✓"
                wl_info = f" [{wl[0]} – {wl[1]}]" if wl else ""
                print(f"  {alert_sym} Frame {frame_count:5d} | {cls_name:12s} | {plate:15s} "
                      f"| conf: {conf_p:.2f}{wl_info}", flush=True)
        elif vehicles_found:
            # Vehicle detected but no plate read — write detection-only row
            for cls_name, conf_v, *_ in vehicles_found:
                row = {
                    "Timestamp (IST)":       ts_ist,
                    "Camera ID":             args.camera_id,
                    "Department":            args.dept,
                    "District":              args.district,
                    "Vehicle Class":         cls_name,
                    "Detected Plate":        "",
                    "Plate Confidence (%)":  "0.0",
                    "Watchlist Match":       "NO",
                    "Watchlist Source":      "",
                    "Risk Score":            0,
                    "Frame Hash (SHA-256)":  frame_hash,
                }
                writer.writerow(row)
                csv_file.flush()
                detections_written += 1

        # ── Draw overlay ──────────────────────────────────────────────────────
        ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 28), (10, 20, 40), -1)
        frame[:] = cv2.addWeighted(overlay, 0.75, frame, 0.25, 0)
        cv2.putText(frame, f"GUIVIN | {args.camera_id} | {ts_str}",
                    (8, 19), cv2.FONT_HERSHEY_SIMPLEX,
                    0.48, (30, 160, 255), 1, cv2.LINE_AA)

        # Show live window (skip if headless)
        try:
            cv2.imshow("GUIVIN — ANPR Runner", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("\n[GUIVIN] Stopped by user.", flush=True)
                break
        except Exception:
            pass  # headless

        if frame_count % 100 == 0:
            print(f"  [Frame {frame_count}] Detections so far: {detections_written}", flush=True)

except KeyboardInterrupt:
    print("\n[GUIVIN] Interrupted.", flush=True)

finally:
    cap.release()
    csv_file.close()
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    print(f"\n[GUIVIN] Done — {frame_count} frames processed, {detections_written} detections")
    print(f"[GUIVIN] Report saved: {output_path.resolve()}")
