import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.ai_pipeline import _draw_overlay


class OverlayTests(unittest.TestCase):
    def test_recorded_watermark_does_not_claim_live_scene(self):
        with patch('app.ai_pipeline.cv2.putText') as draw:
            _draw_overlay(np.zeros((100,300,3), dtype=np.uint8), 'fixture', 'RECORDED')
        labels = [call.args[1] for call in draw.call_args_list]
        self.assertIn('RECORDED', labels)
        self.assertNotIn('LIVE', labels)

    def test_bar_matches_previous_pixels_without_changing_rest_of_frame(self):
        rng = np.random.default_rng(42)
        for height, width in [(1080, 1920), (20, 100), (29, 100)]:
            with self.subTest(height=height):
                original = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
                old_overlay = original.copy()
                cv2.rectangle(old_overlay, (0, 0), (width, 28), (10, 20, 40), -1)
                expected = cv2.addWeighted(old_overlay, .75, original, .25, 0)
                actual = original.copy()
                # Isolate the changed bar; the existing text and badge are unchanged.
                with patch('app.ai_pipeline.cv2.putText'), patch('app.ai_pipeline.cv2.circle'):
                    _draw_overlay(actual, 'fixture')
                np.testing.assert_array_equal(actual, expected)
