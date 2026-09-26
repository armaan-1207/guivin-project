import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.anpr_engine import Engine, load_local_engine, select_observation


def result(text='GJ01AB1234', score=.9, detector=.8):
    return NS(ocr=NS(text=text, confidence=score), detection=NS(confidence=detector))


class FastALPRTests(unittest.TestCase):
    def test_conservative_confidence_and_normalization(self):
        read = select_observation([result('GJ 01-AB 1234', [.99, .7])])
        self.assertEqual(read['plate'], 'GJ01AB1234')
        self.assertEqual(read['confidence'], .7)

    def test_ambiguous_neighbour_and_invalid_reads_are_rejected(self):
        for results in ([result(), result('MH12AB1234')], [result('PARKING123')],
                        [result(score=float('nan'))], [result(score=[])],
                        [result(detector=.1)], [NS(ocr=None)]):
            self.assertEqual(select_observation(results)['plate'], '')

    def test_failure_status_and_backoff(self):
        factory = Mock(side_effect=ValueError('bad model'))
        engine = Engine(factory)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                engine.predict(None)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(engine.status(), 'FAILED')

    def test_local_setup_required_before_import_or_download(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                load_local_engine(folder)

    def test_vehicle_crop_clips_without_wrapping(self):
        import numpy as np
        model = Mock()
        model.predict.return_value = [result()]
        engine = Engine(lambda: model)
        frame = np.zeros((60, 100, 3), dtype=np.uint8)
        self.assertEqual(engine.observe(frame, (-10, -5, 40, 30))['plate'], 'GJ01AB1234')
        self.assertEqual(model.predict.call_args.args[0].shape, (25, 30, 3))
        self.assertEqual(engine.observe(frame, (120, 0, 10, 10))['plate'], '')
        self.assertEqual(model.predict.call_count, 1)

    def test_corrupt_artifact_is_rejected_before_model_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'detector.onnx').write_bytes(b'corrupt')
            (root / 'manifest.json').write_text(json.dumps({'files': {
                'detector': {'name': 'detector.onnx', 'sha256': '0' * 64}}}))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                load_local_engine(root)

    def test_pipeline_routes_and_reports_selected_engine(self):
        import numpy as np
        from app import ai_pipeline
        engine = Mock(error=None)
        engine.status.return_value = 'READY'
        engine.observe.return_value = {'plate': 'GJ01AB1234', 'confidence': .8}
        with patch.object(ai_pipeline, 'ANPR_BACKEND', 'fast_alpr'), \
                patch.object(ai_pipeline, '_anpr', engine), \
                patch.object(ai_pipeline, '_get_ocr') as legacy:
            value = ai_pipeline.extract_plate_observation(np.zeros((10, 10, 3)), (0, 0, 10, 10))
            self.assertEqual(value['plate'], 'GJ01AB1234')
            self.assertEqual(ai_pipeline.model_status()['anpr_backend'], 'fast_alpr')
            legacy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
