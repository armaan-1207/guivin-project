import sys
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.source_origin import source_origin
from app.aci_learning import observe, build_candidate, activate
from app.aci_engine import get_baseline
from fastapi import HTTPException


class SourceOriginTests(unittest.TestCase):
    def test_sandbox_network_and_relay_are_recorded(self):
        for camera, url in [
            ('SENTINEL-CAM01', 'http://127.0.0.1/relay/cam01'),
            ('sentinel-cam01', 'rtsp://example.invalid/reconnect'),
            ('manual-camera', 'rtsp://fixture:fixture@103.250.160.189:8554/stream/cam01'),
            ('manual-camera', 'https://CCTV.CORP8.CLOUD/cam01/index.m3u8'),
        ]:
            with self.subTest(camera=camera, url=url):
                self.assertEqual(source_origin(camera, url), 'RECORDED')

    def test_other_network_and_local_file_behavior(self):
        self.assertEqual(source_origin('camera', 'rtsp://camera.invalid/live'), 'LIVE')
        self.assertEqual(source_origin('camera', 'fixture.avi', True), 'RECORDED')

    def test_replay_never_writes_learning_observations(self):
        db = Mock()
        for camera, origin in [('SENTINEL-CAM01', 'LIVE'), ('manual', 'RECORDED')]:
            observe(db, camera, [], 0, datetime.utcnow(), origin)
        self.assertEqual(db.mock_calls, [])

    def test_historical_mislabeled_sandbox_cannot_build_candidate(self):
        db = Mock()
        with self.assertRaises(HTTPException) as error:
            build_candidate(db, 'SENTINEL-CAM01')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(db.mock_calls, [])

    def test_historical_sandbox_profile_cannot_activate_or_score(self):
        db = Mock()
        with self.assertRaises(HTTPException) as error:
            activate(db, 'SENTINEL-CAM01', 'old-profile')
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(get_baseline(db, 'SENTINEL-CAM01'))
        self.assertEqual(db.mock_calls, [])
