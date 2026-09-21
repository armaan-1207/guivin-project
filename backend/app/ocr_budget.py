"""Bound OCR work using source time and least-recently-attempted tracks."""
import math


def select_ocr_tracks(tracks, visible_ids, source_seconds, limit=3, interval=.5):
    if limit < 1 or interval < 0 or not math.isfinite(source_seconds):
        raise ValueError('Invalid OCR budget')
    candidates = []
    for track_id in dict.fromkeys(visible_ids):
        if not track_id:
            continue
        track = tracks[track_id]
        last = track.get('ocr_attempt_at')
        if last is None or source_seconds < last or source_seconds - last >= interval:
            candidates.append((float('-inf') if last is None else last, track_id))
    candidates.sort()
    return {track_id for _, track_id in candidates[:limit]}
