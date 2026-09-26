import csv
import tempfile
import unittest
from pathlib import Path

from tools.validate_dataset_manifest import validate_manifest


class DatasetManifestTests(unittest.TestCase):
    def write_case(self, rows, files=("a.jpg", "b.jpg")):
        root = Path(tempfile.mkdtemp())
        for name in files:
            (root / name).write_bytes(name.encode())
        manifest = root / "labels.csv"
        with manifest.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
        return manifest, root

    def test_valid_positive_and_explicit_negative(self):
        rows = [
            {"image":"a.jpg","split":"test","camera_id":"C1","capture_session":"S1","vehicle_id":"V1","plate_present":"true","plate_text":"GJ01AB1234"},
            {"image":"b.jpg","split":"test","camera_id":"C1","capture_session":"S1","vehicle_id":"V2","plate_present":"false","plate_text":""},
        ]
        manifest, root = self.write_case(rows)
        self.assertEqual(validate_manifest(manifest, root)[1], [])

    def test_rejects_missing_positive_text_and_vehicle_leak(self):
        rows = [
            {"image":"a.jpg","split":"train","camera_id":"C1","capture_session":"S1","vehicle_id":"V1","plate_present":"true","plate_text":""},
            {"image":"b.jpg","split":"test","camera_id":"C2","capture_session":"S2","vehicle_id":"V1","plate_present":"false","plate_text":""},
        ]
        manifest, root = self.write_case(rows)
        errors = validate_manifest(manifest, root)[1]
        self.assertTrue(any("positive plate requires" in error for error in errors))
        self.assertTrue(any("vehicle identity crosses" in error for error in errors))

    def test_optional_camera_disjointness(self):
        rows = [
            {"image":"a.jpg","split":"train","camera_id":"C1","capture_session":"S1","vehicle_id":"V1","plate_present":"false","plate_text":""},
            {"image":"b.jpg","split":"test","camera_id":"C1","capture_session":"S2","vehicle_id":"V2","plate_present":"false","plate_text":""},
        ]
        manifest, root = self.write_case(rows)
        errors = validate_manifest(manifest, root, require_camera_disjoint=True)[1]
        self.assertTrue(any("camera crosses" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
