import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.database import Base, CameraDB
from app.camera_registry import get_camera, get_cameras_geojson, update_camera
from app.stream_manager import CameraWorker, StreamManager, StreamStillStopping


class StopAndLocationTests(unittest.TestCase):
    def test_reconnect_waits_for_old_worker_without_joining(self):
        manager = StreamManager()
        old = Mock(stream_url='old', _running=False)
        old.is_alive.return_value = True
        manager._workers['fixture'] = old
        with patch('app.stream_manager.CameraWorker') as factory:
            with self.assertRaises(StreamStillStopping):
                manager.add_stream('fixture', 'new')
            old.request_stop.assert_called_once()
            old.stop.assert_not_called()
            factory.assert_not_called()
            self.assertIs(manager._workers['fixture'], old)
            old.is_alive.return_value = False
            manager.add_stream('fixture', 'new')
            factory.return_value.start.assert_called_once()
            self.assertIs(manager._workers['fixture'], factory.return_value)

    def test_failed_start_retains_only_workers_with_running_threads(self):
        for alive in (False, True):
            manager = StreamManager()
            with patch('app.stream_manager.CameraWorker') as factory:
                worker = factory.return_value
                worker.start.side_effect = RuntimeError('start failed')
                worker.is_alive.return_value = alive
                with self.assertRaises(RuntimeError):
                    manager.add_stream('fixture', 'url')
                worker.request_stop.assert_called_once()
                self.assertEqual('fixture' in manager._workers, alive)
                self.assertEqual(worker.clip_buffer.close.called, not alive)

    def test_worker_health_reports_sandbox_replay_origin(self):
        manager = StreamManager()
        worker = CameraWorker('SENTINEL-CAM01', 'http://127.0.0.1/relay/cam01')
        try:
            manager._workers[worker.camera_id] = worker
            self.assertEqual(worker.origin, 'RECORDED')
            session = MagicMock()
            session.__enter__.return_value.get.return_value = None
            with patch('app.database.SessionLocal', return_value=session):
                self.assertEqual(manager.get_health(worker.camera_id)['origin'], 'RECORDED')
        finally:
            worker.clip_buffer.close()

    def test_stop_signals_without_joining_and_reaps_only_exited_worker(self):
        manager = StreamManager()
        worker = CameraWorker('fixture', 'rtsp://example.invalid/fixture')
        worker._running = True
        thread = Mock()
        thread.is_alive.return_value = True
        worker._thread = thread
        manager._workers['fixture'] = worker
        manager.request_stop('fixture')
        self.assertTrue(worker._stop.is_set())
        self.assertFalse(worker._running)
        thread.join.assert_not_called()
        self.assertIn('fixture', manager._workers)
        thread.is_alive.return_value = False
        self.assertEqual(manager.get_health('fixture')['status'], 'NOT_STREAMING')
        self.assertNotIn('fixture', manager._workers)
        worker.clip_buffer.close()

    def test_unknown_location_stays_in_inventory_without_geo_point(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        with Session(engine) as db, patch('app.camera_registry._live_health_payload', return_value={'status': 'NOT_STREAMING'}):
            db.add(CameraDB(id='fixture', name='Fixture', department='Home Department',
                            district='Unverified', lat=0, lon=0, location_known=False))
            db.commit()
            camera = get_camera(db, 'fixture')
            self.assertIsNone(camera['lat'])
            geo = get_cameras_geojson(db)
            self.assertEqual(geo['metadata']['total'], 1)
            self.assertIsNone(geo['features'][0]['geometry'])
            with self.assertRaises(HTTPException):
                update_camera(db, 'fixture', {'lat': 23})
            update_camera(db, 'fixture', {'lat': 0, 'lon': 0})
            db.commit()
            self.assertEqual(get_cameras_geojson(db)['features'][0]['geometry']['coordinates'], [0, 0])
        engine.dispose()
