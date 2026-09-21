import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from compare_ocr import load_samples, compare
from app.ai_pipeline import extract_plate_observation, read_plate_crop


class OCRComparisonTests(unittest.TestCase):
    def test_manifest_rejects_missing_labels_duplicates_and_escaping_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'frame.png').write_bytes(b'fixture')
            manifest = root / 'labels.csv'
            header = 'image,x,y,w,h,ground_truth_plate\n'
            valid = 'frame.png,0,0,20,20,GJ01AB1234\n'
            for rows in ['frame.png,0,0,20,20,\n', valid+valid,
                         '../outside.png,0,0,20,20,GJ01AB1234\n',
                         'frame.png,0,0,0,20,GJ01AB1234\n', '']:
                manifest.write_text(header + rows)
                with self.assertRaises(ValueError):
                    load_samples(manifest, root)
            manifest.write_text(header + valid)
            self.assertEqual(len(load_samples(manifest, root)), 1)

    def test_paired_accuracy_includes_failed_reads_and_excludes_warmup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture'
            path.write_bytes(b'fixture')
            calls = []
            def recognize(frame, bbox, *, ocr_scale):
                calls.append(ocr_scale)
                return {'plate': 'GJ01AB1234' if ocr_scale == 2.5 else '', 'confidence': .9}
            ticks = iter(range(100))
            result = compare([(path, (0,0,20,20), 'GJ01AB1234')] * 2,
                             lambda _: np.zeros((20,20,3)), recognize, lambda: next(ticks))
            self.assertEqual(calls, [2.5,1.5,1.0,2.5,1.5,1.0,1.5,1.0,2.5])
            self.assertEqual(result['variants']['2.5']['samples'], 2)
            self.assertEqual(result['variants']['2.5']['exact_plate_accuracy'], 1)
            self.assertEqual(result['variants']['1.0']['exact_plate_accuracy'], 0)
            self.assertEqual(result['variants']['2.5']['latency_ms']['p50'], 1000)

    def test_invalid_scale_never_loads_models(self):
        with patch('app.ai_pipeline._get_ocr') as load:
            for scale in [0, 3, float('nan'), float('inf')]:
                with self.assertRaises(ValueError):
                    extract_plate_observation(np.zeros((20,20,3)), (0,0,20,20), ocr_scale=scale)
            load.assert_not_called()

    def test_annotated_plate_crop_skips_vehicle_localization(self):
        reader = Mock()
        reader.readtext.return_value = [(None, 'GJ01AB1234', .9)]
        crop = np.full((80, 240, 3), 255, dtype=np.uint8)
        with patch('app.ai_pipeline._get_ocr', return_value=reader), \
             patch('app.ai_pipeline.localize_plate') as localize:
            result = read_plate_crop(crop, ocr_scale=1.0)
        localize.assert_not_called()
        self.assertEqual(result['localization'], 'ANNOTATED_PLATE_CROP')
        self.assertEqual(result['plate'], 'GJ01AB1234')
        self.assertEqual(reader.readtext.call_args.args[0].shape, (96, 320))

    def test_preserved_crop_keeps_aspect_and_assembles_lines_without_warp(self):
        reader = Mock()
        reader.readtext.return_value = [
            ([[0,0],[80,0],[80,20],[0,20]], 'GJ01', .9),
            ([[0,25],[120,25],[120,45],[0,45]], 'AB1234', .8)]
        crop = np.full((80, 240, 3), 180, dtype=np.uint8)
        with patch('app.ai_pipeline._get_ocr', return_value=reader), \
             patch('app.ai_pipeline.rectify_plate') as warp:
            result = read_plate_crop(crop, preprocessing='preserve')
        warp.assert_not_called()
        self.assertEqual(reader.readtext.call_args.args[0].shape, (184, 504))
        self.assertEqual(result['plate'], 'GJ01AB1234')
        self.assertEqual(result['confidence'], .8)
        self.assertFalse(result['rectified'])

    def test_direct_recognition_cannot_bypass_detection_for_vehicle_fallback(self):
        reader = Mock()
        reader.readtext.return_value = []
        with patch('app.ai_pipeline._get_ocr', return_value=reader):
            read_plate_crop(np.ones((80, 300, 3), dtype=np.uint8),
                            preprocessing='preserve_direct', localization='VEHICLE_BOTTOM_FALLBACK')
        reader.recognize.assert_not_called()
        reader.readtext.assert_called_once()
