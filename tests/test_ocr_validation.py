import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from compare_ocr import load_samples, validate_disjoint
from compare_paddleocr import compare


class OCRValidationTests(unittest.TestCase):
    def test_only_explicit_negatives_allow_empty_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'image.jpg').write_bytes(b'fixture')
            manifest = root/'labels.csv'
            header = 'image,x,y,w,h,ground_truth_plate,plate_present\n'
            for text, present in [('', ''), ('', 'true'), ('GJ01AB1234', 'false'), ('', 'maybe')]:
                manifest.write_text(header+f'image.jpg,0,0,10,10,{text},{present}\n')
                with self.assertRaises(ValueError):
                    load_samples(manifest, root, allow_negative=True)
            manifest.write_text(header+'image.jpg,0,0,10,10,,false\n')
            self.assertEqual(load_samples(manifest, root, allow_negative=True)[0][2], '')
            with self.assertRaises(ValueError):
                load_samples(manifest, root)

    def test_renamed_images_and_repeated_plates_cannot_cross_split(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, renamed, other = [root/name for name in ('source', 'renamed', 'other')]
            source.write_bytes(b'identical-image')
            renamed.write_bytes(b'identical-image')
            other.write_bytes(b'different-image')
            development = [(source, (0,0,10,10), 'GJ01AB1234')]
            for sample in [(renamed,(2,2,3,3),'MH02CD5678'), (other,(0,0,10,10),'GJ01AB1234')]:
                with self.assertRaises(ValueError):
                    validate_disjoint([sample], development)
            report = validate_disjoint([(other,(0,0,10,10),'MH02CD5678')], development)
            self.assertEqual(report['image_overlap'], 0)
            self.assertEqual(report['plate_overlap'], 0)
            with self.assertRaises(ValueError):
                validate_disjoint([(other,(0,0,10,10),'MH02CD5678')], [])

    def test_negative_false_reads_are_separate_from_positive_accuracy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'image'
            path.write_bytes(b'image')
            samples = [(path,(0,0,10,10), truth) for truth in ('GJ01AB1234', '', '')]
            result = compare(samples, {'candidate': lambda crop: {'plate':'GJ01AB1234','confidence':.9}},
                             lambda _: np.zeros((10,10,3)))
            metrics = result['variants']['candidate']
            self.assertEqual(metrics['exact_plate_accuracy'], 1)
            self.assertEqual(metrics['labeled_plate_samples'], 1)
            self.assertEqual(metrics['negative_regions'], {'samples':2,'false_reads':2,'false_read_rate':1})

    def test_missing_negatives_do_not_imply_zero_false_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'image'
            path.write_bytes(b'image')
            result = compare([(path,(0,0,10,10),'GJ01AB1234')],
                {'candidate': lambda crop: {'plate':'','confidence':0}}, lambda _: np.zeros((10,10,3)))
            self.assertIsNone(result['variants']['candidate']['negative_regions']['false_read_rate'])
