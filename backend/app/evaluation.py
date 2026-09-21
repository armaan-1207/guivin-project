"""Reproducible metrics for held-out ALPR and ACI evaluation sets.

The evaluator accepts one row per labeled observation or event. It deliberately
does not infer ground truth from the application's own watchlist or alerts.
"""
import csv
import math
from pathlib import Path

from .watchlist_engine import normalise_plate


def _truthy(value):
    text = str(value).strip().upper()
    if text in {'1', 'TRUE', 'YES', 'Y', 'MATCH', 'ANOMALY'}:
        return True
    if text in {'0', 'FALSE', 'NO', 'N', 'NORMAL', 'NO_MATCH'}:
        return False
    raise ValueError('Binary labels must explicitly specify yes/no or true/false')


def _present(row, key):
    return row.get(key) is not None and str(row[key]).strip() != ''


def _number(value, low=0, high=None):
    number = float(value)
    if not math.isfinite(number) or number < low or (high is not None and number > high):
        raise ValueError('Metric values must be finite and within their declared bounds')
    return number


def _auc(rows):
    pairs = sorted((_number(r['alert_score'],high=1), _truthy(r['alert_truth']))
        for r in rows if _present(r,'alert_score') and _present(r,'alert_truth'))
    positives = sum(truth for _, truth in pairs)
    negatives = len(pairs) - positives
    if not positives or not negatives:
        return {'samples':len(pairs),'roc_auc':None}
    rank_sum, index = 0, 0
    while index < len(pairs):
        end = index + 1
        while end < len(pairs) and pairs[end][0] == pairs[index][0]:
            end += 1
        rank_sum += ((index + 1 + end) / 2) * sum(truth for _, truth in pairs[index:end])
        index = end
    return {'samples':len(pairs),'roc_auc':(rank_sum - positives * (positives + 1) / 2) / (positives * negatives)}


def _edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


def _f1(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return precision, recall, 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _binary_metrics(rows, truth_key, prediction_key):
    rows = [r for r in rows if _present(r,truth_key) and _present(r,prediction_key)]
    tp = fp = fn = tn = 0
    for row in rows:
        truth, prediction = _truthy(row.get(truth_key)), _truthy(row.get(prediction_key))
        if truth and prediction:
            tp += 1
        elif not truth and prediction:
            fp += 1
        elif truth and not prediction:
            fn += 1
        else:
            tn += 1
    precision, recall, f1 = _f1(tp, fp, fn)
    return {'samples':len(rows), 'true_positive': tp, 'false_positive': fp, 'false_negative': fn, 'true_negative': tn,
            'precision': precision if rows else None, 'recall': recall if rows else None, 'f1': f1 if rows else None,
            'false_positive_rate': fp / (fp + tn) if fp + tn else None}


def evaluate_rows(rows):
    rows = list(rows)
    labeled = [row for row in rows if normalise_plate(row.get('ground_truth_plate', ''))]
    exact = 0
    character_correct = character_total = 0
    for row in labeled:
        truth = normalise_plate(row.get('ground_truth_plate', ''))
        prediction = normalise_plate(row.get('predicted_plate', ''))
        exact += truth == prediction
        character_correct += max(0, len(truth) - _edit_distance(truth, prediction))
        character_total += len(truth)
    result = {
        'samples': len(rows),
        'labeled_plate_samples': len(labeled),
        'recognized_plate_samples': sum(bool(normalise_plate(row.get('predicted_plate', ''))) for row in labeled),
        'exact_plate_accuracy': exact / len(labeled) if labeled else None,
        'character_accuracy': character_correct / character_total if character_total else None,
        'plate_detection_recall': _binary_metrics(rows, 'plate_available', 'plate_detected')['recall'],
        'watchlist': _binary_metrics(rows, 'watchlist_truth', 'watchlist_prediction'),
        'alerts': _binary_metrics(rows, 'alert_truth', 'alert_prediction'),
    }
    confidences = [_number(row['ocr_confidence'], high=1) for row in rows if _present(row,'ocr_confidence')]
    result['mean_ocr_confidence'] = sum(confidences) / len(confidences) if confidences else None
    result['alert_score_evaluation'] = _auc(rows)
    latencies = sorted(_number(row['latency_ms']) for row in rows if _present(row,'latency_ms'))
    result['latency_ms'] = {'samples':len(latencies), **{name:latencies[max(0,math.ceil(p*len(latencies))-1)] if latencies else None
        for name,p in [('p50',.5),('p95',.95),('p99',.99)]}}
    return result


def evaluate_csv(path):
    with Path(path).open(newline='', encoding='utf-8-sig') as source:
        return evaluate_rows(csv.DictReader(source))
