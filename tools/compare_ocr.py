"""Offline paired OCR comparison on explicitly labeled local vehicle frames.

CSV columns: image,x,y,w,h,ground_truth_plate. Paths are relative to --images.
Does not connect to cameras, write application data, or change runtime defaults.
"""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.evaluation import evaluate_rows
from app.watchlist_engine import normalise_plate


def load_samples(manifest, image_root):
    root = Path(image_root).resolve()
    samples = []
    seen = set()
    with Path(manifest).open(newline='', encoding='utf-8-sig') as source:
        for row in csv.DictReader(source):
            image = (root / row['image']).resolve()
            if not image.is_relative_to(root) or not image.is_file():
                raise ValueError('Every image must exist inside the supplied image folder')
            bbox = tuple(int(row[key]) for key in ('x', 'y', 'w', 'h'))
            if min(bbox[:2]) < 0 or min(bbox[2:]) <= 0:
                raise ValueError('Vehicle boxes need nonnegative positions and positive dimensions')
            truth = normalise_plate(row['ground_truth_plate'])
            if not truth:
                raise ValueError('Every sample needs an independently verified plate label')
            identity = (image, bbox)
            if identity in seen:
                raise ValueError('Duplicate image/vehicle box in evaluation manifest')
            seen.add(identity)
            samples.append((image, bbox, truth))
    if not samples:
        raise ValueError('No labeled samples provided')
    return samples


def compare(samples, read_image, recognize, clock=perf_counter, box_kind='vehicle', progress=None):
    variants = {2.5: [], 1.5: [], 1.0: []}
    digests = []
    for index, (path, bbox, truth) in enumerate(samples):
        frame = read_image(str(path))
        x, y, w, h = bbox
        if frame is None or x+w > frame.shape[1] or y+h > frame.shape[0]:
            raise ValueError('Unreadable image or vehicle box outside image bounds')
        digests.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
        if index == 0:
            for scale in variants:
                recognize(frame, bbox, ocr_scale=scale)  # exclude initialization from timing
        scales = list(variants)
        scales = scales[index % 3:] + scales[:index % 3]
        for scale in scales:
            start = clock()
            observation = recognize(frame, bbox, ocr_scale=scale)
            elapsed = (clock() - start) * 1000
            variants[scale].append({'ground_truth_plate': truth,
                'predicted_plate': observation['plate'], 'ocr_confidence': observation['confidence'],
                'latency_ms': elapsed})
        if progress:
            progress(index + 1)
    return {'sample_image_sha256': digests,
            'scope': f'Positive labeled {box_kind} boxes; single-frame OCR only, no watchlist or temporal consensus evaluation.',
            'timing': ('Warm rectification/OCR wall time; supplied plate boxes bypass localization.'
                       if box_kind == 'plate' else 'Warm localization/rectification/OCR wall time;')
                       + ' Variant order rotated per sample.',
            'variants': {str(scale): evaluate_rows(rows) for scale, rows in variants.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--images', required=True)
    parser.add_argument('--box-kind', choices=['vehicle', 'plate'], default='vehicle')
    args = parser.parse_args()
    samples = load_samples(args.manifest, args.images)
    import cv2
    import torch
    from app.ai_pipeline import extract_plate_observation, read_plate_crop, model_status
    torch.set_num_threads(2)
    cv2.setNumThreads(2)
    def recognize(frame, bbox, *, ocr_scale):
        if args.box_kind == 'plate':
            x, y, w, h = bbox
            return read_plate_crop(frame[y:y+h, x:x+w], ocr_scale=ocr_scale)
        return extract_plate_observation(frame, bbox, ocr_scale=ocr_scale, preprocessing='legacy')
    result = compare(samples, cv2.imread, recognize, box_kind=args.box_kind,
                     progress=lambda count: print(f'Evaluated {count}/{len(samples)} regions', file=sys.stderr, flush=True))
    result['manifest_sha256'] = hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()
    result['models'] = model_status()
    result['versions'] = {'torch': torch.__version__, 'opencv': cv2.__version__}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
