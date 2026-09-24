"""Bounded capture/inference workers. Local playback and API health share real state."""
import cv2
import time
import queue
import threading
import logging
from pathlib import Path
from datetime import datetime
from collections import Counter, deque
from .time_utils import utc_iso
from .config import TRACKER_BACKEND, TRACKER_HIGH_THRESHOLD, TRACKER_LOW_THRESHOLD, TRACKER_MAX_AGE_SECONDS
from .tracking import ByteTrackLite, plate_consensus
from .ocr_budget import select_ocr_tracks
from .source_origin import source_origin
from .config import OCR_MAX_TRACKS_PER_FRAME, OCR_TRACK_INTERVAL, OCR_PREPROCESSING
logger = logging.getLogger('guivin.streams')


class StreamStillStopping(RuntimeError):
    """A replacement must wait until both old worker threads exit."""


class CameraWorker:
    def __init__(self, camera_id, stream_url, department='Default', on_event=None):
        self.camera_id, self.stream_url, self.department = camera_id, stream_url, department
        self.on_event = on_event
        self.cap = None
        self.latest_frame = self.latest_annotated = None
        self.health_status, self.health_reason = 'CONNECTING', ''
        self.ai_status, self.ai_error = 'NOT_STARTED', ''
        self.last_frame_at = self.last_inference_at = None
        self.frame_count = self.processed_frames = self.dropped_frames = 0
        self.ocr_attempts = self.ocr_skipped = 0
        self.last_inference_seconds = 0
        self.last_stage_seconds = {}
        self.detect_every = 5
        self.conf_threshold = .45
        self.max_frames = 0
        self._running = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._jobs = queue.Queue(maxsize=2)
        self._thread = self._ai_thread = None
        self._capture_done = threading.Event()
        self._tracker = ByteTrackLite(TRACKER_HIGH_THRESHOLD, TRACKER_LOW_THRESHOLD,
                                      max_age_seconds=TRACKER_MAX_AGE_SECONDS)
        self.local_file = Path(stream_url).is_file() if '://' not in stream_url else False
        self.origin = source_origin(camera_id, stream_url, self.local_file)
        from .clip_evidence import ClipBuffer
        self.clip_buffer = ClipBuffer()
        from .scene_rules import SceneEvaluator
        self.scene_evaluator = SceneEvaluator()
        self.source_epoch = 0
        self.timing_status = 'WAITING_FOR_PTS'
        self.source_pts_seconds = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name='capture-' + self.camera_id)
        self._thread.start()

    def is_alive(self):
        return any(thread and thread.is_alive() for thread in (self._thread, self._ai_thread))

    def request_stop(self):
        self._running = False
        self._stop.set()
        self.health_status, self.health_reason = 'STOPPING', 'Finishing current capture/inference operation'

    def stop(self):
        self.request_stop()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=7)
        if self._ai_thread and self._ai_thread is not threading.current_thread():
            self._ai_thread.join(timeout=7)
        if self.is_alive():
            self.health_status, self.health_reason = 'STOPPING', 'Waiting for capture or inference to finish'
        else:
            self.health_status = 'STOPPED'

    def get_latest_frame(self):
        with self._lock:
            recent = self.last_inference_at and (datetime.utcnow() - self.last_inference_at).total_seconds() < 1
            frame = self.latest_annotated if recent and self.latest_annotated is not None else self.latest_frame
            return frame.copy() if frame is not None else None

    def _connect(self):
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG,
            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
        if self.cap.isOpened():
            self.health_status, self.health_reason = 'OPERATIONAL', ''
            return True
        return False

    def _capture_loop(self):
        from .ai_pipeline import detect_camera_health
        self._ai_thread = threading.Thread(target=self._inference_loop, daemon=True, name='ai-' + self.camera_id)
        self._ai_thread.start()
        from .source_timing import SourceTimeline
        delay = 2
        try:
            while self._running and not self._stop.is_set():
                if not self._connect():
                    self.health_status, self.health_reason = 'OFFLINE', 'Source unavailable'
                    if self.local_file:
                        break
                    self._stop.wait(delay)
                    delay = min(delay * 2, 30)
                    continue
                delay = 2
                timeline = SourceTimeline()
                self.source_epoch += 1
                if self.source_pts_seconds is not None:
                    self.clip_buffer.close()
                    from .clip_evidence import ClipBuffer
                    self.clip_buffer = ClipBuffer()
                previous_thumbnail = None
                while self._running and not self._stop.is_set():
                    started = time.monotonic()
                    ok, frame = self.cap.read()
                    if not ok:
                        self.health_status = 'ENDED' if self.local_file else 'OFFLINE'
                        break
                    self.frame_count += 1
                    self.last_frame_at = datetime.utcnow()
                    pts = self.cap.get(cv2.CAP_PROP_POS_MSEC) if hasattr(self.cap,'get') else None
                    timing = timeline.read(pts,self.last_frame_at)
                    thumbnail = cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),(32,32))
                    cut = previous_thumbnail is not None and cv2.absdiff(thumbnail,previous_thumbnail).mean() > 80
                    previous_thumbnail = thumbnail
                    if timing and (timing['reset'] or cut):
                        self.source_epoch += 1
                        self.clip_buffer.close()
                        from .clip_evidence import ClipBuffer
                        self.clip_buffer = ClipBuffer()
                    self.timing_status = timing['basis'] if timing else 'PTS_UNAVAILABLE_OR_STALLED'
                    if timing:
                        self.source_pts_seconds = timing['seconds']
                        self.clip_buffer.add(frame, timing['utc'])
                    health, reason = detect_camera_health(frame)
                    if health == 'OPERATIONAL' and not timing:
                        health, reason = 'DEGRADED', 'Preview available; analysis requires advancing source PTS'
                    self.health_status, self.health_reason = health, reason
                    with self._lock:
                        self.latest_frame = frame.copy()
                        self.latest_annotated = frame.copy()
                    if health == 'OPERATIONAL' and timing and self.frame_count % self.detect_every == 0:
                        job = (frame.copy(), timing['utc'], timing['seconds'], self.source_epoch, self.clip_buffer)
                        try:
                            self._jobs.put_nowait(job)
                        except queue.Full:
                            self.dropped_frames += 1
                    if self.max_frames and self.frame_count >= self.max_frames:
                        self._running = False
                    if self.local_file and timing:
                        self._stop.wait(max(0,min(timing['delta'],5) - (time.monotonic() - started)))
                if self.local_file:
                    break
                if self._running:
                    self._stop.wait(delay)
        except Exception as error:
            self.health_status, self.health_reason = 'ERROR', type(error).__name__
            logger.exception('Capture failed for %s', self.camera_id)
        finally:
            self._running = False
            if self.cap and hasattr(self.cap, 'release'):
                self.cap.release()
            self._capture_done.set()
            self._ai_thread.join(timeout=7)
            self.clip_buffer.close()

    def _inference_loop(self):
        from .ai_pipeline import detect_frame, extract_plate_observation
        from .processing import process_detection
        from .watchlist_engine import normalise_plate
        inference_epoch = None
        while not self._capture_done.is_set() or not self._jobs.empty():
            if self._stop.is_set():
                break
            try:
                frame, captured_at, media_seconds, epoch, evidence_buffer = self._jobs.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                self.ai_status = 'PROCESSING'
                inference_started = time.monotonic()
                if epoch != self.source_epoch:
                    continue
                if epoch != inference_epoch:
                    self._tracker = ByteTrackLite(TRACKER_HIGH_THRESHOLD, TRACKER_LOW_THRESHOLD,
                                                  max_age_seconds=TRACKER_MAX_AGE_SECONDS)
                    from .scene_rules import SceneEvaluator
                    self.scene_evaluator = SceneEvaluator()
                    inference_epoch = epoch
                detection_started = time.monotonic()
                annotated, detections = detect_frame(frame.copy(), self.camera_id, self.department,
                                                     conf_threshold=self.conf_threshold, origin=self.origin)
                detection_seconds = time.monotonic() - detection_started
                ocr_seconds = 0.0
                if epoch != self.source_epoch:
                    continue
                now=media_seconds
                for event in self.scene_evaluator.process(self.camera_id,detections,frame,now,captured_at,self.origin):
                    evidence_buffer.request(event['alert']['id'],self.camera_id,captured_at)
                    if self.on_event:
                        self.on_event(event)
                vehicle_detections = [det for det in detections if det.get('is_vehicle')]
                track_ids = self._tracker.update(vehicle_detections, now)
                ocr_selected = select_ocr_tracks(self._tracker.tracks, track_ids, now,
                                                OCR_MAX_TRACKS_PER_FRAME, OCR_TRACK_INTERVAL)
                from .aci_learning import observe
                from .database import SessionLocal
                with SessionLocal() as learning_db:
                    observe(learning_db, self.camera_id, detections,
                            max((now - self._tracker.tracks[t]['first'] for t in track_ids if t), default=0),
                            captured_at, self.origin)
                for det, track_id in zip(vehicle_detections, track_ids):
                    if not track_id:
                        continue
                    if self._stop.is_set():
                        break
                    track = self._tracker.tracks[track_id]
                    if track_id in ocr_selected:
                        track['ocr_attempt_at'] = now
                        self.ocr_attempts += 1
                        ocr_started = time.monotonic()
                        observation = extract_plate_observation(frame, det['bbox'])
                        ocr_seconds += time.monotonic() - ocr_started
                    else:
                        self.ocr_skipped += 1
                        observation = {'localization': 'OCR_DEFERRED_BUDGET'}
                    raw = observation.get('plate', '')
                    confidence = float(observation.get('confidence', 0.0))
                    localization = observation.get('localization', '')
                    rectified = bool(observation.get('rectified', False))
                    plate=normalise_plate(raw)
                    # Deferred work is not an unreadable observation or another vote.
                    # Do not attach a previous plate to an unexamined current frame.
                    accepted, accepted_confidence = (plate_consensus(track, plate, confidence)
                        if track_id in ocr_selected else ('', 0.0))
                    if self._stop.is_set():
                        break
                    event=process_detection(self.camera_id, det, accepted, accepted_confidence,
                                            frame, self.origin, track_id, now-track['first'], captured_at,
                                            raw_plate=raw, plate_localization=localization,
                                            plate_rectified=rectified, raw_plate_confidence=confidence)
                    if event:
                        evidence_buffer.request(event['alert']['id'], self.camera_id, captured_at)
                        if self.on_event:
                            self.on_event(event)
                with self._lock:
                    self.latest_annotated=annotated
                self.last_inference_at=datetime.utcnow()
                self.last_inference_seconds = time.monotonic() - inference_started
                self.last_stage_seconds = {
                    'detection': detection_seconds,
                    'plate_localization_and_ocr': ocr_seconds,
                    'tracking_rules_and_persistence': max(0.0, self.last_inference_seconds - detection_seconds - ocr_seconds),
                }
                self.ai_status, self.ai_error='READY',''
                self.processed_frames += 1
            except Exception as error:
                self.ai_status, self.ai_error='ERROR', type(error).__name__
                logger.error('Inference failed for %s: %s', self.camera_id, type(error).__name__)
            finally:
                self._jobs.task_done()

class StreamManager:
    def __init__(self):
        self._workers={}
        self._lock=threading.RLock()
        self.on_event=None

    def add_stream(self, camera_id, stream_url, department='Default'):
        with self._lock:
            existing=self._workers.get(camera_id)
            if existing:
                if existing.stream_url == stream_url and existing._running:
                    return
                existing.request_stop()
                if existing.is_alive():
                    raise StreamStillStopping('Previous worker is still stopping')
            worker=CameraWorker(camera_id,stream_url,department,self.on_event)
            self._workers[camera_id]=worker
            try:
                worker.start()
            except Exception:
                worker.request_stop()
                if not worker.is_alive():
                    self._workers.pop(camera_id, None)
                    worker.clip_buffer.close()
                raise

    def remove_stream(self,camera_id):
        with self._lock:
            worker=self._workers.get(camera_id)
            if worker:
                worker.stop()
                if not worker.is_alive():
                    self._workers.pop(camera_id,None)

    def request_stop(self, camera_id):
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker:
                worker.request_stop()

    def stop_all(self):
        for camera_id in list(self._workers):
            self.remove_stream(camera_id)

    def get_health(self,camera_id):
        worker=self._workers.get(camera_id)
        if worker and worker._stop.is_set() and not worker.is_alive():
            with self._lock:
                if self._workers.get(camera_id) is worker:
                    self._workers.pop(camera_id, None)
            worker = None
        if not worker:
            return {'status':'NOT_STREAMING','reason':''}
        from .database import SessionLocal, CameraDB
        with SessionLocal() as db:
            camera = db.get(CameraDB, camera_id)
            freshness = (camera.source_freshness_seconds if camera else 0) or 15
        stale=worker.last_frame_at and (datetime.utcnow()-worker.last_frame_at).total_seconds()>freshness
        status='STALE' if stale and worker._running else worker.health_status
        return {'status':status,'reason':worker.health_reason,'ai_status':worker.ai_status,'ai_error':worker.ai_error,
                'last_frame_at':utc_iso(worker.last_frame_at),'last_inference_at':utc_iso(worker.last_inference_at),
                'frames':worker.frame_count,'processed_frames':worker.processed_frames,'dropped_frames':worker.dropped_frames,
                'ocr_attempts':worker.ocr_attempts,'ocr_deferred':worker.ocr_skipped,
                'last_inference_seconds':worker.last_inference_seconds,
                'last_stage_seconds': dict(worker.last_stage_seconds),
                'ocr_preprocessing': OCR_PREPROCESSING,
                'origin':worker.origin,'queue_depth':worker._jobs.qsize(),
                'timing_status':worker.timing_status,'source_pts_seconds':worker.source_pts_seconds,
                'tracker': TRACKER_BACKEND}

    def get_all_health(self):
        return {cid:self.get_health(cid) for cid in list(self._workers)}

    def active_cameras(self):
        return [cid for cid,w in list(self._workers.items()) if w._running]

    def mjpeg_generator(self,camera_id):
        worker=self._workers.get(camera_id)
        if not worker:
            return
        while not worker._stop.is_set():
            frame=worker.get_latest_frame()
            if frame is not None:
                height, width = frame.shape[:2]
                if width > 960:
                    frame = cv2.resize(frame, (960, max(1, round(height * 960 / width))))
                ok,jpg=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,70])
                if ok:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'+jpg.tobytes()+b'\r\n'
            if not worker._running:
                return
            worker._stop.wait(1/6)

    def snapshot_jpeg(self,camera_id):
        worker=self._workers.get(camera_id)
        frame=worker.get_latest_frame() if worker else None
        if frame is None:
            return None
        ok,jpg=cv2.imencode('.jpg',frame)
        return jpg.tobytes() if ok else None

stream_manager=StreamManager()
