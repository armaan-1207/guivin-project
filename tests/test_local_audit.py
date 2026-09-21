"""Local acceptance checks. Known defects intentionally fail until fixed.

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
Uses a temporary database; never changes the running dashboard's data.
"""
import csv
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = tempfile.TemporaryDirectory(prefix="guivin-audit-")
from app import config
config.DATABASE_URL = "sqlite:///" + str(Path(TEMP.name) / "audit.db")
config.REPORTS_DIR = Path(TEMP.name)
config.EVIDENCE_DIR = Path(TEMP.name)
from fastapi.testclient import TestClient
from app.main import app
from app import database, alert_manager, blockchain_ledger, report_generator


class LocalAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = TestClient(app)
        cls.client = cls.context.__enter__()
        cls.camera = cls.client.get("/api/cameras").json()[0]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None, None, None)
        database.engine.dispose()
        TEMP.cleanup()

    def setUp(self):
        alert_manager._recent_alerts.clear()

    def demo(self, **overrides):
        body = {"camera_id": self.camera, "plate_number": "GJ-01-AB-1234"}
        body.update(overrides)
        response = self.client.post("/api/alerts/demo/anpr", json=body)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_access_department_and_roles(self):
        import json
        from app import auth
        cameras = self.client.get('/api/cameras').json()
        dept = cameras[0]['department']
        foreign = next(c for c in cameras if c['department'] != dept)
        accounts = Path(TEMP.name) / 'accounts.json'
        salt = 'ab' * 16
        accounts.write_text(json.dumps({'viewer': {'role':'viewer', 'department':dept,
            'salt':salt, 'hash':auth.password_hash('test-password', salt)}}))
        with patch.object(auth, 'USER_FILE', str(accounts)):
            try:
                self.assertEqual(self.client.get('/api/cameras').status_code, 401)
                self.assertEqual(self.client.post('/api/auth/login', json={'username':'viewer','password':'test-password'}).status_code, 200)
                visible = self.client.get('/api/cameras').json()
                self.assertTrue(visible)
                self.assertTrue(all(c['department'] == dept for c in visible))
                self.assertEqual(self.client.get('/api/cameras/' + foreign['id']).status_code, 404)
                self.assertEqual(self.client.get('/api/stream/' + foreign['id'] + '/health').status_code, 404)
                self.assertEqual(self.client.post('/api/journey/seed').status_code, 403)
                self.assertEqual(self.client.post('/api/alerts/demo/anpr', json={'camera_id':self.camera, 'plate_number':'GJ01AB1234'}).status_code, 403)
                self.assertEqual(self.client.get('/api/health').json()['cameras_registered'], len(visible))
                self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
                self.assertEqual(self.client.get('/api/alerts').status_code, 401)
            finally:
                self.client.cookies.clear()
                auth.SESSIONS.clear()
        self.assertEqual(self.client.post('/api/journey/seed', headers={'Origin':'https://evil.example'}).status_code, 403)

    def test_evidence_pipeline_and_tampering(self):
        import numpy as np
        from app.processing import process_detection
        frame = np.full((120, 160, 3), 180, dtype=np.uint8)
        detection = {'class':'car', 'confidence':.91, 'bbox':(0,0,160,120)}
        event = process_detection(self.camera, detection, 'GJ01AB1234', .88, frame, 'RECORDED', 'track-test')
        self.assertIsNotNone(event)
        alert = event['alert']
        self.assertEqual(alert['origin'], 'RECORDED')
        self.assertEqual(self.client.get(alert['evidence_url']).status_code, 200)
        def verify():
            return self.client.post('/api/blockchain/verify', json={'alert_id':alert['id']}).json()
        self.assertTrue(verify()['verified'])
        with database.SessionLocal() as db:
            record = db.query(database.BlockchainLedgerDB).filter_by(alert_id=alert['id']).one()
            evidence = Path(record.evidence_path)
            self.assertEqual(db.query(database.DetectionDB).filter_by(alert_id=alert['id']).count(), 1)
        original = evidence.read_bytes()
        try:
            evidence.write_bytes(original + b'tampered')
            self.assertFalse(verify()['verified'])
            evidence.unlink()
            self.assertEqual(verify()['evidence_status'], 'MISSING')
        finally:
            evidence.write_bytes(original)
        self.assertTrue(verify()['verified'])

    def test_hls_nested_and_guard(self):
        from app.hls_proxy import rewrite_manifest, validate_asset
        from fastapi import HTTPException
        from urllib.parse import unquote
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="keys/a.key?token=abc"\nvideo/low.m3u8?token=abc\n'
        result = unquote(rewrite_manifest(text, 'https://cctv.corp8.cloud/cam1/index.m3u8', 'SENTINEL-CAM1', '/proxy').decode())
        self.assertIn('https://cctv.corp8.cloud/cam1/video/low.m3u8?token=abc', result)
        self.assertIn('https://cctv.corp8.cloud/cam1/keys/a.key?token=abc', result)
        for url in ['http://127.0.0.1/secret', 'https://cctv.corp8.cloud/cam1/../cam2/key', 'https://evil.example/cam1/key']:
            with self.assertRaises(HTTPException):
                validate_asset('SENTINEL-CAM1', url)

    def test_bulk_camera_validation(self):
        body = {'id':'BULK-TEST','name':'Bulk test','department':'Test','district':'Test','lat':23,'lon':72,'coverage_radius_m':100}
        self.assertEqual(self.client.post('/api/cameras/bulk', json={'cameras':[body,body]}).status_code, 422)
        self.assertEqual(self.client.get('/api/cameras/BULK-TEST').status_code, 404)
        try:
            self.assertEqual(self.client.post('/api/cameras/bulk', json={'cameras':[body]}).status_code, 200)
            result = self.client.get('/api/registry/coverage?lat=23&lon=72').json()
            self.assertIn('BULK-TEST', result['camera_ids'])
            import json
            with database.SessionLocal() as db:
                rows = db.query(database.AuditDB).filter(
                    database.AuditDB.resource == '/api/cameras/BULK-TEST'
                ).all()
                self.assertTrue(any(json.loads(row.details).get('fields') for row in rows if row.details))
                self.assertFalse(any('stream_url' in row.details for row in rows))
        finally:
            with database.SessionLocal() as db:
                db.query(database.CameraDB).filter_by(id='BULK-TEST').delete()
                db.commit()

    def test_registry_atomic_failure_and_merge(self):
        from app import camera_registry
        body = {'id':'ATOMIC-TEST', 'name':'Atomic', 'department':'Test',
                'district':'Test', 'lat':23, 'lon':72, 'ownership':'preserve'}
        original = camera_registry._audit_camera_change
        def fail_second(db, camera, action, details):
            original(db, camera, action, details)
            if camera.id == 'ATOMIC-SECOND':
                raise RuntimeError('injected audit failure')
        with TestClient(app, raise_server_exceptions=False) as client:
            with patch.object(camera_registry, '_audit_camera_change', side_effect=fail_second):
                response = client.post('/api/cameras/bulk', json={'cameras':[
                    body, dict(body, id='ATOMIC-SECOND')]})
            self.assertEqual(response.status_code, 500)
        with database.SessionLocal() as db:
            self.assertIsNone(db.get(database.CameraDB, 'ATOMIC-TEST'))
            self.assertEqual(db.query(database.AuditDB).filter(
                database.AuditDB.resource.in_(['/api/cameras/ATOMIC-TEST', '/api/cameras/ATOMIC-SECOND'])).count(), 0)
        try:
            self.assertEqual(self.client.post('/api/cameras', json=body).status_code, 200)
            retry = dict(body, name='Updated')
            retry.pop('ownership')
            self.assertEqual(self.client.post('/api/cameras', json=retry).json()['ownership'], 'preserve')
            self.assertEqual(self.client.patch('/api/cameras/ATOMIC-TEST', json={'name':None}).status_code, 422)
            self.assertEqual(self.client.get('/api/cameras/ATOMIC-TEST').json()['name'], 'Updated')
            with database.SessionLocal() as db:
                db.info['department'] = 'Test'
                rows = db.query(database.AuditDB).filter_by(resource='/api/cameras/ATOMIC-TEST').all()
                self.assertTrue(rows)
                self.assertTrue(all(row.actor == 'local-developer' for row in rows))
            entries = self.client.get('/api/registry/audit?limit=1000').json()
            self.assertTrue(any(e['resource'] == '/api/cameras/ATOMIC-TEST' and e['details'].get('after', {}).get('ownership') == 'preserve' for e in entries))
        finally:
            with database.SessionLocal() as db:
                db.query(database.CameraDB).filter_by(id='ATOMIC-TEST').delete()
                db.commit()

    def test_operator_cannot_configure_camera(self):
        from app import auth
        operator = dict(auth.LOCAL, role='operator')
        with patch.object(auth, 'identity', return_value=operator), patch('app.main.stream_manager.remove_stream') as stop:
            self.assertEqual(self.client.patch('/api/cameras/' + self.camera, json={'name':'Denied'}).status_code, 403)
            self.assertEqual(self.client.post('/api/cameras/' + self.camera + '/archive').status_code, 403)
            stop.assert_not_called()

    def test_camera_freshness_budget_and_recovery(self):
        from app.stream_manager import StreamManager, CameraWorker
        manager = StreamManager()
        worker = CameraWorker(self.camera, 'rtsp://example.test/feed')
        manager._workers[self.camera] = worker
        worker._running = True
        worker.health_status = 'OPERATIONAL'
        with database.SessionLocal() as db:
            camera = db.get(database.CameraDB, self.camera)
            previous = camera.source_freshness_seconds
            camera.source_freshness_seconds = 2
            db.commit()
        try:
            worker.last_frame_at = datetime.utcnow() - timedelta(seconds=3)
            self.assertEqual(manager.get_health(self.camera)['status'], 'STALE')
            worker.last_frame_at = datetime.utcnow()
            self.assertEqual(manager.get_health(self.camera)['status'], 'OPERATIONAL')
        finally:
            with database.SessionLocal() as db:
                db.get(database.CameraDB, self.camera).source_freshness_seconds = previous
                db.commit()

    def test_csv_formula_escaped(self):
        path = report_generator.generate_csv_report([{'camera_id':'=1+1'}], 'safe.csv')
        with open(path, encoding='utf-8') as source:
            row = next(csv.DictReader(source))
        self.assertEqual(row['Camera ID'], "'=1+1")

    def test_aci_learning_approval_and_rollback(self):
        from app.aci_learning import build_candidate, observe
        from app.aci_engine import get_baseline
        from app import auth
        camera_id = self.camera
        now = datetime.utcnow().replace(second=0, microsecond=0)
        endpoint = f'/api/aci/{camera_id}/profiles'
        self.assertEqual(self.client.post(endpoint).status_code, 409)
        try:
            with database.SessionLocal() as db:
                observe(db, camera_id, [], 0, now, 'RECORDED')
                self.assertEqual(db.query(database.ACIObservationDB).filter_by(camera_id=camera_id).count(), 0)
                start = now - timedelta(hours=73)
                for i in range(73 * 60 + 1):
                    stamp = start + timedelta(minutes=i)
                    db.add(database.ACIObservationDB(id=f'{camera_id}:{stamp.isoformat()}',
                        camera_id=camera_id, timestamp=stamp, vehicle_count=1,
                        person_count=0, dwell_seconds=20, origin='LIVE'))
                db.commit()
                first = build_candidate(db, camera_id, now)
                self.assertEqual(first['status'], 'CANDIDATE')
                self.assertEqual(get_baseline(db, camera_id)['origin'], 'SEEDED_RULES')
            with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, role='operator')):
                self.assertEqual(self.client.post(endpoint + '/' + first['id'] + '/approve').status_code, 403)
            with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, department='Unrelated')):
                self.assertEqual(self.client.get(endpoint).status_code, 404)
                self.assertEqual(self.client.post(endpoint + '/' + first['id'] + '/approve').status_code, 404)
            self.assertEqual(self.client.post(endpoint + '/' + first['id'] + '/approve').status_code, 200)
            self.assertEqual(self.client.get(f'/api/aci/{camera_id}').json()['avg_dwell_seconds'], 20)
            with database.SessionLocal() as db:
                second = build_candidate(db, camera_id, now)
            for profile_id in (second['id'], first['id'], first['id']):
                self.assertEqual(self.client.post(endpoint + '/' + profile_id + '/approve').status_code, 200)
            profiles = self.client.get(endpoint).json()
            self.assertEqual([p['id'] for p in profiles if p['status'] == 'ACTIVE'], [first['id']])
        finally:
            with database.SessionLocal() as db:
                db.query(database.ACIObservationDB).filter_by(camera_id=camera_id).delete()
                db.query(database.ACIProfileDB).filter_by(camera_id=camera_id).delete()
                db.commit()

    def test_aci_sparse_data_and_minute_sampling(self):
        from app.aci_learning import observe, build_candidate
        from fastapi import HTTPException
        now = datetime.utcnow().replace(second=0, microsecond=0)
        try:
            with database.SessionLocal() as db:
                for stamp in (now - timedelta(days=8), now - timedelta(hours=73), now):
                    observe(db, self.camera, [{'is_vehicle':True, 'class':'car'}], 10, stamp, 'LIVE')
                observe(db, self.camera, [], 0, now + timedelta(seconds=20), 'LIVE')
                rows = db.query(database.ACIObservationDB).filter_by(camera_id=self.camera).all()
                self.assertEqual(len(rows), 2)
                with self.assertRaises(HTTPException) as result:
                    build_candidate(db, self.camera, now)
                self.assertEqual(result.exception.status_code, 409)
                self.assertEqual(result.exception.detail['covered_hours'], 0)
        finally:
            with database.SessionLocal() as db:
                db.query(database.ACIObservationDB).filter_by(camera_id=self.camera).delete()
                db.commit()

    def test_clip_window_playback_and_tampering(self):
        import cv2
        import numpy as np
        from app.clip_evidence import ClipBuffer
        alert_id = self.demo()['alert']['id']
        buffer = ClipBuffer()
        start = datetime.utcnow()
        frame = np.full((48, 64, 3), 100, np.uint8)
        for i in range(26):
            buffer.add(frame, start + timedelta(seconds=i / 5))
        buffer.request(alert_id, self.camera, start + timedelta(seconds=5))
        self.assertEqual(self.client.get(f'/api/evidence/{alert_id}/clip').json()['status'], 'PENDING')
        for i in range(26, 76):
            buffer.add(frame, start + timedelta(seconds=i / 5))
        buffer.close()
        status = self.client.get(f'/api/evidence/{alert_id}/clip').json()
        self.assertEqual(status['status'], 'COMPLETE', status)
        self.assertEqual(status['frame_count'], 76)
        self.assertEqual(self.client.get(status['download_url']).status_code, 200)
        from app import auth
        with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, department='Other Department')):
            for suffix in ('', '/download', '/verify', '/preview'):
                self.assertEqual(self.client.get(f'/api/evidence/{alert_id}/clip' + suffix).status_code, 404)
        endpoint = f'/api/evidence/{alert_id}/clip/verify'
        self.assertTrue(self.client.get(endpoint).json()['verified'])
        with database.SessionLocal() as db:
            row = db.get(database.ClipEvidenceDB, alert_id)
            path = Path(row.path)
            self.assertEqual(db.query(database.BlockchainLedgerDB).filter_by(alert_id=alert_id).count(), 2)
        cap = cv2.VideoCapture(str(path))
        try:
            self.assertTrue(cap.read()[0])
        finally:
            cap.release()
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'tamper')
            self.assertFalse(self.client.get(endpoint).json()['verified'])
            path.unlink()
            self.assertEqual(self.client.get(endpoint).json()['evidence_status'], 'MISSING')
        finally:
            path.write_bytes(original)

    def test_clip_partial_capacity_and_restart(self):
        import numpy as np
        from app.clip_evidence import ClipBuffer, recover_pending_clips
        alert_id = self.demo()['alert']['id']
        buffer = ClipBuffer()
        now = datetime.utcnow()
        buffer.add(np.zeros((48, 64, 3), np.uint8), now)
        buffer.request(alert_id, self.camera, now)
        buffer.close()
        status = self.client.get(f'/api/evidence/{alert_id}/clip').json()
        self.assertEqual(status['status'], 'PARTIAL')
        self.assertEqual(status['post_seconds'], 0)
        preview = self.client.get(f'/api/evidence/{alert_id}/clip/preview')
        self.assertEqual(preview.status_code, 200)
        self.assertIn('multipart/x-mixed-replace', preview.headers['content-type'])
        self.assertIn(b'Content-Type: image/jpeg', preview.content)
        self.assertIn(b'\xff\xd8', preview.content)
        second = self.demo(plate_number='GJ01CD5678')['alert']['id']
        limited = ClipBuffer()
        limited.max_pending = 0
        limited.request(second, self.camera, now)
        self.assertEqual(self.client.get(f'/api/evidence/{second}/clip').json()['status'], 'UNAVAILABLE')
        with database.SessionLocal() as db:
            db.get(database.ClipEvidenceDB, second).status = 'PENDING'
            db.commit()
        recover_pending_clips()
        self.assertEqual(self.client.get(f'/api/evidence/{second}/clip').json()['status'], 'UNAVAILABLE')

    def test_clip_encoding_failure_is_explicit(self):
        import numpy as np
        from app.clip_evidence import ClipBuffer
        alert_id = self.demo()['alert']['id']
        buffer = ClipBuffer()
        now = datetime.utcnow()
        buffer.add(np.zeros((48, 64, 3), np.uint8), now)
        buffer.request(alert_id, self.camera, now)
        with patch('app.clip_evidence.cv2.VideoWriter', side_effect=RuntimeError('Encoder failed')):
            buffer.close()
        self.assertEqual(self.client.get(f'/api/evidence/{alert_id}/clip').json()['status'], 'FAILED')
        self.assertEqual(self.client.get(f'/api/evidence/{alert_id}/clip/download').status_code, 404)
        with database.SessionLocal() as db:
            self.assertEqual(db.query(database.BlockchainLedgerDB).filter_by(alert_id=alert_id, evidence_type='CLIP').count(), 0)

    def test_clip_encoding_does_not_block_capture_and_counts_capacity(self):
        import threading
        import numpy as np
        from app.clip_evidence import ClipBuffer
        alert_id = self.demo()['alert']['id']
        other_id = self.demo(plate_number='GJ01CD5678')['alert']['id']
        buffer = ClipBuffer()
        buffer.max_pending = 1
        started, release = threading.Event(), threading.Event()
        original = buffer.finish
        def slow_finish(*args):
            started.set()
            release.wait(5)
            original(*args)
        buffer.finish = slow_finish
        now = datetime.utcnow()
        frame = np.zeros((48,64,3),np.uint8)
        try:
            buffer.add(frame,now)
            buffer.request(alert_id,self.camera,now)
            buffer.add(frame,now + timedelta(seconds=10))
            self.assertTrue(started.wait(1))
            self.assertFalse(release.is_set())
            buffer.request(other_id,self.camera,now)
            self.assertEqual(self.client.get(f'/api/evidence/{other_id}/clip').json()['status'],'UNAVAILABLE')
        finally:
            release.set()
            buffer.close()
        self.assertEqual(self.client.get(f'/api/evidence/{alert_id}/clip').json()['status'],'PARTIAL')

    def test_read_routes(self):
        for path in ["/", "/docs", "/openapi.json", "/static/js/api.js", "/static/js/operations.js", "/api/health",
                     "/api/cameras", "/api/cameras/geojson", "/api/cameras/stats",
                     "/api/watchlist", "/api/alerts", "/api/blockchain/ledger",
                     "/api/streams/active", f"/api/aci/{self.camera}"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        health = self.client.get('/api/health').json()
        self.assertEqual(health['readiness']['api'], 'READY')
        self.assertIn(health['readiness']['analysis'], {'READY', 'NOT_INITIALIZED', 'FAILED'})
        root = self.client.get('/')
        self.assertIn('/static/js/journey.js?v=',root.text)
        self.assertEqual(root.headers['referrer-policy'],'strict-origin-when-cross-origin')
        with patch.dict('os.environ',{'GUIVIN_TILE_URL':''}):
            self.assertEqual(self.client.get('/api/map-config').json()['tile_url'],'')
        with patch.dict('os.environ',{'GUIVIN_TILE_URL':'https://user:secret@example.org/{z}/{x}/{y}'}):
            self.assertEqual(self.client.get('/api/map-config').json()['tile_url'],'')

    def test_seed_counts(self):
        health = self.client.get("/api/health").json()
        self.assertEqual(health["cameras_registered"], 12)
        self.assertEqual(health["watchlist_entries"], 8)

    def test_watchlist_normalization(self):
        result = self.client.get("/api/watchlist/check/gj01ab1234").json()
        self.assertEqual(result["reason"], "STOLEN")
        self.assertFalse(self.client.get("/api/watchlist/check/UNKNOWN123").json()["matched"])

    def test_camera_onboarding(self):
        body = {"id": "AUDIT-CAMERA", "name": "Audit camera", "department": "Test",
                "district": "Test", "lat": 23.0, "lon": 72.5,
                "onvif_profile": "T", "codec": "H264", "metadata_streaming": True,
                "motion_tamper_events": True, "https_streaming": True, "ptz_capable": True,
                "conformance_status": "DECLARED", "source_freshness_seconds": 3}
        self.assertEqual(self.client.post("/api/cameras", json=body).status_code, 200)
        camera = self.client.get("/api/cameras/AUDIT-CAMERA").json()
        self.assertEqual(camera["name"], "Audit camera")
        self.assertEqual(camera["onvif_profile"], "T")
        self.assertTrue(camera["metadata_streaming"])
        self.assertEqual(camera["conformance_status"], "DECLARED")
        patch = self.client.patch("/api/cameras/AUDIT-CAMERA", json={
            "maintenance_status": "SCHEDULED", "source_freshness_seconds": 5})
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.json()["maintenance_status"], "SCHEDULED")
        self.assertEqual(self.client.post("/api/cameras/AUDIT-CAMERA/archive").status_code, 200)
        self.assertEqual(self.client.get("/api/cameras/AUDIT-CAMERA").status_code, 404)
        with database.SessionLocal() as db:
            import json
            audit_rows = db.query(database.AuditDB).filter(
                database.AuditDB.resource == '/api/cameras/AUDIT-CAMERA'
            ).all()
            detail_rows = [json.loads(row.details) for row in audit_rows if row.details]
            self.assertTrue(any('changed_fields' in details for details in detail_rows))
            self.assertTrue(any(details.get('before', {}).get('archived') is False
                                and details.get('after', {}).get('archived') is True
                                for details in detail_rows))
            self.assertFalse(any('stream_url' in row.details for row in audit_rows))
            db.query(database.CameraDB).filter_by(id="AUDIT-CAMERA").delete()
            db.commit()

    def test_local_video_capture_and_snapshot(self):
        import cv2
        import numpy as np
        from app.main import stream_manager
        path = Path(TEMP.name) / "blackout.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
        self.assertTrue(writer.isOpened())
        for _ in range(30):
            writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
        writer.release()
        try:
            result = self.client.post("/api/stream/start", json={"camera_id": self.camera, "stream_url": str(path)})
            self.assertEqual(result.status_code, 200)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                snapshot = self.client.get(f"/api/stream/{self.camera}/snapshot")
                if snapshot.status_code == 200:
                    break
                time.sleep(.05)
            self.assertEqual(snapshot.status_code, 200)
            self.assertTrue(snapshot.content.startswith(b"\xff\xd8"))
        finally:
            worker = stream_manager._workers.get(self.camera)
            stream_manager.remove_stream(self.camera)
            if worker and worker._thread:
                worker._thread.join(timeout=3)

    def test_recorded_detection_persists_alert_and_evidence(self):
        import cv2
        import numpy as np
        from app.stream_manager import stream_manager
        path = Path(TEMP.name) / "deterministic-anpr.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (160, 120))
        self.assertTrue(writer.isOpened())
        for _ in range(45):
            writer.write(np.full((120, 160, 3), 180, dtype=np.uint8))
        writer.release()
        detection = {"class": "car", "is_vehicle": True, "confidence": .95,
                     "bbox": (0, 0, 160, 120)}
        try:
            with patch("app.ai_pipeline.detect_frame", return_value=(np.full((120, 160, 3), 180, dtype=np.uint8), [detection])), \
                 patch("app.ai_pipeline.extract_plate_observation", return_value={
                     "plate": "GJ01AB1234", "confidence": .95,
                     "localization": "VEHICLE_BOTTOM_FALLBACK", "rectified": True}), \
                 patch("app.ai_pipeline.detect_camera_health", return_value=("OPERATIONAL", "OK")):
                existing_alert_ids = {a["id"] for a in self.client.get("/api/alerts?limit=100").json()}
                result = self.client.post("/api/stream/start", json={
                    "camera_id": self.camera, "stream_url": str(path)})
                self.assertEqual(result.status_code, 200)
                deadline = time.monotonic() + 5
                found = None
                while time.monotonic() < deadline:
                    alerts = self.client.get("/api/alerts?limit=100").json()
                    found = next((a for a in alerts if a.get("origin") == "RECORDED"
                                  and a.get("plate_number") == "GJ-01-AB-1234"
                                  and a.get("id") not in existing_alert_ids), None)
                    if found:
                        break
                    time.sleep(.05)
                if found is None:
                    with database.SessionLocal() as db:
                        count = db.query(database.DetectionDB).filter_by(camera_id=self.camera).count()
                    health = self.client.get(f"/api/stream/{self.camera}/health").json()
                    self.fail(f"Recorded detection must create a watchlist alert; detections={count}, health={health}")
                self.assertEqual(self.client.get(found["evidence_url"]).status_code, 200)
                with database.SessionLocal() as db:
                    stored = db.query(database.DetectionDB).filter_by(alert_id=found["id"]).one()
                    self.assertEqual(stored.origin, "RECORDED")
                    self.assertTrue(stored.track_id)
                    self.assertEqual(stored.raw_plate, "GJ01AB1234")
                    self.assertEqual(stored.plate_localization, "VEHICLE_BOTTOM_FALLBACK", stored.__dict__)
                    self.assertTrue(stored.plate_rectified)
        finally:
            stream_manager.remove_stream(self.camera)

    def test_demo_and_websocket(self):
        with self.client.websocket_connect("/ws") as ws:
            ws.send_text("ping")
            self.assertEqual(ws.receive_text(), "pong")
            result = self.demo()
            message = ws.receive_json()
            self.assertEqual(message["event"], "new_alert")
            self.assertEqual(message["alert"]["id"], result["alert"]["id"])
            self.assertEqual(result["alert"]["severity"], "HIGH")

    def test_exact_duplicate_suppression(self):
        self.demo()
        self.assertTrue(self.demo()["suppressed"])

    def test_equivalent_plate_duplicate_suppression(self):
        self.demo()
        self.assertTrue(self.demo(plate_number="gj01ab1234").get("suppressed", False))

    def test_acknowledgement(self):
        aid = self.demo()["alert"]["id"]
        r = self.client.post(f"/api/alerts/{aid}/acknowledge", json={"operator": "Local audit", "action": "ACKNOWLEDGED"})
        self.assertTrue(r.json()["success"])
        self.assertEqual(self.client.get(f"/api/alerts/{aid}").json()["status"], "ACKNOWLEDGED")

    def test_invalid_status_rejected(self):
        aid = self.demo()["alert"]["id"]
        r = self.client.post(f"/api/alerts/{aid}/acknowledge", json={"operator": "Local audit", "action": "INVALID"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_confidence_rejected(self):
        r = self.client.post("/api/alerts/demo/anpr", json={"camera_id": self.camera, "plate_number": "TEST123", "confidence": 5.0})
        self.assertEqual(r.status_code, 422)

    def test_journey_seed_and_search(self):
        self.assertEqual(self.client.post("/api/journey/seed").status_code, 200)
        result = self.client.get("/api/journey/GJ01AB1234").json()
        self.assertGreaterEqual(len({d["camera_id"] for d in result["journey"]}), 4)
        times = [d["timestamp"] for d in result["journey"]]
        self.assertEqual(times, sorted(times))

    def test_topology_journey_windows_and_access(self):
        from app import auth
        cameras = self.client.get('/api/cameras').json()
        source = cameras[0]
        target = next(c for c in cameras if c['department'] != source['department'])
        body = dict(source_id=source['id'], target_id=target['id'], min_seconds=60, max_seconds=300)
        plate = 'GJ-99-ZZ-9999'
        start = datetime(2026, 1, 1)
        try:
            self.assertEqual(self.client.put('/api/topology/links', json=dict(body, max_seconds=10)).status_code, 422)
            self.assertEqual(self.client.put('/api/topology/links', json=dict(body, target_id=source['id'])).status_code, 422)
            with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, role='operator')):
                self.assertEqual(self.client.put('/api/topology/links', json=body).status_code, 403)
            self.assertEqual(self.client.put('/api/topology/links', json=body).status_code, 200)
            with database.SessionLocal() as db:
                for camera, seconds, confidence, origin in [
                    (source['id'],0,.9,'LIVE'), (target['id'],30,.9,'LIVE'),
                    (source['id'],60,.9,'LIVE'), (target['id'],180,.9,'LIVE'),
                    (source['id'],200,.9,'LIVE'), (target['id'],600,.9,'LIVE'),
                    (source['id'],620,.9,'LIVE'), (target['id'],720,.4,'LIVE'),
                    (source['id'],740,.9,'RECORDED')]:
                    db.add(database.DetectionDB(camera_id=camera, plate_number=plate,
                        timestamp=start + timedelta(seconds=seconds), plate_confidence=confidence, origin=origin))
                db.commit()
            result = self.client.get('/api/journey/' + plate).json()
            statuses = [row['correlation']['status'] for row in result['journey']]
            self.assertEqual(statuses[1], 'TOO_FAST')
            self.assertEqual(statuses[2], 'UNKNOWN_TOPOLOGY')
            self.assertEqual(statuses[3], 'PLAUSIBLE')
            self.assertEqual(statuses[5], 'OUTSIDE_WINDOW')
            self.assertEqual(statuses[7], 'UNCERTAIN_OCR')
            self.assertEqual(statuses[8], 'UNASSESSED_NON_LIVE')
            self.assertFalse(any(r['correlation']['identity_confirmed'] for r in result['journey']))
            page = self.client.get('/api/journey/' + plate, params={'limit':2}).json()
            self.assertTrue(page['has_more'])
            self.assertEqual(page['next_offset'], 2)
            next_page = self.client.get('/api/journey/' + plate, params={'limit':2, 'offset':2}).json()
            self.assertNotEqual(page['journey'][-1]['detection_id'], next_page['journey'][0]['detection_id'])
            filtered = self.client.get('/api/journey/' + plate, params={'start':'2026-01-01T00:01:00Z','end':'2026-01-01T00:03:00Z'}).json()
            self.assertEqual(filtered['total_sightings'], 2)
            self.assertEqual(self.client.get('/api/journey/' + plate, params={'start':'2026-01-01T00:00:00'}).status_code, 422)
            self.assertEqual(self.client.get('/api/journey/' + plate, params={
                'start':'2026-01-02T00:00:00Z', 'end':'2026-01-01T00:00:00Z'}).status_code, 422)
            with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, department=source['department'])):
                self.assertEqual(self.client.get('/api/topology/links').json(), [])
                self.assertEqual(self.client.put('/api/topology/links', json=body).status_code, 404)
                scoped = self.client.get('/api/journey/' + plate).json()
                self.assertTrue(all(r['camera_id'] == source['id'] for r in scoped['journey']))
                audit = self.client.get('/api/registry/audit?limit=1000').json()
                self.assertFalse(any(r['action'] == 'TOPOLOGY_UPDATE' for r in audit))
            self.assertEqual(self.client.put('/api/topology/links', json=dict(body, enabled=False)).status_code, 200)
            disabled = self.client.get('/api/journey/' + plate).json()
            self.assertEqual(disabled['journey'][3]['correlation']['status'], 'UNKNOWN_TOPOLOGY')
        finally:
            with database.SessionLocal() as db:
                db.query(database.CameraLinkDB).filter_by(source_id=source['id'], target_id=target['id']).delete()
                db.query(database.DetectionDB).filter_by(plate_number=plate).delete()
                db.commit()

    def test_report_downloads(self):
        self.demo()
        csv_result = self.client.get("/api/reports/csv")
        self.assertEqual(csv_result.status_code, 200)
        self.assertIn("Detected Plate", csv_result.text)
        pdf_result = self.client.get("/api/reports/pdf")
        self.assertEqual(pdf_result.status_code, 200)
        self.assertTrue(pdf_result.content.startswith(b"%PDF-"))

    def test_case_workflow_and_legal_grant_revocation(self):
        from app import auth
        alert = self.demo()['alert']
        second = self.demo(plate_number='GJ01CD5678')['alert']
        row = self.client.post('/api/cases', json={'title':'Review case', 'alert_id':alert['id']}).json()
        case_id = row['id']
        try:
            self.assertEqual(self.client.patch('/api/cases/' + case_id, json={
                'status':'ESCALATED', 'assigned_unit':'Unit A', 'note':'Review requested'}).status_code, 200)
            self.assertEqual(self.client.post(f'/api/cases/{case_id}/alerts', json={'alert_id':second['id']}).status_code, 200)
            with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, role='field_operator')):
                self.assertEqual(self.client.post('/api/cases', json={'title':'Denied', 'alert_id':alert['id']}).status_code, 403)
            judiciary = dict(auth.LOCAL, username='legal-reviewer', role='judiciary')
            with patch.object(auth, 'identity', return_value=judiciary):
                self.assertEqual(self.client.get('/api/cases').json(), [])
                self.assertEqual(self.client.get('/api/cases/' + case_id).status_code, 404)
                self.assertFalse(self.client.post('/api/blockchain/verify', json={'alert_id':alert['id']}).json()['verified'])
                self.assertEqual(self.client.get('/api/cameras').status_code, 403)
            with patch('app.cases.users', return_value={'legal-reviewer':{'role':'judiciary'}}):
                self.assertEqual(self.client.post(f'/api/cases/{case_id}/access', json={'username':'legal-reviewer'}).status_code, 200)
                with patch.object(auth, 'identity', return_value=judiciary):
                    detail = self.client.get('/api/cases/' + case_id).json()
                    self.assertEqual(len(detail['alerts']), 2)
                    self.assertEqual(self.client.patch('/api/cases/' + case_id, json={'status':'CLOSED'}).status_code, 403)
                self.assertEqual(self.client.post(f'/api/cases/{case_id}/access', json={'username':'legal-reviewer', 'enabled':False}).status_code, 200)
            with patch.object(auth, 'identity', return_value=judiciary):
                self.assertEqual(self.client.get('/api/cases/' + case_id).status_code, 404)
                self.assertEqual(self.client.get(f"/api/evidence/{alert['id']}/clip").status_code, 404)
            self.assertEqual(self.client.patch('/api/cases/' + case_id, json={'status':'CLOSED'}).status_code, 200)
            self.assertEqual(self.client.post(f'/api/cases/{case_id}/alerts', json={'alert_id':second['id']}).status_code, 409)
        finally:
            with database.SessionLocal() as db:
                db.query(database.CaseAlertDB).filter_by(case_id=case_id).delete()
                db.query(database.CaseAccessDB).filter_by(case_id=case_id).delete()
                db.query(database.CaseDB).filter_by(id=case_id).delete()
                db.commit()

    def test_supervisor_review_is_independent_and_scoped(self):
        from app import auth
        alert = self.demo()['alert']
        self.assertEqual(self.client.post(f"/api/alerts/{alert['id']}/acknowledge", json={'operator':'ignored','action':'FALSE_ALARM'}).status_code, 200)
        row = next(r for r in self.client.get('/api/reviews').json() if r['alert_id'] == alert['id'])
        endpoint = '/api/reviews/' + row['id']
        body = {'decision':'REOPEN','note':'Independent review requested'}
        self.assertEqual(self.client.post(endpoint,json=body).status_code,403)
        with patch.object(auth,'identity',return_value=dict(auth.LOCAL,username='other',role='sector_supervisor',camera_ids=[])):
            self.assertEqual(self.client.post(endpoint,json=body).status_code,404)
        with patch.object(auth,'identity',return_value=dict(auth.LOCAL,username='other',role='sector_supervisor',camera_ids=[self.camera])):
            result = self.client.post(endpoint,json=body)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['alert_status'],'NEW')
            self.assertEqual(self.client.post(endpoint,json=body).status_code,409)
        with patch.object(auth,'identity',return_value=dict(auth.LOCAL,role='field_operator')):
            self.assertEqual(self.client.get('/api/reviews').status_code,403)

    def test_case_supervisor_assignment_and_revocation(self):
        from app import auth
        alert = self.demo()['alert']
        case_id = self.client.post('/api/cases',json={'title':'Shared sector case','alert_id':alert['id']}).json()['id']
        endpoint = f'/api/cases/{case_id}/assignments'
        who = dict(auth.LOCAL,username='assigned-supervisor',role='sector_supervisor',camera_ids=[self.camera])
        with patch.object(auth,'identity',return_value=who):
            self.assertEqual(self.client.get('/api/cases/'+case_id).status_code,404)
        with patch('app.cases.users',return_value={who['username']:who}):
            self.assertEqual(self.client.post(endpoint,json={'username':who['username']}).status_code,200)
            with patch.object(auth,'identity',return_value=who):
                self.assertEqual(self.client.get('/api/cases/'+case_id).status_code,200)
                self.assertEqual(self.client.patch('/api/cases/'+case_id,json={'status':'ESCALATED'}).status_code,200)
                self.assertEqual(self.client.post(endpoint,json={'username':who['username']}).status_code,403)
            with patch.object(auth,'identity',return_value=dict(who,camera_ids=[])):
                self.assertEqual(self.client.get('/api/cases/'+case_id).status_code,404)
            self.assertEqual(self.client.post(endpoint,json={'username':who['username'],'enabled':False}).status_code,200)
        with patch.object(auth,'identity',return_value=who):
            self.assertEqual(self.client.get('/api/cases/'+case_id).status_code,404)
        with patch('app.cases.users',return_value={who['username']:dict(who,camera_ids=[])}):
            self.assertEqual(self.client.post(endpoint,json={'username':who['username']}).status_code,422)

    def test_scene_rules_scope_versions_and_persisted_evidence(self):
        from app import auth
        from app.scene_rules import SceneEvaluator
        import numpy as np
        endpoint = f'/api/cameras/{self.camera}/rules'
        rule = {'id':'crowd-test','kind':'CROWD','points':[[0,0],[1,0],[1,1],[0,1]],'count':2}
        body = {'expected_version':0,'rules':[rule]}
        self.assertEqual(self.client.get(endpoint).json()['version'],0)
        try:
            with patch.object(auth,'identity',return_value=dict(auth.LOCAL,role='field_operator')):
                self.assertEqual(self.client.put(endpoint,json=body).status_code,403)
            with patch.object(auth,'identity',return_value=dict(auth.LOCAL,department='Other department')):
                self.assertEqual(self.client.get(endpoint).status_code,404)
            result = self.client.put(endpoint,json=body)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(self.client.put(endpoint,json=body).status_code,409)
            evaluator = SceneEvaluator()
            frame = np.full((100,100,3),100,np.uint8)
            detections=[{'bbox':(10,10,20,20),'class':'person','confidence':.9},
                        {'bbox':(60,10,20,20),'class':'person','confidence':.8}]
            events = evaluator.process(self.camera,detections,frame,0,datetime.utcnow(),'RECORDED')
            self.assertEqual(len(events),1)
            alert=events[0]['alert']
            self.assertEqual(alert['origin'],'RECORDED')
            self.assertEqual(alert['alert_type'],'SCENE_CROWD')
            self.assertEqual(self.client.get(alert['evidence_url']).status_code,200)
            self.assertEqual(evaluator.process(self.camera,detections,frame,1,datetime.utcnow(),'RECORDED'),[])
        finally:
            with database.SessionLocal() as db:
                db.query(database.SceneRulesDB).filter_by(camera_id=self.camera).delete()
                db.commit()

    def test_login_bounds_and_expiration(self):
        from app import auth
        with patch.object(auth,'USER_FILE','test-only'):
            for data in ([], {'username':[], 'password':'x'}):
                self.assertEqual(self.client.post('/api/auth/login',json=data).status_code,422)
            self.assertEqual(self.client.post('/api/auth/login',content=b'x'*4097).status_code,413)
            self.assertEqual(self.client.post('/api/auth/login',content=b'{bad').status_code,422)
        with patch.object(auth,'USER_FILE','test-only'), patch.object(auth,'users',return_value={'expired':{'role':'viewer','department':'*'}}):
            auth.SESSIONS['expired-test']={'username':'expired','expires':time.time()-1}
            self.client.cookies.set('guivin_session','expired-test')
            try:
                self.assertEqual(self.client.get('/api/alerts').status_code,401)
            finally:
                self.client.cookies.clear()
                auth.SESSIONS.pop('expired-test',None)
        self.assertEqual(auth.SESSION_SECONDS,900)

    def test_document_roles_and_sector_scope(self):
        from app import auth
        cameras = self.client.get('/api/cameras').json()
        dept = cameras[0]['department']
        who = dict(auth.LOCAL, username='supervisor', role='sector_supervisor', camera_ids=[self.camera])
        with patch.object(auth, 'identity', return_value=who):
            self.assertEqual([c['id'] for c in self.client.get('/api/cameras').json()], [self.camera])
            self.assertEqual(self.client.get('/api/cameras/' + cameras[1]['id']).status_code, 404)
            self.assertEqual(self.client.get('/api/stream/' + cameras[1]['id'] + '/snapshot').status_code, 404)
            with patch('app.main.stream_manager.active_cameras', return_value=[self.camera, cameras[1]['id']]):
                self.assertEqual(self.client.get('/api/streams/active').json()['active'], [self.camera])
            self.assertEqual(self.client.patch('/api/cameras/' + self.camera, json={'name':'Denied'}).status_code, 403)
        with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, role='technical_admin')):
            self.assertEqual(self.client.get('/api/health').status_code, 200)
            self.assertEqual(self.client.get('/api/alerts').status_code, 403)
        with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, role='auditor')):
            self.assertEqual(self.client.get('/api/alerts').status_code, 200)
            self.assertEqual(self.client.get('/api/cameras').status_code, 403)
            self.assertEqual(self.client.get(f'/api/stream/{self.camera}/snapshot').status_code, 403)
        with patch.object(auth, 'identity', return_value=dict(auth.LOCAL, role='department_head', department=dept)):
            self.assertEqual(self.client.patch('/api/cameras/' + self.camera, json={}).status_code, 200)
            foreign = next(c for c in cameras if c['department'] != dept)
            self.assertEqual(self.client.patch('/api/cameras/' + foreign['id'], json={}).status_code, 404)

    def test_csv_converts_utc_to_ist(self):
        path = report_generator.generate_csv_report([{"timestamp": "2026-09-17T00:00:00"}], "timezone.csv")
        with open(path, encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        self.assertEqual(row["Timestamp (IST)"], "2026-09-17 05:30:00 IST")

    def test_missing_evidence_not_verified(self):
        alert = self.demo()["alert"]
        self.assertFalse(Path(alert["clip_path"]).is_file())
        result = self.client.post("/api/blockchain/verify", json={"alert_id": alert["id"]}).json()
        self.assertFalse(result["verified"], "Nonexistent clip must not verify successfully")

    def test_modified_ledger_not_verified(self):
        aid = self.demo()["alert"]["id"]
        with database.SessionLocal() as db:
            block = db.query(database.BlockchainLedgerDB).filter_by(alert_id=aid).one()
            block.clip_hash = "f" * 64
            db.commit()
        result = self.client.post("/api/blockchain/verify", json={"alert_id": aid}).json()
        self.assertFalse(result["verified"], "Tampered ledger must not verify successfully")

    def test_dedup_expires_after_one_day(self):
        alert_manager._recent_alerts["TEST:PLATE"] = datetime.utcnow() - timedelta(days=1, seconds=1)
        self.assertFalse(alert_manager._is_duplicate("TEST", "PLATE"))

    def test_unknown_resources(self):
        self.assertEqual(self.client.get("/api/cameras/NO-SUCH-CAMERA").status_code, 404)
        self.assertEqual(self.client.get("/api/stream/NO-SUCH-CAMERA/snapshot").status_code, 404)
        self.assertEqual(self.client.get("/api/proxy/hls/NO-SUCH-CAMERA/index.m3u8").status_code, 403)

    def test_stream_worker_calls_plate_recognition(self):
        import numpy as np
        from app.stream_manager import CameraWorker
        worker = CameraWorker("AUDIT", "unused")
        worker._running = True
        frame = np.zeros((100, 100, 3), dtype=np.uint8)

        class Capture:
            n = 0
            def get(self, prop):
                return self.n * 200.0
            def read(self):
                self.n += 1
                if self.n > 5:
                    worker._running = False
                    return False, None
                return True, frame.copy()

        worker.cap = Capture()
        detection = {"class": "car", "is_vehicle": True, "confidence": .9, "bbox": (0, 0, 100, 100)}
        with patch.object(worker, "_connect", return_value=True), \
             patch("app.ai_pipeline.detect_camera_health", return_value=("OPERATIONAL", "OK")), \
             patch("app.ai_pipeline.detect_frame", return_value=(frame, [detection])), \
             patch("app.ai_pipeline.extract_plate_observation", return_value={
                 "plate": "GJ01AB1234", "confidence": .9,
                 "localization": "VEHICLE_BOTTOM_FALLBACK", "rectified": True}) as ocr:
            worker._capture_loop()
        self.assertGreater(ocr.call_count, 0, "Live detected vehicles must enter OCR")


if __name__ == "__main__":
    unittest.main(verbosity=2)
