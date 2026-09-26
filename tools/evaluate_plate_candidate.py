"""Offline full-image plate detection plus PaddleOCR on existing VOC annotations.

Not an independent benchmark: publisher labels may be incomplete, and model
training provenance is unknown. Run only in an isolated container without network.
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
import xml.etree.ElementTree as ET

from compare_paddleocr import create_engine, bounded_crop, observation
from app.evaluation import evaluate_rows
from app.watchlist_engine import normalise_plate


def iou(a, b):
    intersection = max(0, min(a[2],b[2])-max(a[0],b[0])) * max(0, min(a[3],b[3])-max(a[1],b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection/union if union > 0 else 0


def match_predictions(predictions, truths, threshold=.5):
    """Confidence-ordered one-to-one IoU matching; duplicate detections are false positives."""
    available = set(range(len(truths)))
    matches = []
    for index in sorted(range(len(predictions)), key=lambda i: predictions[i]['detection_confidence'], reverse=True):
        best = max(available, key=lambda j: iou(predictions[index]['box'], truths[j]['box']), default=None)
        if best is not None and iou(predictions[index]['box'], truths[best]['box']) >= threshold:
            matches.append((index, best))
            available.remove(best)
    return matches


def vehicle_regions(boxes, width, height):
    """Map predicted vehicle xyxy boxes to the application's padded crop bounds."""
    regions = []
    for box in boxes:
        if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
            raise ValueError('Invalid vehicle box')
        left, top, right, bottom = map(int, box)
        if right <= left or bottom <= top:
            continue
        x1,y1 = max(0,left-5),max(0,top)
        x2,y2 = min(width,right+5),min(height,bottom+20)
        if x2 > x1 and y2 > y1:
            regions.append((x1,y1,x2,y2))
    return regions


def suppress_duplicate_boxes(predictions, threshold=.5):
    selected = []
    for item in sorted(predictions, key=lambda p:p['detection_confidence'], reverse=True):
        if all(iou(item['box'], other['box']) <= threshold for other in selected):
            selected.append(item)
    return selected


def load_images(dataset):
    root = Path(dataset).resolve()
    annotations = {path.stem: path for path in (root/'Annotations').glob('*.xml')}
    rows = []
    for image in sorted((root/'images').iterdir()):
        if image.suffix.lower() not in {'.jpg','.jpeg','.png'} or image.stem not in annotations:
            continue
        if not image.resolve().is_relative_to(root):
            raise ValueError('Image must remain within dataset root')
        document = ET.parse(annotations[image.stem]).getroot()
        objects = []
        for item in document.findall('object'):
            if item.findtext('name') != 'number_plate':
                raise ValueError('Expected number_plate annotations only')
            box = item.find('bndbox')
            if box is None:
                raise ValueError('Annotation lacks bounding box')
            coords = [float(box.findtext(name)) for name in ('xmin','ymin','xmax','ymax')]
            if not all(math.isfinite(value) for value in coords) or min(coords) < 0 or coords[2] <= coords[0] or coords[3] <= coords[1]:
                raise ValueError('Invalid annotation coordinates')
            texts = [attr.findtext('value','') for attr in item.findall('./attributes/attribute')
                     if attr.findtext('name') == 'number_plate_text']
            objects.append({'box':coords, 'text':normalise_plate(texts[0]) if texts else ''})
        # Do not invent negative images from an empty or missing annotation.
        if objects:
            rows.append((image, annotations[image.stem], objects))
    if not rows:
        raise ValueError('No downloaded images with plate annotations')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--model-root', default='/cache/.paddlex/official_models')
    parser.add_argument('--output', required=True)
    parser.add_argument('--image-size', type=int, choices=[640,1280], default=640)
    parser.add_argument('--vehicle-model', help='Optional local COCO model for vehicle-first evaluation')
    args = parser.parse_args()
    if Path(args.output).exists():
        parser.error('Output exists; choose a new report path')
    if not Path(args.model).is_file():
        parser.error('Download the candidate locally first')
    if args.vehicle_model and not Path(args.vehicle_model).is_file():
        parser.error('Vehicle model must exist locally')
    samples = load_images(args.dataset)
    with contextlib.redirect_stdout(sys.stderr):
        import cv2
        import numpy as np
        import torch
        from ultralytics import YOLO
        torch.set_num_threads(2)
        cv2.setNumThreads(2)
        detector = YOLO(args.model)
        vehicle_detector = YOLO(args.vehicle_model) if args.vehicle_model else None
        if vehicle_detector:
            vehicle_classes = [index for index,name in vehicle_detector.names.items()
                               if name in {'car','truck','bus','motorcycle','bicycle'}]
            if not vehicle_classes:
                raise ValueError('Vehicle model has no expected vehicle classes')
            vehicle_detector(np.zeros((640,640,3),dtype=np.uint8),verbose=False,device='cpu')
        if len(detector.names) != 1:
            raise ValueError('Expected a single-class plate detector')
        reader = create_engine(args.model_root)
        def read(crop):
            results = list(reader.predict(bounded_crop(crop), text_det_limit_side_len=960, text_det_limit_type='max'))
            if len(results) != 1:
                raise ValueError('Expected one OCR result')
            return observation(results[0])
        detector(np.zeros((args.image_size,args.image_size,3),dtype=np.uint8),
                 imgsz=args.image_size, verbose=False, device='cpu')
        read(np.full((80,240,3),255,dtype=np.uint8))
        tp = fp = fn = 0
        ocr_rows, timing_rows, image_reports = [], [], []
        for index, (image, annotation, truths) in enumerate(samples):
            frame = cv2.imread(str(image))
            if frame is None:
                raise ValueError('Image cannot be decoded')
            height,width = frame.shape[:2]
            if any(t['box'][2] > width or t['box'][3] > height for t in truths):
                raise ValueError('Annotation outside image')
            start = perf_counter()
            regions = [(0,0,width,height)]
            if vehicle_detector:
                vehicles = vehicle_detector(frame, conf=.45, imgsz=640, classes=vehicle_classes,
                                            verbose=False, device='cpu')[0]
                regions = vehicle_regions([b.xyxy[0].tolist() for b in vehicles.boxes],width,height)
            detections = []
            for rx1,ry1,rx2,ry2 in regions:
                result = detector(frame[ry1:ry2,rx1:rx2], conf=.25, imgsz=args.image_size, verbose=False, device='cpu')[0]
                for box in result.boxes:
                    bx1,by1,bx2,by2 = map(float,box.xyxy[0].tolist())
                    detections.append({'box':[bx1+rx1,by1+ry1,bx2+rx1,by2+ry1],
                                       'detection_confidence':float(box.conf[0])})
            if vehicle_detector:
                detections = suppress_duplicate_boxes(detections)
            detection_ms = (perf_counter()-start)*1000
            predictions = []
            for box in detections:
                coords = box['box']
                x1,y1 = max(0,math.floor(coords[0])),max(0,math.floor(coords[1]))
                x2,y2 = min(width,math.ceil(coords[2])),min(height,math.ceil(coords[3]))
                if x2 <= x1 or y2 <= y1:
                    continue
                text = read(frame[y1:y2,x1:x2])
                predictions.append({**box, **text})
            timing_rows.append({'latency_ms':(perf_counter()-start)*1000})
            matches = match_predictions(predictions, truths)
            tp += len(matches)
            fp += len(predictions)-len(matches)
            fn += len(truths)-len(matches)
            matched = {truth: predictions[pred] for pred,truth in matches}
            for i, truth in enumerate(truths):
                if truth['text']:
                    prediction = matched.get(i, {})
                    ocr_rows.append({'ground_truth_plate':truth['text'], 'predicted_plate':prediction.get('plate',''),
                                     'ocr_confidence':prediction.get('confidence',0)})
            image_reports.append({'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                'annotation_sha256':hashlib.sha256(annotation.read_bytes()).hexdigest(),
                'ground_truth_boxes':len(truths),'regions':len(regions),
                'predictions':predictions,'matches':matches,'detection_ms':detection_ms})
            print(f'Evaluated full image {index+1}/{len(samples)}',file=sys.stderr,flush=True)
    precision = tp/(tp+fp) if tp+fp else 0
    recall = tp/(tp+fn) if tp+fn else 0
    report = {'scope':'Exploratory detection and OCR on reused development images; no tracking/temporal voting; training overlap unknown.',
        'settings':{'confidence':.25,'image_size':args.image_size,'match_iou':.5,'ocr':'bounded PaddleOCR'},
        'model_sha256':hashlib.sha256(Path(args.model).read_bytes()).hexdigest(),
        'vehicle_stage': {'enabled':bool(args.vehicle_model),'confidence':.45,'image_size':640,'cross_crop_nms_iou':.5,
            'model_sha256':hashlib.sha256(Path(args.vehicle_model).read_bytes()).hexdigest() if args.vehicle_model else None},
        'versions':{name:importlib.metadata.version(name) for name in ('ultralytics','torch','paddleocr','paddlepaddle')},
        'images':len(samples),'detection':{'true_positive':tp,'false_positive':fp,'false_negative':fn,
            'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0},
        'ocr_including_detection_misses':evaluate_rows(ocr_rows),
        'frame_latency_ms':evaluate_rows(timing_rows)['latency_ms'],
        'image_results':image_reports,'ocr_results':ocr_rows}
    with Path(args.output).open('x',encoding='utf-8') as output:
        json.dump(report,output,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k not in {'image_results','ocr_results'}},indent=2))


if __name__ == '__main__':
    main()
