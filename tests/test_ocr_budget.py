import sys
import unittest
from pathlib import Path
from collections import deque
from unittest.mock import patch, MagicMock
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.ocr_budget import select_ocr_tracks
from app.tracking import plate_consensus


class OCRBudgetTests(unittest.TestCase):
    def test_worker_limits_ocr_but_preserves_all_vehicle_records(self):
        import numpy as np
        from app.stream_manager import CameraWorker
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        detections = [{'class': 'car', 'is_vehicle': True, 'confidence': .9,
                       'bbox': (i * 20, 0, 15, 30)} for i in range(8)]
        worker = CameraWorker('fixture', 'unused')
        worker._capture_done.set()
        worker._jobs.put((frame, datetime.utcnow(), 1.0, 0, worker.clip_buffer))
        try:
            with patch('app.ai_pipeline.detect_frame', return_value=(frame, detections)), \
                 patch('app.ai_pipeline.extract_plate_observation', return_value={'plate': 'GJ01AB1234', 'confidence': .9}) as ocr, \
                 patch('app.processing.process_detection', return_value=None) as persist, \
                 patch('app.scene_rules.SceneEvaluator.process', return_value=[]), \
                 patch('app.aci_learning.observe'), patch('app.database.SessionLocal', return_value=MagicMock()), \
                 patch('app.stream_manager.OCR_MAX_TRACKS_PER_FRAME', 3):
                worker._inference_loop()
            self.assertEqual(ocr.call_count, 3)
            self.assertEqual(persist.call_count, 8)
            self.assertEqual(worker.ocr_skipped, 5)
            self.assertEqual(set(worker.last_stage_seconds),
                             {'detection', 'plate_localization_and_ocr', 'tracking_rules_and_persistence'})
            self.assertTrue(all(value >= 0 for value in worker.last_stage_seconds.values()))
            self.assertAlmostEqual(sum(worker.last_stage_seconds.values()), worker.last_inference_seconds)
            self.assertTrue(all(call.args[2] == '' for call in persist.call_args_list))
            deferred = [c for c in persist.call_args_list if c.kwargs['plate_localization'] == 'OCR_DEFERRED_BUDGET']
            self.assertEqual(len(deferred), 5)
        finally:
            worker.clip_buffer.close()

    def test_budget_rotates_tracks_and_uses_media_time(self):
        tracks = {str(n): {} for n in range(7)}
        seen = set()
        for now in [0, .1, .2]:
            selected = select_ocr_tracks(tracks, list(tracks), now)
            self.assertLessEqual(len(selected), 3)
            seen.update(selected)
            for tid in selected:
                tracks[tid]['ocr_attempt_at'] = now
        self.assertEqual(seen, set(tracks))
        self.assertEqual(select_ocr_tracks(tracks, list(tracks), .3), set())
        self.assertTrue(select_ocr_tracks(tracks, list(tracks), .7))

    def test_selection_does_not_invent_plate_votes(self):
        track = {'votes': deque(maxlen=5)}
        self.assertEqual(plate_consensus(track, 'GJ01AB1234', .9), ('', 0.0))
        track['ocr_attempt_at'] = 1
        for _ in range(10):
            self.assertEqual(select_ocr_tracks({'a': track}, ['a'], 1.1), set())
        self.assertEqual(len(track['votes']), 1)
        self.assertEqual(plate_consensus(track, 'GJ01AB1234', .9)[0], 'GJ01AB1234')

    def test_empty_duplicate_and_reset_tracks(self):
        self.assertEqual(select_ocr_tracks({'a': {}}, ['', 'a', 'a'], 0), {'a'})
        self.assertEqual(select_ocr_tracks({'a': {'ocr_attempt_at': 10}}, ['a'], 0), {'a'})
