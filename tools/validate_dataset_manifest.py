"""Validate an authorized ANPR manifest before using it for evaluation."""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

REQUIRED = {"image", "split", "camera_id", "capture_session", "vehicle_id", "plate_present"}
SPLITS = {"train", "validation", "test"}


def normalize_plate(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _bool(value, row_number):
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise ValueError(f"row {row_number}: plate_present must be true or false")


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(manifest, images_root, require_camera_disjoint=False):
    errors, rows = [], []
    with Path(manifest).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = set(reader.fieldnames or [])
        missing = REQUIRED - fields
        if missing:
            raise ValueError("missing required columns: " + ", ".join(sorted(missing)))
        for number, row in enumerate(reader, 2):
            split = str(row.get("split", "")).strip().lower()
            if split not in SPLITS:
                errors.append(f"row {number}: split must be train, validation or test")
            image = Path(str(row.get("image", "")).strip())
            image_path = Path(images_root) / image
            if not image.name or not image_path.is_file():
                errors.append(f"row {number}: image not found: {image}")
                image_hash = ""
            else:
                image_hash = _hash(image_path)
            try:
                present = _bool(row.get("plate_present"), number)
            except ValueError as exc:
                errors.append(str(exc))
                present = None
            plate = normalize_plate(row.get("plate_text"))
            if present is True and not plate:
                errors.append(f"row {number}: positive plate requires plate_text")
            if present is False and plate:
                errors.append(f"row {number}: negative plate must have empty plate_text")
            for key in ("camera_id", "capture_session", "vehicle_id"):
                if not str(row.get(key, "")).strip():
                    errors.append(f"row {number}: {key} is required")
            rows.append({"row": number, "split": split, "image_hash": image_hash,
                         "plate": plate, "vehicle": str(row.get("vehicle_id", "")).strip(),
                         "camera": str(row.get("camera_id", "")).strip()})

    for key, label in (("image_hash", "image bytes"), ("plate", "plate identity"), ("vehicle", "vehicle identity")):
        groups = {}
        for row in rows:
            value = row[key]
            if value:
                groups.setdefault(value, set()).add(row["split"])
        for value, splits in groups.items():
            if len(splits) > 1:
                errors.append(f"{label} crosses splits: {value[:16]} ({','.join(sorted(splits))})")
    if require_camera_disjoint:
        cameras = {}
        for row in rows:
            if row["camera"]:
                cameras.setdefault(row["camera"], set()).add(row["split"])
        for camera, splits in cameras.items():
            if len(splits) > 1:
                errors.append(f"camera crosses splits: {camera} ({','.join(sorted(splits))})")
    return rows, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--images", required=True)
    parser.add_argument("--require-camera-disjoint", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    rows, errors = validate_manifest(args.manifest, args.images, args.require_camera_disjoint)
    result = {"rows": len(rows), "errors": errors, "valid": not errors}
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
