import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from compare_paddleocr import compare, create_engine, observation, bounded_crop


class PaddleComparisonTests(unittest.TestCase):
    def test_bounded_crop_preserves_aspect_and_limits_work(self):
        for height, width in [(80,240), (1000,100), (10,1000)]:
            result = bounded_crop(np.ones((height,width,3), dtype=np.uint8))
            self.assertLessEqual(result.shape[0], 280)
            self.assertLessEqual(result.shape[1], 504)
            factor = min(480/width, 256/height)
            self.assertEqual(result.shape[:2], (max(1,round(height*factor))+24, max(1,round(width*factor))+24))

    def test_assembles_multiline_with_weakest_confidence(self):
        result = observation({'rec_texts': ['GJ01', 'AB1234'], 'rec_scores': [.9, .8],
            'rec_polys': [[[0,0],[80,0],[80,20],[0,20]], [[0,25],[120,25],[120,45],[0,45]]]})
        self.assertEqual(result, {'plate': 'GJ01AB1234', 'confidence': .8})

    def test_empty_text_is_a_failed_read_not_an_invented_plate(self):
        self.assertEqual(observation({'rec_texts': [], 'rec_scores': [], 'rec_polys': []}),
                         {'plate': '', 'confidence': 0})

    def test_misaligned_and_nonfinite_results_fail_loudly(self):
        polygon = [[0,0],[100,0],[100,20],[0,20]]
        for result in [
            {'rec_texts': ['GJ01AB1234'], 'rec_scores': [], 'rec_polys': [polygon]},
            {'rec_texts': ['GJ01AB1234'], 'rec_scores': [float('nan')], 'rec_polys': [polygon]},
            {'rec_texts': ['GJ01AB1234'], 'rec_scores': [.8], 'rec_polys': [[[float('inf'),0]]*4]},
        ]:
            with self.assertRaises(ValueError):
                observation(result)

    def test_missing_models_fail_before_import_or_download(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {'paddleocr': Mock()}) as modules:
            with self.assertRaises(FileNotFoundError):
                create_engine(folder)
            modules['paddleocr'].PaddleOCR.assert_not_called()

    def test_comparison_pairs_crops_rotates_order_and_counts_no_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture'
            path.write_bytes(b'fixture')
            calls = []
            def read(name, plate):
                def run(crop):
                    self.assertEqual(crop.shape, (10,20,3))
                    calls.append(name)
                    return {'plate': plate, 'confidence': .8 if plate else 0}
                return run
            ticks = iter(range(100))
            result = compare([(path,(2,3,20,10),'GJ01AB1234')]*2,
                {'baseline': read('baseline', 'GJ01AB1234'), 'candidate': read('candidate', '')},
                lambda _: np.zeros((30,30,3)), lambda: next(ticks))
            self.assertEqual(calls, ['baseline','candidate','baseline','candidate','candidate','baseline'])
            self.assertEqual(result['variants']['candidate']['exact_plate_accuracy'], 0)
            self.assertEqual(result['variants']['baseline']['samples'], 2)
            self.assertEqual(result['variants']['baseline']['latency_ms']['p50'], 1000)

    def test_invalid_crop_does_not_call_reader(self):
        reader = Mock()
        with self.assertRaises(ValueError):
            compare([(Path('unused'),(0,0,31,10),'GJ01AB1234')], {'candidate': reader},
                    lambda _: np.zeros((30,30,3)))
        reader.assert_not_called()
