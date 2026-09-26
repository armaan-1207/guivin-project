"""Offline PaddleOCR/EasyOCR development comparison on annotated PLATE boxes.

Optional dependencies belong in deploy/Dockerfile.ocr-evaluation, not the API.
Model downloads require --download-only. Run evaluation with Docker --network none.
"""
import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from time import perf_counter

from compare_ocr import load_samples, validate_disjoint
from app.evaluation import evaluate_rows
from app.plate_text import assemble_plate

DETECTION_MODEL = 'PP-OCRv5_mobile_det'
RECOGNITION_MODEL = 'en_PP-OCRv4_mobile_rec'


def observation(result):
    """Validate Paddle's aligned result arrays before existing plate assembly."""
    polygons, texts, scores = (result[key] for key in ('rec_polys', 'rec_texts', 'rec_scores'))
    if not len(polygons) == len(texts) == len(scores):
        raise ValueError('PaddleOCR result arrays have different lengths')
    parts = []
    for polygon, text, score in zip(polygons, texts, scores):
        score = float(score)
        if not isinstance(text, str) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('PaddleOCR returned invalid text or confidence')
        if len(polygon) != 4 or any(len(point) != 2 for point in polygon):
            raise ValueError('PaddleOCR returned an invalid text polygon')
        if any(not math.isfinite(float(v)) for point in polygon for v in point):
            raise ValueError('PaddleOCR returned a nonfinite polygon')
        parts.append((polygon, text, score))
    plate, confidence = assemble_plate(parts)
    return {'plate': plate, 'confidence': confidence}


def create_engine(model_root, download=False):
    options = {}
    if not download:
        for kind, name in [('detection', DETECTION_MODEL), ('recognition', RECOGNITION_MODEL)]:
            directory = Path(model_root) / name
            if not all((directory / f).is_file() for f in ('inference.json', 'inference.pdiparams', 'inference.yml')):
                raise FileNotFoundError('Prepare local Paddle models using --download-only first')
            options[f'text_{kind}_model_dir'] = str(directory)
    from paddleocr import PaddleOCR
    return PaddleOCR(text_detection_model_name=DETECTION_MODEL,
        text_recognition_model_name=RECOGNITION_MODEL, device='cpu', cpu_threads=2,
        enable_mkldnn=False, use_doc_orientation_classify=False,
        use_doc_unwarping=False, use_textline_orientation=False, **options)


def bounded_crop(crop):
    """Preserve color/layout while bounding detector work across crop sizes."""
    import cv2
    height, width = crop.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError('Plate crop is empty')
    factor = min(480 / width, 256 / height)
    resized = cv2.resize(crop, (max(1, round(width*factor)), max(1, round(height*factor))),
                         interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
    return cv2.copyMakeBorder(resized, 12, 12, 12, 12, cv2.BORDER_REPLICATE)


def compare(samples, readers, read_image, clock=perf_counter):
    """Pair identical crops, rotate order, exclude warm-up, retain no-read failures."""
    if not readers or not samples:
        raise ValueError('Samples and readers are required')
    rows = {name: [] for name in readers}
    inputs = []
    for index, (path, box, truth) in enumerate(samples):
        frame = read_image(str(path))
        x, y, w, h = box
        if frame is None or min(x, y) < 0 or min(w, h) <= 0 or x+w > frame.shape[1] or y+h > frame.shape[0]:
            raise ValueError('Unreadable image or plate box outside image bounds')
        crop = frame[y:y+h, x:x+w]
        inputs.append({'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(), 'box': list(box)})
        if index == 0:
            for read in readers.values():
                read(crop.copy())
        names = list(readers)
        names = names[index % len(names):] + names[:index % len(names)]
        for name in names:
            start = clock()
            item = readers[name](crop.copy())
            rows[name].append({'ground_truth_plate': truth, 'predicted_plate': item['plate'],
                'ocr_confidence': item['confidence'], 'latency_ms': (clock()-start)*1000})
        print(f'Evaluated {index+1}/{len(samples)} regions', file=sys.stderr, flush=True)
    summaries = {}
    for name, values in rows.items():
        metrics = evaluate_rows(values)
        negatives = [row for row in values if not row['ground_truth_plate']]
        false_reads = sum(bool(row['predicted_plate']) for row in negatives)
        metrics['negative_regions'] = {'samples': len(negatives), 'false_reads': false_reads,
            'false_read_rate': false_reads / len(negatives) if negatives else None}
        summaries[name] = metrics
    return {'scope': 'Supplied plate/explicit no-plate regions; not plate-detection or live-feed accuracy.',
        'timing': 'Warm crop-to-text wall time, including preprocessing; variant order rotated per sample.',
        'inputs': inputs, 'variants': summaries,
        'sample_results': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--model-root', default='/cache/.paddlex/official_models')
    parser.add_argument('--manifest')
    parser.add_argument('--images')
    parser.add_argument('--output')
    parser.add_argument('--bounded', action='store_true', help='Compare a bounded color crop and max-side detector limit')
    parser.add_argument('--development-manifest', action='append', default=[],
                        help='Reject overlap with these development manifests; repeat for each dataset')
    parser.add_argument('--development-images', action='append', default=[],
                        help='Matching image root for each development manifest, in the same order')
    args = parser.parse_args()
    if not args.download_only and not all([args.manifest, args.images, args.output]):
        parser.error('Evaluation needs --manifest, --images and --output')
    if args.output and Path(args.output).exists():
        parser.error('Output already exists; choose a new report path')
    if len(args.development_manifest) != len(args.development_images):
        parser.error('Each development manifest requires a matching --development-images root')
    samples = None
    split_check = None
    if not args.download_only:
        samples = load_samples(args.manifest, args.images, allow_negative=True)
        if args.development_manifest:
            development = [sample for manifest, root in zip(args.development_manifest, args.development_images)
                           for sample in load_samples(manifest, root, allow_negative=True)]
            split_check = validate_disjoint(samples, development)
    with contextlib.redirect_stdout(sys.stderr):
        engine = create_engine(args.model_root, args.download_only)
        if args.download_only:
            print('PaddleOCR evaluation models prepared.', file=sys.stderr)
            return
        import cv2
        import torch
        from app.ai_pipeline import read_plate_crop, _get_ocr
        torch.set_num_threads(2)
        cv2.setNumThreads(2)
        if _get_ocr() is None:
            raise RuntimeError('Local EasyOCR baseline models are unavailable')
        def paddle_read(crop):
            if args.bounded:
                results = list(engine.predict(bounded_crop(crop), text_det_limit_side_len=960,
                                              text_det_limit_type='max'))
            else:
                results = list(engine.predict(crop))
            if len(results) != 1:
                raise ValueError('Expected one PaddleOCR result per plate crop')
            return observation(results[0])
        report = compare(samples, {
            'easyocr_preserve': lambda crop: read_plate_crop(crop, preprocessing='preserve'),
            'paddleocr_mobile_bounded' if args.bounded else 'paddleocr_mobile': paddle_read}, cv2.imread)
    report['manifest_sha256'] = hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest()
    report['development_manifest_sha256'] = [hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                            for path in args.development_manifest]
    report['split_check'] = split_check
    report['evaluation_status'] = ('Disjoint from declared development manifests; label audit and representativeness not verified.'
                                   if split_check else 'Development comparison; no independent split verified.')
    report['versions'] = {name: importlib.metadata.version(name) for name in
                          ('paddleocr', 'paddlex', 'paddlepaddle', 'easyocr', 'torch', 'numpy')}
    report['paddle_preprocessing'] = 'color fit 480x256 + 12px border; detector max side 960' if args.bounded else 'PaddleOCR defaults'
    report['paddle_models'] = {name: {str(p.relative_to(Path(args.model_root) / name)):
        hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((Path(args.model_root) / name).rglob('*'))
        if p.is_file()} for name in (DETECTION_MODEL, RECOGNITION_MODEL)}
    with Path(args.output).open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps(report['variants'], indent=2))


if __name__ == '__main__':
    main()
