"""Bundle already-downloaded, hash-verified detector and local Paddle assets."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--detector-bundle', required=True)
    parser.add_argument('--paddle-root', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(args.detector_bundle).resolve()
    root = Path(args.output)
    if root.exists():
        parser.error('Output must be a new directory')
    manifest = json.loads((source / 'manifest.json').read_text())
    entry = manifest['files']['detector']
    detector = (source / entry['name']).resolve()
    if not detector.is_relative_to(source) or hashlib.sha256(detector.read_bytes()).hexdigest() != entry['sha256']:
        raise ValueError('Source detector hash or path mismatch')
    assets = [Path(args.paddle_root) / name / file for name in
              ('PP-OCRv5_mobile_det', 'en_PP-OCRv4_mobile_rec') for file in
              ('inference.json', 'inference.pdiparams', 'inference.yml')]
    if not all(path.is_file() for path in assets):
        raise ValueError('Incomplete Paddle model files')
    root.mkdir(parents=True)
    shutil.copyfile(detector, root / detector.name)
    files = {'detector': dict(entry, name=detector.name)}
    for index, path in enumerate(assets):
        relative = Path('paddle') / path.parent.name / path.name
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        files[f'paddle_{index}'] = {'name': relative.as_posix(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    manifest.update(recognizer='paddle', ocr_model='PP-OCRv5_mobile_det + en_PP-OCRv4_mobile_rec', files=files)
    manifest['sources'].append('https://github.com/PaddlePaddle/PaddleOCR')
    manifest['packages'].update(paddleocr='3.0.3', paddlepaddle='3.0.0', paddlex='3.0.3')
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Local hybrid ANPR bundle prepared')


if __name__ == '__main__':
    main()
