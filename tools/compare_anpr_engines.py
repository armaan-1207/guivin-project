"""Paired development comparison of three complete ANPR configurations."""
import argparse
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from time import perf_counter

from app.anpr_engine import load_local_engine, select_observation
from compare_paddleocr import create_engine, bounded_crop, observation
from evaluate_plate_candidate import load_images, match_predictions, suppress_duplicate_boxes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--small', required=True)
    parser.add_argument('--large', required=True)
    parser.add_argument('--paddle', required=True)
    parser.add_argument('--vehicle-model')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error('Choose a new report filename')
    import cv2
    import numpy as np
    from fast_alpr import ALPR
    from fast_alpr.base import OcrResult
    cv2.setNumThreads(2)
    small, large = load_local_engine(args.small), load_local_engine(args.large)
    paddle = create_engine(args.paddle)

    class PaddleReader:
        def predict(self, crop):
            if crop.size == 0:
                return None
            values = list(paddle.predict(bounded_crop(crop), text_det_limit_side_len=960,
                                         text_det_limit_type='max'))
            read = observation(values[0]) if values else {'plate': '', 'confidence': 0.0}
            return OcrResult(text=read['plate'], confidence=read['confidence'])

    engines = {'fast_small': small, 'fast_large': large,
               'large_paddle': ALPR(detector=large.detector, ocr=PaddleReader())}
    vehicle = None
    if args.vehicle_model:
        import torch
        from ultralytics import YOLO
        torch.set_num_threads(2)
        vehicle = YOLO(args.vehicle_model)
    # Warm all recognition and detection paths on the same nonempty image.
    samples = load_images(args.dataset)
    warm = cv2.imread(str(samples[0][0]))
    for engine in engines.values():
        engine.predict(warm)
        engine.ocr.predict(warm[:100, :250])
    reports = {name: {'tp': 0, 'fp': 0, 'fn': 0, 'exact': 0, 'labeled': 0,
                      'latencies_ms': [], 'frames': []} for name in engines}
    for index, (path, annotation, truths) in enumerate(samples):
        frame = cv2.imread(str(path))
        truths = [dict(t, text=re.sub('[^A-Z0-9]', '', t['text'].upper())) for t in truths]
        regions = [(0, 0, frame.shape[1], frame.shape[0])]
        if vehicle:
            regions = []
            for box in vehicle(frame, verbose=False, conf=.45)[0].boxes:
                if vehicle.names[int(box.cls[0])] in {'car', 'truck', 'bus', 'motorcycle', 'bicycle'}:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    regions.append((max(0, x1), max(0, y1), min(frame.shape[1], x2), min(frame.shape[0], y2)))
        names = list(engines)
        names = names[index % 3:] + names[:index % 3]
        for name in names:
            start = perf_counter()
            predictions = []
            for x1, y1, x2, y2 in regions:
                outputs = engines[name].predict(frame[y1:y2, x1:x2])
                accepted = select_observation(outputs)['plate'] if vehicle else None
                for output in outputs:
                    box = output.detection.bounding_box
                    text = select_observation([output])['plate']
                    if vehicle and text != accepted:
                        text = ''
                    predictions.append({'box': [x1+box.x1, y1+box.y1, x1+box.x2, y1+box.y2],
                                        'detection_confidence': output.detection.confidence, 'plate': text})
            predictions = suppress_duplicate_boxes(predictions)
            elapsed = (perf_counter()-start)*1000
            pairs = match_predictions(predictions, truths)
            report = reports[name]
            report['tp'] += len(pairs)
            report['fp'] += len(predictions)-len(pairs)
            report['fn'] += len(truths)-len(pairs)
            report['labeled'] += sum(bool(t['text']) for t in truths)
            report['exact'] += sum(bool(truths[j]['text']) and predictions[i]['plate'] == truths[j]['text'] for i,j in pairs)
            report['latencies_ms'].append(elapsed)
            report['frames'].append({'image_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                      'predictions': predictions, 'truths': truths})
        print(f'Compared {index+1}/{len(samples)}', flush=True)
    for report in reports.values():
        report['median_ms'] = float(np.median(report['latencies_ms']))
        report['p95_ms'] = float(np.percentile(report['latencies_ms'], 95))
    result = {'scope': 'Reused development sample, no held-out or negative-scene validation.',
              'mode': 'vehicle_crops' if vehicle else 'full_frames',
              'timing': 'Rotated paired ANPR timing; shared vehicle detection and scheduling excluded.',
              'models': {name: json.loads((Path(root)/'manifest.json').read_text())
                         for name, root in [('small', args.small), ('large', args.large)]},
              'paddle_files': {str(path.relative_to(args.paddle)): hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in Path(args.paddle).rglob('*')
                               if path.is_file() and path.name in {'inference.json', 'inference.yml', 'inference.pdiparams'}},
              'packages': {name: importlib.metadata.version(name) for name in
                           ('fast-alpr', 'fast-plate-ocr', 'open-image-models', 'onnxruntime', 'paddleocr', 'paddlepaddle')},
              'vehicle_model_sha256': hashlib.sha256(Path(args.vehicle_model).read_bytes()).hexdigest() if vehicle else None,
              'variants': reports}
    with Path(args.output).open('x', encoding='utf-8') as out:
        json.dump(result, out, indent=2)
    print(json.dumps({name: {k:v for k,v in r.items() if k not in {'frames', 'latencies_ms'}}
                      for name,r in reports.items()}, indent=2))


if __name__ == '__main__':
    main()
