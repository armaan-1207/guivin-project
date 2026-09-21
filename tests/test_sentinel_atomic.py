import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import main
from app.database import Base, CameraDB, AuditDB
from app.camera_registry import add_camera
from app.sentinel_connector import parse_catalogue


class SentinelAtomicTests(unittest.TestCase):
    def test_worker_failures_are_reported_without_losing_successes_or_secrets(self):
        from app.stream_manager import StreamStillStopping
        for outcomes in ([None, RuntimeError('secret-stream-url')],
                         [StreamStillStopping('private'), RuntimeError('secret-stream-url')]):
            with self.subTest(outcomes=bool(outcomes[0])):
                engine = create_engine('sqlite:///:memory:')
                Base.metadata.create_all(engine)
                catalogue = parse_catalogue([{'id':'fixture1'}, {'id':'fixture2'}])
                req = main.SentinelRequest(email='fixture@example.invalid', password='fixture', max_cameras=2)
                with Session(engine) as db, \
                     patch('app.sentinel_connector.SentinelConnector.login', return_value=True), \
                     patch('app.sentinel_connector.SentinelConnector.fetch_cameras', return_value=catalogue), \
                     patch.object(main.ws_manager, 'broadcast', new_callable=AsyncMock) as broadcast, \
                     patch.object(main.stream_manager, 'add_stream', side_effect=outcomes):
                    result = asyncio.run(main.connect_sentinel_sandbox(req, db))
                    expected = 0 if outcomes[0] else 1
                    self.assertEqual(len(result['streams_started']), expected)
                    self.assertEqual(len(result['streams_failed']), 2 - expected)
                    self.assertEqual(result['connected'], bool(expected))
                    self.assertEqual(result['partial'], bool(expected))
                    self.assertEqual(db.query(CameraDB).count(), 2)
                    self.assertNotIn('secret-stream-url', str(result))
                    self.assertNotIn('private', str(result))
                    if not expected:
                        self.assertEqual(result['streams_failed'][0]['reason'], 'PREVIOUS_WORKER_STOPPING')
                    self.assertEqual(broadcast.call_args.args[0]['cameras_failed'], 2 - expected)
                engine.dispose()

    def test_failed_second_registration_rolls_back_first_and_starts_no_feeds(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        catalogue = parse_catalogue([{'id':'fixture1'}, {'id':'fixture2'}])
        req = main.SentinelRequest(email='fixture@example.invalid', password='fixture', max_cameras=2)
        def insert(db, data):
            if data['id'].endswith('FIXTURE2'):
                raise RuntimeError('Injected registration failure')
            return add_camera(db, data)
        with Session(engine) as db, \
             patch('app.sentinel_connector.SentinelConnector.login', return_value=True), \
             patch('app.sentinel_connector.SentinelConnector.fetch_cameras', return_value=catalogue), \
             patch('app.main.add_camera', side_effect=insert), \
             patch.object(main.stream_manager, 'add_stream') as start:
            with self.assertRaises(RuntimeError):
                asyncio.run(main.connect_sentinel_sandbox(req, db))
            self.assertEqual(db.query(CameraDB).count(), 0)
            self.assertEqual(db.query(AuditDB).count(), 0)
            start.assert_not_called()
        engine.dispose()

    def test_all_registrations_commit_before_first_worker(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        catalogue = parse_catalogue([{'id':'fixture1'}, {'id':'fixture2'}])
        req = main.SentinelRequest(email='fixture@example.invalid', password='fixture', max_cameras=2)
        with Session(engine) as db, \
             patch('app.sentinel_connector.SentinelConnector.login', return_value=True), \
             patch('app.sentinel_connector.SentinelConnector.fetch_cameras', return_value=catalogue), \
             patch.object(main.ws_manager, 'broadcast', new_callable=AsyncMock):
            def start(*args):
                self.assertFalse(db.in_transaction())
            with patch.object(main.stream_manager, 'add_stream', side_effect=start) as worker:
                result = asyncio.run(main.connect_sentinel_sandbox(req, db))
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(result['cameras_registered'], 2)
            self.assertEqual(db.query(CameraDB).count(), 2)
        engine.dispose()
