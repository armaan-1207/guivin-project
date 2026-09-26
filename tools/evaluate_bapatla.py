#!/usr/bin/env python3
"""
Bapatla Indian License Plate Dataset Benchmark
Evaluates PaddleOCR on labeled Bapatla images; this is not the live ANPR pipeline.
"""
import argparse
import io
import json
import logging
import math
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import cv2
import numpy as np

# Ensure root & backend imports work
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'tools'))

# Configure safe cache locations before any paddle/ultralytics import
os.environ.setdefault('HOME', '/tmp')
os.environ.setdefault('PADDLE_PDX_CACHE_HOME', '/tmp/.paddlex')
os.environ.setdefault('PADDLE_HOME', '/tmp/.paddle')
os.environ.setdefault('PADDLEX_HOME', '/tmp/.paddlex')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('evaluate_bapatla')


def clean_plate(text: str) -> str:
    """Normalize plate text: uppercase, remove spaces, dashes, and non-alphanumeric chars."""
    if not text:
        return ""
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def normalize_key(name: str) -> str:
    """Normalize file stem: 'y (10).jpg' -> 'Y10', 'C (1).png' -> 'C1', 'AN1.png' -> 'AN1'."""
    base = Path(name).name.split('.')[0]
    m = re.match(r'([A-Za-z]+)\s*\(?(\d+)\)?', base)
    if m:
        return f'{m.group(1).upper()}{m.group(2)}'
    return base.upper()


def parse_ground_truth_from_zip(zip_obj: zipfile.ZipFile) -> dict:
    """Parse number_plate.xlsx from zip using standard xml.etree."""
    candidates = [f for f in zip_obj.namelist() if f.endswith('.xlsx')]
    if not candidates:
        raise FileNotFoundError("No .xlsx file found inside dataset zip")
    
    excel_name = candidates[0]
    logger.info(f"Parsing ground truth spreadsheet: {excel_name}")
    xl_z = zipfile.ZipFile(io.BytesIO(zip_obj.read(excel_name)))
    
    # 1. Read shared strings
    shared = []
    if 'xl/sharedStrings.xml' in xl_z.namelist():
        tree = ET.fromstring(xl_z.read('xl/sharedStrings.xml'))
        ns = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        for si in tree.findall('main:si', ns):
            t = si.find('main:t', ns)
            shared.append(t.text if t is not None and t.text else '')

    # 2. Read sheet1 rows
    sheet_candidates = [f for f in xl_z.namelist() if f.startswith('xl/worksheets/sheet')]
    if not sheet_candidates:
        raise ValueError("No worksheet found in excel file")
    
    sheet_tree = ET.fromstring(xl_z.read(sheet_candidates[0]))
    ns = {'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    rows = []
    for row in sheet_tree.findall('.//main:row', ns):
        cells = []
        for c in row.findall('main:c', ns):
            cell_type = c.get('t')
            v = c.find('main:v', ns)
            if v is not None and v.text:
                if cell_type == 's':
                    idx = int(v.text)
                    cells.append(shared[idx] if idx < len(shared) else v.text)
                else:
                    cells.append(v.text)
            else:
                cells.append('')
        if cells:
            rows.append(cells)

    mapping = {}
    for r in rows[1:]:  # skip header
        if len(r) >= 2 and r[0] and r[1]:
            key = normalize_key(r[0])
            gt = clean_plate(r[1])
            if gt:
                mapping[key] = gt

    logger.info(f"Loaded {len(mapping)} ground truth records from spreadsheet")
    return mapping


def enhance_plate_clahe(crop: np.ndarray) -> np.ndarray:
    """Apply CLAHE enhancement on plate crop."""
    if crop is None or crop.size == 0:
        return crop
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def bounded_crop(crop: np.ndarray) -> np.ndarray:
    """Preserve aspect ratio & scale up small crops for OCR."""
    if crop is None or crop.size == 0:
        return crop
    height, width = crop.shape[:2]
    factor = min(480 / width, 256 / height)
    resized = cv2.resize(crop, (max(1, round(width * factor)), max(1, round(height * factor))),
                         interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
    return cv2.copyMakeBorder(resized, 12, 12, 12, 12, cv2.BORDER_REPLICATE)


def init_paddle_engine(model_root: Path):
    """Initialize PaddleOCR engine."""
    from tools.compare_paddleocr import create_engine
    logger.info(f"Loading PaddleOCR with models from {model_root}")
    return create_engine(str(model_root))


def run_paddle_prediction(engine, crop: np.ndarray) -> tuple:
    """Run PaddleOCR on crop, returning raw recognized text and syntax-gated plate."""
    from compare_paddleocr import observation
    t0 = time.perf_counter()
    results = list(engine.predict(crop))
    latency = (time.perf_counter() - t0) * 1000.0
    if not results or not results[0].get('rec_texts'):
        return "", "", 0.0, latency
    
    # Raw OCR text: join alphanumeric characters from all detected text blocks
    raw_texts = [clean_plate(t) for t in results[0]['rec_texts'] if clean_plate(t)]
    # Pick the longest or best matching block, or join multi-line
    raw_combined = "".join(raw_texts)
    
    # Syntax-gated observation
    try:
        obs = observation(results[0])
        gated_plate = clean_plate(obs['plate'])
        confidence = obs['confidence']
    except Exception:
        gated_plate = ""
        confidence = 0.0

    return raw_combined, gated_plate, confidence, latency


def main():
    parser = argparse.ArgumentParser(description="Evaluate OCR on Bapatla dataset")
    parser.add_argument("--zip", help="Path to Bapatla zip file (defaults to auto-detect in tools/)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of images to evaluate")
    parser.add_argument("--model-root", help="Path to official_models directory for PaddleOCR")
    parser.add_argument("--output", default="tmp/bapatla_benchmark_report.json", help="Path to output JSON report")
    args = parser.parse_args()

    # 1. Locate zip
    zip_path = None
    if args.zip:
        zip_path = Path(args.zip)
    else:
        for candidate in [
            REPO_ROOT / "tools" / "number_plate.zip",
            REPO_ROOT / "tools" / "Bapatla_Number_Plates.zip",
            Path("/app/tools/number_plate.zip"),
            Path("/app/tools/Bapatla_Number_Plates.zip"),
        ]:
            if candidate.is_file():
                zip_path = candidate
                break

    if not zip_path or not zip_path.is_file():
        logger.error("Could not find dataset zip file! Please pass --zip path/to/dataset.zip")
        sys.exit(1)

    logger.info(f"Using dataset zip: {zip_path}")
    dataset_zip = zipfile.ZipFile(zip_path)
    gt_mapping = parse_ground_truth_from_zip(dataset_zip)

    # 2. Locate Paddle models
    model_root = None
    if args.model_root:
        model_root = Path(args.model_root)
    else:
        for cand in [
            Path("/host_tmp/paddle-evaluation/.paddlex/official_models"),
            REPO_ROOT / "tmp" / "paddle-evaluation" / ".paddlex" / "official_models",
            Path("/app/tmp/paddle-evaluation/.paddlex/official_models"),
            Path("/cache/.paddlex/official_models"),
        ]:
            if cand.is_dir() and (cand / "en_PP-OCRv4_mobile_rec").is_dir():
                model_root = cand
                break

    paddle_engine = None
    if model_root and model_root.is_dir():
        try:
            paddle_engine = init_paddle_engine(model_root)
            logger.info("PaddleOCR engine initialized successfully.")
        except Exception as e:
            logger.warning(f"PaddleOCR failed to load: {e}")
    else:
        logger.warning(f"Model root not found or incomplete: {model_root}")

    if paddle_engine is None:
        raise SystemExit("PaddleOCR unavailable; prepare models before evaluation")

    # 3. Match image files in zip
    image_entries = {}
    for name in dataset_zip.namelist():
        if name.lower().endswith(('.png', '.jpg', '.jpeg')):
            key = normalize_key(name)
            if key in gt_mapping:
                image_entries[key] = (name, gt_mapping[key])

    logger.info(f"Found {len(image_entries)} images with matching ground truth.")
    all_keys = sorted(list(image_entries.keys()))
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.limit:
        all_keys = all_keys[:args.limit]
        logger.info(f"Limiting benchmark to {len(all_keys)} images.")

    if not all_keys:
        raise SystemExit("No labeled images matched; no report generated")

    # 4. Run Benchmark
    paddle_results = []
    skipped_images = 0
    latencies = []
    
    # Raw OCR metrics
    raw_exact_matches = 0
    raw_one_char_matches = 0
    total_raw_cer_dist = 0
    total_gt_len = 0

    # Syntax-gated plate metrics
    gated_exact_matches = 0
    gated_valid_reads = 0

    logger.info("Starting OCR evaluation loop...")
    for idx, key in enumerate(all_keys):
        entry_name, gt = image_entries[key]
        raw_bytes = dataset_zip.read(entry_name)
        nparr = np.frombuffer(raw_bytes, np.uint8)
        try:
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        except cv2.error:
            img = None

        if img is None:
            skipped_images += 1
            continue

        raw_pred, gated_pred, conf, lat = run_paddle_prediction(paddle_engine, img)
        latencies.append(lat)

        dist = levenshtein_distance(raw_pred, gt)
        total_raw_cer_dist += dist
        total_gt_len += len(gt)

        is_raw_exact = raw_pred == gt
        is_raw_one_char = (dist <= 1)
        is_gated_exact = (gated_pred == gt)

        if is_raw_exact:
            raw_exact_matches += 1
        if is_raw_one_char:
            raw_one_char_matches += 1
        if is_gated_exact:
            gated_exact_matches += 1
        if gated_pred:
            gated_valid_reads += 1

        paddle_results.append({
            'key': key,
            'file': entry_name,
            'ground_truth': gt,
            'raw_predicted': raw_pred,
            'gated_predicted': gated_pred,
            'confidence': conf,
            'raw_exact': is_raw_exact,
            'edit_distance': dist,
            'latency_ms': round(lat, 2)
        })

        if (idx + 1) % 50 == 0 or (idx + 1) == len(all_keys):
            curr_exact = (raw_exact_matches / len(paddle_results)) * 100
            curr_one_char = (raw_one_char_matches / len(paddle_results)) * 100
            avg_cer = (total_raw_cer_dist / total_gt_len) * 100 if total_gt_len else 0
            logger.info(f"[{idx+1}/{len(all_keys)}] Raw Exact: {curr_exact:.1f}% | 1-Char Tol: {curr_one_char:.1f}% | CER: {avg_cer:.1f}% | Median Latency: {np.median(latencies):.1f}ms")

    # 5. Summarize Metrics
    n = len(paddle_results)
    if not n:
        raise SystemExit("No images could be decoded; no report generated")
    raw_exact_rate = (raw_exact_matches / n) * 100 if n else 0.0
    raw_one_char_rate = (raw_one_char_matches / n) * 100 if n else 0.0
    overall_cer = (total_raw_cer_dist / total_gt_len) * 100 if total_gt_len else 0.0
    raw_char_acc = max(0.0, 100.0 - overall_cer)
    gated_exact_rate = (gated_exact_matches / n) * 100 if n else 0.0
    gated_yield_rate = (gated_valid_reads / n) * 100 if n else 0.0

    report = {
        'dataset': 'Bapatla Indian License Plate Dataset',
        'license': 'CC BY 4.0 (Zenodo 13954136)',
        'samples_evaluated': n,
        'samples_selected': len(all_keys),
        'images_skipped': skipped_images,
        'scope': 'PaddleOCR on dataset images; excludes vehicle/plate detection and temporal voting',
        'model_name': 'PP-OCRv5_mobile_det + en_PP-OCRv4_mobile_rec',
        'metrics': {
            'raw_ocr': {
                'exact_match_percent': round(raw_exact_rate, 2),
                'one_char_tolerance_percent': round(raw_one_char_rate, 2),
                'character_accuracy_percent': round(raw_char_acc, 2),
                'character_error_rate_percent': round(overall_cer, 2),
            },
            'syntax_gated_anpr': {
                'exact_match_percent': round(gated_exact_rate, 2),
                'valid_plate_yield_percent': round(gated_yield_rate, 2),
            }
        },
        'latency_ms': {
            'median': round(float(np.median(latencies)), 2) if latencies else 0,
            'p90': round(float(np.percentile(latencies, 90)), 2) if latencies else 0,
            'p95': round(float(np.percentile(latencies, 95)), 2) if latencies else 0,
            'min': round(float(np.min(latencies)), 2) if latencies else 0,
            'max': round(float(np.max(latencies)), 2) if latencies else 0,
        },
        'sample_predictions': paddle_results[:30]
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    logger.info(f"Saved benchmark report to {out_path.resolve()}")

    print("\n=======================================================")
    print("      GUIVIN BAPATLA ANPR BENCHMARK REPORT             ")
    print("=======================================================")
    print(f" Total Samples Evaluated:      {n}")
    print(f" Raw OCR Exact Match:          {raw_exact_rate:.2f}%")
    print(f" Raw OCR 1-Char Tolerance:     {raw_one_char_rate:.2f}%")
    print(f" Character-Level Accuracy:     {raw_char_acc:.2f}%")
    print(f" Strict Syntax-Gated Match:    {gated_exact_rate:.2f}%")
    print(f" Median Latency per Crop:      {np.median(latencies):.1f} ms")
    print(f" 95th Percentile Latency:      {np.percentile(latencies, 95):.1f} ms")
    print("=======================================================\n")


if __name__ == '__main__':
    main()
