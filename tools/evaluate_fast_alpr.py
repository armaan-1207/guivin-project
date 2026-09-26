"""Full-frame development evaluation; not a held-out accuracy claim."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from time import perf_counter

from evaluate_plate_candidate import load_images, match_predictions
from app.anpr_engine import load_local_engine, select_observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--models', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error('Choose a new report path; reports are never overwritten')
    import cv2
    import numpy as np
    cv2.setNumThreads(2)
    engine = load_local_engine(args.models)
    engine.predict(np.zeros((384, 384, 3), dtype=np.uint8))
    timings, records = [], []
    matched = false_positive = missed = exact = labeled = 0
    for image, annotation, truths in load_images(args.dataset):
        truths = [dict(truth, text=re.sub('[^A-Z0-9]', '', truth['text'].upper())) for truth in truths]
        frame = cv2.imread(str(image))
        if frame is None:
            raise ValueError('Unreadable evaluation image')
        start = perf_counter()
        outputs = engine.predict(frame)
        timings.append((perf_counter() - start) * 1000)
        predictions = []
        for output in outputs:
            box = output.detection.bounding_box
            predictions.append({'box': [box.x1, box.y1, box.x2, box.y2],
                                'detection_confidence': output.detection.confidence,
                                'plate': select_observation([output])['plate']})
        pairs = match_predictions(predictions, truths)
        matched += len(pairs)
        false_positive += len(predictions) - len(pairs)
        missed += len(truths) - len(pairs)
        labeled += sum(bool(truth['text']) for truth in truths)
        exact += sum(bool(truths[j]['text']) and predictions[i]['plate'] == truths[j]['text'] for i, j in pairs)
        records.append({'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                        'annotation_sha256': hashlib.sha256(annotation.read_bytes()).hexdigest()})
    report = {'scope': 'Reused development full frames; no independent accuracy or negative-scene validation.',
              'images': len(records), 'true_positive': matched, 'false_positive': false_positive,
              'false_negative': missed, 'labeled_plates': labeled, 'exact_reads': exact,
              'median_ms': float(np.median(timings)), 'p95_ms': float(np.percentile(timings, 95)),
              'inputs': records, 'models': json.loads((Path(args.models) / 'manifest.json').read_text())}
    with Path(args.output).open('x', encoding='utf-8') as target:
        json.dump(report, target, indent=2)
    print(json.dumps({key: value for key, value in report.items() if key not in {'inputs', 'models'}}, indent=2))


if __name__ == '__main__':
    main()
