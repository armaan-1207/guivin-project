"""Development comparison of existing OCR against aspect-preserving multi-line OCR."""
import argparse
import hashlib
import json
import sys
from time import perf_counter
from compare_ocr import load_samples
from app.evaluation import evaluate_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--images', required=True)
    parser.add_argument('--modes', nargs='+', default=['legacy', 'preserve', 'preserve_direct'],
                        choices=['legacy', 'preserve', 'preserve_direct', 'preserve_beam'])
    args = parser.parse_args()
    samples = load_samples(args.manifest, args.images)
    import cv2
    import torch
    from app.ai_pipeline import read_plate_crop
    torch.set_num_threads(2)
    cv2.setNumThreads(2)
    rows = {mode: [] for mode in args.modes}
    for index, (path, bbox, truth) in enumerate(samples):
        frame = cv2.imread(str(path))
        x,y,w,h = bbox
        if frame is None or x+w > frame.shape[1] or y+h > frame.shape[0]:
            raise ValueError('Invalid image or box')
        crop = frame[y:y+h, x:x+w]
        if index == 0:
            for mode in rows:
                read_plate_crop(crop, preprocessing=mode)
        modes = list(rows)
        modes = modes[index % len(modes):] + modes[:index % len(modes)]
        for mode in modes:
            start = perf_counter()
            observation = read_plate_crop(crop, preprocessing=mode)
            rows[mode].append({'ground_truth_plate': truth, 'predicted_plate': observation['plate'],
                               'ocr_confidence': observation['confidence'], 'latency_ms': (perf_counter()-start)*1000})
        print(f'Evaluated {index+1}/{len(samples)}', file=sys.stderr, flush=True)
    print(json.dumps({'scope':'Development sample, annotated plate boxes only; not independent validation.',
        'input_hashes':[hashlib.sha256(p.read_bytes()).hexdigest() for p,_,_ in samples],
        'variants': {mode:evaluate_rows(values) for mode,values in rows.items()}}, indent=2))


if __name__ == '__main__':
    main()
