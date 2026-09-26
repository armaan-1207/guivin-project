import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.paddle_anpr import PaddlePlateReader, read_result


def valid_result():
    return {'rec_polys': [[[0, 0], [100, 0], [100, 20], [0, 20]]],
            'rec_texts': ['GJ01AB1234'], 'rec_scores': [.95]}


class PaddleANPRTests(unittest.TestCase):
    def test_assembly_and_invalid_output(self):
        self.assertEqual(read_result(valid_result()), ('GJ01AB1234', .95))
        for field, value in [('rec_scores', []), ('rec_scores', [float('nan')]),
                             ('rec_polys', [[[0, 1]]]), ('rec_texts', [None])]:
            result = valid_result()
            result[field] = value
            with self.assertRaises(ValueError):
                read_result(result)

    def test_absent_models_fail_before_optional_import(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                PaddlePlateReader(root)

    def test_bounded_preprocessing_and_result_count(self):
        import numpy as np
        # FastALPR is optional in the base image; mock its value object only.
        from types import SimpleNamespace
        from unittest.mock import patch
        reader = PaddlePlateReader.__new__(PaddlePlateReader)
        reader.engine = Mock()
        reader.engine.predict.return_value = [valid_result()]
        with patch.dict(sys.modules, {'fast_alpr.base': SimpleNamespace(OcrResult=SimpleNamespace)}):
            result = reader.predict(np.zeros((100, 1000, 3), dtype=np.uint8))
            self.assertEqual(result.text, 'GJ01AB1234')
            self.assertEqual(reader.engine.predict.call_args.args[0].shape, (72, 504, 3))
            reader.engine.predict.return_value = []
            with self.assertRaises(ValueError):
                reader.predict(np.zeros((20, 50, 3), dtype=np.uint8))
        self.assertIsNone(reader.predict(np.zeros((0, 0, 3), dtype=np.uint8)))


if __name__ == '__main__':
    unittest.main()
