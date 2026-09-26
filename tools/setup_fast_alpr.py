"""Explicitly fetch pretrained assets; never called from application startup."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--large', action='store_true', help='YOLOv9-S-608 and CCT-S-v2')
    args = parser.parse_args()
    root = Path(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    from open_image_models.detection.core.hub import download_model as detector_download
    from fast_plate_ocr.inference.hub import download_model as ocr_download
    detector_name = 'yolo-v9-s-608-license-plate-end2end' if args.large else 'yolo-v9-t-384-license-plate-end2end'
    ocr_name = 'cct-s-v2-global-model' if args.large else 'cct-xs-v2-global-model'
    detector = detector_download(detector_name, save_directory=root)
    ocr, config = ocr_download(ocr_name, save_directory=root)
    manifest = {
        'detector_model': detector_name,
        'ocr_model': ocr_name,
        'sources': ['https://github.com/ankandrew/open-image-models',
                    'https://github.com/ankandrew/fast-plate-ocr'],
        'packages': {name: importlib.metadata.version(name) for name in
                     ('fast-alpr', 'fast-plate-ocr', 'open-image-models', 'onnxruntime')},
        'files': {key: {'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                  for key, path in [('detector', detector), ('ocr', ocr), ('config', config)]},
    }
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('FastALPR local model manifest saved. Accuracy has not been validated.')


if __name__ == '__main__':
    main()
