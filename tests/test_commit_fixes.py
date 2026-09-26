import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'backend'))
import evaluate_bapatla as benchmark


class BenchmarkRegression(unittest.TestCase):
    def test_extra_text_is_not_exact_and_corrupt_images_are_counted(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, report = root / 'data.zip', root / 'report.json'
            with zipfile.ZipFile(archive, 'w') as data:
                data.writestr('AN1.png', cv2.imencode('.png', np.zeros((30, 90, 3), dtype=np.uint8))[1].tobytes())
                data.writestr('AN2.png', b'not an image')
                data.writestr('AN3.png', b'')
            args = ['evaluate', '--zip', str(archive), '--model-root', str(root), '--output', str(report)]
            with patch.object(sys, 'argv', args), patch.object(benchmark, 'parse_ground_truth_from_zip', return_value={'AN1': 'AN01P9687', 'AN2': 'AN01D4153', 'AN3': 'AN01K9412'}), patch.object(benchmark, 'init_paddle_engine', return_value=Mock()), patch.object(benchmark, 'run_paddle_prediction', return_value=('EXTRAAN01P9687', 'AN01P9687', .9, 10)), contextlib.redirect_stdout(io.StringIO()):
                benchmark.main()
            result = json.loads(report.read_text())
            self.assertEqual(result['samples_evaluated'], 1)
            self.assertEqual(result['images_skipped'], 2)
            self.assertEqual(result['metrics']['raw_ocr']['exact_match_percent'], 0)
            self.assertEqual(result['metrics']['syntax_gated_anpr']['exact_match_percent'], 100)

    def test_missing_engine_stops_without_a_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, report = root / 'data.zip', root / 'report.json'
            with zipfile.ZipFile(archive, 'w'):
                pass
            with patch.object(sys, 'argv', ['evaluate', '--zip', str(archive), '--model-root', str(root), '--output', str(report)]), patch.object(benchmark, 'parse_ground_truth_from_zip', return_value={}), patch.object(benchmark, 'init_paddle_engine', side_effect=RuntimeError('missing model')):
                with self.assertRaisesRegex(SystemExit, 'PaddleOCR unavailable'):
                    benchmark.main()
            self.assertFalse(report.exists())


class RuntimeRegression(unittest.TestCase):
    def test_metrics_uses_active_camera_interface(self):
        from app.main import get_metrics, stream_manager
        with patch.object(stream_manager, 'active_cameras', return_value=['one', 'two']):
            response = get_metrics()
        self.assertIn(b'guivin_active_streams 2\n', response.body)

    def test_mock_alert_keeps_provenance(self):
        from app.watchlist_engine import check_plate
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = check_plate(db, 'GJ05CH9999')
        self.assertEqual(result['source_db'], 'VAHAN/CCTNS-MOCK')
        self.assertIn('DEMO MOCK DATA', result['additional_info'])

    def test_mock_owner_does_not_fill_local_watchlist(self):
        from app.watchlist_engine import check_plate
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = [Mock(
            identifier='GJ01AB1234', reason='Local entry', source_db='Local',
            owner_name='', additional_info='', priority='HIGH')]
        self.assertEqual(check_plate(db, 'GJ01AB1234')['owner_name'], '')


class SetupRegression(unittest.TestCase):
    def test_rerun_preserves_accounts_and_existing_bundle(self):
        import setup_docker
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'tmp/docker').mkdir(parents=True)
            (root / 'tmp/docker/users.json').write_text('existing account')
            (root / 'tmp/docker/admin-password.txt').write_text('existing password')
            (root / 'tmp/fast-alpr-paddle').mkdir()
            with patch.object(setup_docker, 'ROOT', root), patch.object(setup_docker, 'run') as run, patch('app.anpr_engine.load_local_engine') as load, contextlib.redirect_stdout(io.StringIO()):
                setup_docker.prepare()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(Path(run.call_args.args[1]).name, 'setup_models.py')
            load.return_value.predict.assert_called_once()
            self.assertEqual((root / 'tmp/docker/users.json').read_text(), 'existing account')
            self.assertEqual((root / 'tmp/docker/admin-password.txt').read_text(), 'existing password')

    def test_partial_credentials_fail_without_overwrite(self):
        import setup_docker
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'tmp/docker').mkdir(parents=True)
            (root / 'tmp/docker/users.json').write_text('existing account')
            with patch.object(setup_docker, 'ROOT', root), patch.object(setup_docker, 'run') as run:
                with self.assertRaisesRegex(SystemExit, 'Incomplete account setup'):
                    setup_docker.prepare()
            run.assert_not_called()
