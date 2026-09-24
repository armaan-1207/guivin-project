"""Geometry-based OCR assembly without guessing missing registration characters."""
import math
import re


def assemble_plate(results):
    parts = []
    for polygon, text, confidence in results:
        clean = re.sub('[^A-Z0-9]', '', text.upper())
        confidence = float(confidence)
        if not clean or clean == 'IND' or not math.isfinite(confidence) or not .3 <= confidence <= 1:
            continue
        points = [(float(p[0]), float(p[1])) for p in polygon]
        if not points or not all(math.isfinite(v) for p in points for v in p):
            continue
        x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
        y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
        if x1 <= x0 or y1 <= y0:
            continue
        parts.append(dict(text=clean, confidence=confidence, x=x0, right=x1,
                          y=(y0+y1)/2, height=y1-y0))
    lines = []
    for part in sorted(parts, key=lambda p: p['y']):
        line = next((row for row in lines if abs(row[0]['y']-part['y']) <=
                     .55 * max(row[0]['height'], part['height'])), None)
        if line is None:
            lines.append([part])
        else:
            line.append(part)
    ordered = [p for line in lines for p in sorted(line, key=lambda p: p['x'])]
    # Only join plausible whole registrations; do not join arbitrary signage.
    combined = ''.join(p['text'] for p in ordered)
    conventional = r'(?:[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}|[0-9]{2}BH[0-9]{4}[A-Z]{1,2})'
    compact = all(all(b['x']-a['right'] <= 3*max(a['height'], b['height'])
                      for a,b in zip(sorted(row,key=lambda p:p['x']), sorted(row,key=lambda p:p['x'])[1:]))
                  for row in lines)
    compact = compact and all(b[0]['y']-a[0]['y'] <= 2*max(a[0]['height'], b[0]['height'])
                              for a,b in zip(lines, lines[1:]))
    if ordered and len(lines) <= 3 and compact and re.fullmatch(conventional, combined):
        return combined, min(p['confidence'] for p in ordered)
    valid = [p for p in parts if 5 <= len(p['text']) <= 12 and sum(c.isdigit() for c in p['text']) >= 2]
    best = max(valid, key=lambda p:p['confidence'], default=None)
    return (best['text'], best['confidence']) if best else ('', 0.0)
