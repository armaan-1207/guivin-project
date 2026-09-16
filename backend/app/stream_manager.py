"""
GUIVIN — RTSP Stream Manager
Manages per-camera RTSP capture threads. Provides MJPEG frames to FastAPI.
Supports: RTSP/TCP, local video files, webcam, and Sentinel sandbox streams.
Handles reconnect with exponential backoff as per Sentinel integration spec.
"""
import os
import cv2
import time
import threading
import logging
from typing import Dict, Optional, Generator
from datetime import datetime

logger = logging.getLogger("guivin.streams")

# ── Per-camera worker state ──────────────────────────────────────────────────
class CameraWorker:
    def __init__(self, camera_id: str, stream_url: str, department: str = "Default"):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.department = department
        self.cap: Optional[cv2.VideoCapture] = None
        self.latest_frame = None
        self.latest_annotated = None
        self.health_status = "CONNECTING"
        self.health_reason = ""
        self.fps = 0.0
        self.prev_luminance: Optional[float] = None
        self._running = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.frame_count = 0
        self.detect_every = 5   # Run AI every N frames

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self.cap:
            self.cap.release()

    def get_latest_frame(self):
        with self._lock:
            return self.latest_annotated if self.latest_annotated is not None else self.latest_frame

    def _connect(self) -> bool:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
        if self.cap.isOpened():
            self.health_status = "OPERATIONAL"
            logger.info(f"[Stream] Connected: {self.camera_id} @ {self.stream_url}")
            return True
        logger.warning(f"[Stream] Failed to connect: {self.camera_id}")
        return False

    def _capture_loop(self):
        from .ai_pipeline import detect_frame, detect_camera_health, extract_plate, save_frame
        from .config import CONFIDENCE_THRESHOLD

        delay = 2   # reconnect delay
        while self._running:
            if not self._connect():
                self.health_status = "OFFLINE"
                time.sleep(min(delay, 30))
                delay = min(delay * 2, 30)
                continue
            delay = 2   # reset on successful connect

            while self._running:
                ok, frame = self.cap.read()
                if not ok:
                    logger.warning(f"[Stream] Frame read failed: {self.camera_id}")
                    self.health_status = "OFFLINE"
                    break

                self.frame_count += 1

                # Camera health check (every frame)
                health, reason = detect_camera_health(
                    frame, prev_luminance=self.prev_luminance
                )
                import numpy as np
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                self.prev_luminance = float(np.mean(gray))

                if health != "OPERATIONAL":
                    self.health_status = health
                    self.health_reason = reason
                    # Store raw frame but flag health issue
                    with self._lock:
                        self.latest_frame = frame.copy()
                        annotated = frame.copy()
                        # Draw health warning overlay
                        cv2.rectangle(annotated, (0, 0), (frame.shape[1], frame.shape[0]),
                                      (0, 0, 180), 6)
                        cv2.putText(annotated, f"CAMERA {health}: {reason}",
                                    (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                    (0, 0, 255), 2, cv2.LINE_AA)
                        self.latest_annotated = annotated
                    continue
                else:
                    self.health_status = "OPERATIONAL"
                    self.health_reason = ""

                # AI inference on every Nth frame
                if self.frame_count % self.detect_every == 0:
                    annotated, detections = detect_frame(
                        frame.copy(), self.camera_id, self.department, CONFIDENCE_THRESHOLD
                    )
                    with self._lock:
                        self.latest_frame = frame.copy()
                        self.latest_annotated = annotated
                else:
                    with self._lock:
                        if self.latest_annotated is None:
                            self.latest_annotated = frame.copy()


# ── Global stream registry ────────────────────────────────────────────────────
class StreamManager:
    def __init__(self):
        self._workers: Dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    def add_stream(self, camera_id: str, stream_url: str, department: str = "Default"):
        with self._lock:
            if camera_id in self._workers:
                return  # Already running
            worker = CameraWorker(camera_id, stream_url, department)
            self._workers[camera_id] = worker
            worker.start()
            logger.info(f"[StreamManager] Started worker for {camera_id}")

    def remove_stream(self, camera_id: str):
        with self._lock:
            if camera_id in self._workers:
                self._workers[camera_id].stop()
                del self._workers[camera_id]

    def get_health(self, camera_id: str) -> dict:
        worker = self._workers.get(camera_id)
        if not worker:
            return {"status": "NOT_STREAMING", "reason": ""}
        return {"status": worker.health_status, "reason": worker.health_reason}

    def get_all_health(self) -> Dict[str, dict]:
        return {cid: self.get_health(cid) for cid in self._workers}

    def mjpeg_generator(self, camera_id: str) -> Generator[bytes, None, None]:
        """Yield MJPEG frames for HTTP multipart streaming."""
        worker = self._workers.get(camera_id)
        if not worker:
            # Return a placeholder frame
            yield _placeholder_frame(camera_id)
            return

        while True:
            frame = worker.get_latest_frame()
            if frame is None:
                time.sleep(0.05)
                continue
            ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' +
                       jpg.tobytes() + b'\r\n')
            time.sleep(1 / 15)   # ~15 fps to browser

    def snapshot_jpeg(self, camera_id: str) -> Optional[bytes]:
        """Return single JPEG snapshot bytes."""
        worker = self._workers.get(camera_id)
        if not worker:
            return None
        frame = worker.get_latest_frame()
        if frame is None:
            return None
        ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return jpg.tobytes() if ok else None

    def active_cameras(self) -> list:
        return list(self._workers.keys())


def _placeholder_frame(camera_id: str) -> bytes:
    """Generate a dark placeholder JPEG when no stream is available."""
    import numpy as np
    h, w = 360, 640
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (15, 25, 45)  # Dark navy
    cv2.putText(img, "GUIVIN", (w//2 - 60, h//2 - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (30, 160, 255), 2, cv2.LINE_AA)
    cv2.putText(img, f"{camera_id}", (w//2 - 120, h//2 + 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 180, 220), 1, cv2.LINE_AA)
    cv2.putText(img, "Connecting to stream...", (w//2 - 120, h//2 + 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 130, 160), 1, cv2.LINE_AA)
    _, jpg = cv2.imencode('.jpg', img)
    return (b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + jpg.tobytes() + b'\r\n')


# ── Singleton instance ────────────────────────────────────────────────────────
stream_manager = StreamManager()
