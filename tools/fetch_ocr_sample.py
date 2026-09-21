"""Fetch a pinned DataCluster sample for local noncommercial evaluation only.

Retains source annotations/card; never redistributes images or downloads code.
"""
import csv
import json
import math
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET
import requests

REPO = 'Dataclusterlabspvtltd/indian-number-plates-dataset'
REVISION = '2bb7cd4e46e58af4f6fa413ce8e205ac69e605a7'
ROOT = Path(__file__).resolve().parents[1] / 'tmp' / 'ocr-evaluation' / 'datacluster'


def fetch(path):
    target = ROOT / path
    if not target.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Invalid dataset path')
    if target.is_file():
        return target.read_bytes()
    response = requests.get(f'https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{quote(path)}', timeout=60)
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return response.content


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    fetch('README.md')
    response = requests.get(f'https://huggingface.co/api/datasets/{REPO}/tree/{REVISION}?recursive=true&limit=1000', timeout=30)
    response.raise_for_status()
    entries = response.json()
    images = {Path(row['path']).stem: row['path'] for row in entries
              if row['path'].startswith('images/') and row['path'].lower().endswith(('.jpg', '.jpeg', '.png'))}
    rows = []
    selected = set()
    for entry in entries:
        if not entry['path'].lower().endswith('.xml'):
            continue
        annotation = ET.fromstring(fetch(entry['path']))
        image = images.get(Path(entry['path']).stem)
        if not image:
            continue
        for obj in annotation.findall('object'):
            labels = [attr.findtext('value', '').strip() for attr in obj.findall('./attributes/attribute')
                      if attr.findtext('name') == 'number_plate_text']
            if not labels or not labels[0]:
                continue
            box = obj.find('bndbox')
            x, y = (math.floor(float(box.findtext(key))) for key in ('xmin', 'ymin'))
            right, bottom = (math.ceil(float(box.findtext(key))) for key in ('xmax', 'ymax'))
            rows.append([image, x, y, right-x, bottom-y, labels[0]])
            selected.add(image)
    sizes = {row['path']: row.get('size', 0) for row in entries}
    if sum(sizes[path] for path in selected) > 150_000_000:
        raise ValueError('Evaluation subset exceeds 150 MB budget')
    for index, path in enumerate(sorted(selected), 1):
        fetch(path)
        print(f'Downloaded labeled image {index}/{len(selected)}', flush=True)
    with (ROOT / 'labels.csv').open('w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output)
        writer.writerow(['image', 'x', 'y', 'w', 'h', 'ground_truth_plate'])
        writer.writerows(rows)
    provenance = {'source': f'https://huggingface.co/datasets/{REPO}', 'revision': REVISION,
                  'usage': 'Local noncommercial evaluation only; no image redistribution or training.',
                  'license': 'CC BY-NC-ND 4.0; retain publisher README for additional conditions.',
                  'images': len(selected), 'labeled_boxes': len(rows), 'box_kind': 'plate',
                  'labels': 'Publisher annotations, not independently audited ground truth.'}
    (ROOT / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    print(json.dumps(provenance), flush=True)


if __name__ == '__main__':
    main()
