import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.ai_pipeline import localize_plate, rectify_plate, model_status
from app.evaluation import evaluate_rows
from app.tracking import ByteTrackLite


class ResearchFeatureTests(unittest.TestCase):
    def test_tracker_associates_low_confidence_detection(self):
        tracker = ByteTrackLite(high_threshold=.5, low_threshold=.1, match_threshold=.3)
        first = [{'bbox': (10, 10, 40, 20), 'confidence': .9}]
        low = [{'bbox': (12, 10, 40, 20), 'confidence': .2}]
        first_id = tracker.update(first, 0.0)[0]
        self.assertEqual(tracker.update(low, 1.0)[0], first_id)
        self.assertNotEqual(tracker.update(first, 10.0)[0], first_id)

    def test_consensus_confidence_and_expiry(self):
        from collections import deque
        from app.tracking import plate_consensus
        track = {'votes': deque(maxlen=5)}
        self.assertEqual(plate_consensus(track, 'A', .8), ('', 0))
        self.assertAlmostEqual(plate_consensus(track, 'A', 1)[1], .9)
        plate, confidence = plate_consensus(track, 'B', .6)
        self.assertEqual(plate, 'A')
        self.assertAlmostEqual(confidence, .9)
        self.assertEqual(plate_consensus(track, 'B', .7), ('', 0))
        for _ in range(5):
            result = plate_consensus(track, '', 0)
        self.assertEqual(result, ('', 0))

    def test_plate_fallback_is_explicit_and_rectified(self):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        crop, source = localize_plate(frame, (10, 10, 80, 80))
        self.assertEqual(source, 'VEHICLE_BOTTOM_FALLBACK')
        self.assertGreater(crop.size, 0)
        self.assertEqual(rectify_plate(crop).shape[:2], (96, 320))
        self.assertEqual(model_status()['plate_detector'], 'NOT_CONFIGURED')

    def test_evaluation_missing_labels_auc_latency_and_invalid_values(self):
        missing = evaluate_rows([{}])
        self.assertIsNone(missing['alerts']['recall'])
        self.assertEqual(missing['alerts']['samples'],0)
        self.assertIsNone(missing['alert_score_evaluation']['roc_auc'])
        rows = [{'alert_truth':True,'alert_score':.9,'latency_ms':10},
                {'alert_truth':False,'alert_score':.1,'latency_ms':20}]
        result = evaluate_rows(rows)
        self.assertEqual(result['alert_score_evaluation']['roc_auc'],1)
        self.assertEqual(result['latency_ms']['p95'],20)
        rows[1]['alert_score'] = .9
        self.assertEqual(evaluate_rows(rows)['alert_score_evaluation']['roc_auc'],.5)
        for row in [{'ocr_confidence':'nan'},{'latency_ms':-1},{'alert_truth':'maybe','alert_prediction':True}]:
            with self.assertRaises(ValueError):
                evaluate_rows([row])

    def test_evaluation_reports_plate_and_alert_metrics(self):
        rows = [
            {'ground_truth_plate':'GJ01AB1234', 'predicted_plate':'GJ01AB1234',
             'plate_available':'yes', 'plate_detected':'yes', 'watchlist_truth':'yes',
             'watchlist_prediction':'yes', 'alert_truth':'yes', 'alert_prediction':'yes',
             'ocr_confidence':'0.9'},
            {'ground_truth_plate':'GJ01CD5678', 'predicted_plate':'GJ01CD567B',
             'plate_available':'yes', 'plate_detected':'yes', 'watchlist_truth':'no',
             'watchlist_prediction':'yes', 'alert_truth':'no', 'alert_prediction':'yes',
             'ocr_confidence':'0.7'},
        ]
        result = evaluate_rows(rows)
        self.assertEqual(result['samples'], 2)
        self.assertEqual(result['exact_plate_accuracy'], .5)
        self.assertEqual(result['plate_detection_recall'], 1.0)
        self.assertEqual(result['alerts']['false_positive'], 1)
        self.assertAlmostEqual(result['mean_ocr_confidence'], .8)


if __name__ == '__main__':
    unittest.main()
