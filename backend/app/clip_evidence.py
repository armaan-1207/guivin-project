"""Bounded sampled clip evidence for the single-process prototype.

AVI/MJPEG is intentionally separate from the existing JPEG evidence endpoint.
Encoding runs on a bounded per-camera worker; gaps/early termination are explicit.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import threading
import uuid

import cv2
import numpy as np

from .config import EVIDENCE_DIR
from .database import SessionLocal, ClipEvidenceDB, AlertDB, CameraDB
from .blockchain_ledger import anchor_evidence, ledger_lock


class ClipBuffer:
    pre = 5
    post = 10
    fps = 5
    max_bytes = 32 * 1024 * 1024
    max_pending = 8

    def __init__(self):
        self.frames = deque(maxlen=100)
        self.pending = {}
        self.lock = threading.RLock()
        self.closed = False
        self.last_sample = None
        self.encoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix='clip-encoder')
        self.encoding = set()

    def add(self, frame, timestamp):
        if self.last_sample and (timestamp - self.last_sample).total_seconds() < 1 / self.fps:
            return
        self.last_sample = timestamp
        height, width = frame.shape[:2]
        scale = min(1, 640 / max(width, height))
        scaled = cv2.resize(frame, (max(2, int(width * scale) // 2 * 2),
                                    max(2, int(height * scale) // 2 * 2)))
        ok, encoded = cv2.imencode('.jpg', scaled, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            return
        item = (timestamp, encoded.tobytes())
        ready = []
        with self.lock:
            if self.closed:
                return
            self.frames.append(item)
            while self.frames and (timestamp - self.frames[0][0]).total_seconds() > self.pre + 2:
                self.frames.popleft()
            while sum(len(data) for _, data in self.frames) > self.max_bytes:
                self.frames.popleft()
            for alert_id, request in list(self.pending.items()):
                if timestamp <= request['event'] + timedelta(seconds=self.post):
                    request['frames'].append(item)
                size = sum(len(data) for _, data in request['frames'])
                if timestamp >= request['event'] + timedelta(seconds=self.post) or size > self.max_bytes:
                    ready.append((alert_id, self.pending.pop(alert_id)))
            for alert_id, request in ready:
                self._submit(alert_id, request)

    def _submit(self, alert_id, request):
        # Caller holds the buffer lock. Encoding + pending requests share the cap.
        self.encoding = {future for future in self.encoding if not future.done()}
        self.encoding.add(self.encoder.submit(self.finish, alert_id, request))

    def request(self, alert_id, camera_id, event_time):
        with self.lock:
            with SessionLocal() as db:
                if db.get(ClipEvidenceDB, alert_id):
                    return
                self.encoding = {future for future in self.encoding if not future.done()}
                status = 'PENDING' if len(self.pending) + len(self.encoding) < self.max_pending else 'UNAVAILABLE'
                db.add(ClipEvidenceDB(alert_id=alert_id, camera_id=camera_id, status=status,
                                     reason='' if status == 'PENDING' else 'Pending clip capacity reached'))
                db.commit()
            if status != 'PENDING':
                return
            request = {'event': event_time, 'frames': [item for item in self.frames
                if event_time - timedelta(seconds=self.pre) <= item[0] <= event_time + timedelta(seconds=self.post)]}
            if not self.closed:
                self.pending[alert_id] = request
                return
        self.finish(alert_id, request)

    def close(self):
        with self.lock:
            self.closed = True
            pending, self.pending = self.pending, {}
            for alert_id, request in pending.items():
                self._submit(alert_id, request)
        self.encoder.shutdown(wait=True)

    def finish(self, alert_id, request):
        frames = request['frames']
        path = EVIDENCE_DIR / ('CLIP-' + uuid.uuid4().hex + '.avi')
        temporary = path.with_name(path.stem + '.partial.avi')
        writer = None
        try:
            if not frames:
                raise ValueError('No buffered frames')
            EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            first = cv2.imdecode(np.frombuffer(frames[0][1], np.uint8), cv2.IMREAD_COLOR)
            height, width = first.shape[:2]
            writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*'MJPG'), self.fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError('Clip encoder unavailable')
            for _, data in frames:
                frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                writer.write(cv2.resize(frame, (width, height)))
            writer.release()
            writer = None
            check = cv2.VideoCapture(str(temporary))
            try:
                if not check.read()[0] or int(check.get(cv2.CAP_PROP_FRAME_COUNT)) != len(frames):
                    raise RuntimeError('Clip playback validation failed')
            finally:
                check.release()
            temporary.replace(path)
            pre = max(0, (request['event'] - frames[0][0]).total_seconds())
            post = max(0, (frames[-1][0] - request['event']).total_seconds())
            gaps = any((b[0] - a[0]).total_seconds() > .5 for a, b in zip(frames, frames[1:]))
            complete = pre >= self.pre - .25 and post >= self.post - .25 and not gaps
            with ledger_lock, SessionLocal() as db:
                row = db.get(ClipEvidenceDB, alert_id)
                alert = db.get(AlertDB, alert_id)
                camera = db.get(CameraDB, row.camera_id)
                anchor_evidence(db, alert_id, row.camera_id, str(path), alert.alert_type,
                                alert.risk_score, camera.department, commit=False)
                row.path, row.status = str(path), 'COMPLETE' if complete else 'PARTIAL'
                row.reason = '' if complete else 'Short pre/post window, sampling gap or buffer limit'
                row.started_at, row.ended_at = frames[0][0], frames[-1][0]
                row.frame_count, row.pre_seconds, row.post_seconds = len(frames), pre, post
                alert.clip_path = str(path)
                db.commit()
        except Exception as error:
            if writer:
                writer.release()
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            with SessionLocal() as db:
                row = db.get(ClipEvidenceDB, alert_id)
                if row:
                    row.status, row.reason = 'FAILED', type(error).__name__
                    db.commit()


def recover_pending_clips():
    """An interrupted process cannot recover its in-memory video buffer."""
    with SessionLocal() as db:
        db.query(ClipEvidenceDB).filter_by(status='PENDING').update(
            {'status': 'UNAVAILABLE', 'reason': 'Process restarted before clip finalization'})
        db.commit()
