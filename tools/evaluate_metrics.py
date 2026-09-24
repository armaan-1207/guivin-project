#!/usr/bin/env python3
"""Evaluate a labeled ALPR/ACI CSV and emit JSON metrics.

Required columns for plate metrics: ground_truth_plate, predicted_plate.
Optional columns: plate_available, plate_detected, watchlist_truth,
watchlist_prediction, alert_truth, alert_prediction, ocr_confidence,
alert_score (0..1 for ROC-AUC), latency_ms (nonnegative capture-to-alert time).
Missing binary labels are excluded, never assumed negative. Each row must be an
independently labeled event/observation; this tool cannot remove frame leakage.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.evaluation import evaluate_csv

parser = argparse.ArgumentParser()
parser.add_argument('--input', required=True, help='Labeled CSV evaluation set')
args = parser.parse_args()
print(json.dumps(evaluate_csv(args.input), indent=2, sort_keys=True))
