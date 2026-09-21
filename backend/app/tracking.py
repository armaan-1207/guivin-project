"""Within-camera two-threshold association.

This is a small dependency-free ByteTrack-style association layer for the
prototype. It uses high-confidence detections first, then associates lower
confidence detections to unmatched tracks. It does not perform re-identification
or claim identity across cameras.
"""
from collections import deque, Counter


def plate_consensus(track, plate, confidence):
    """Vote over the last five observations, including unreadable frames.

    Confidence is the mean of supporting readings, never another plate's score.
    A strict majority and two readings are required; ties remain unconfirmed.
    """
    votes = track['votes']
    votes.append((plate if plate and confidence >= .5 else '', confidence))
    counts = Counter(p for p, _ in votes if p)
    if not counts:
        return '', 0.0
    winner, count = counts.most_common(1)[0]
    if count < 2 or count * 2 <= len(votes):
        return '', 0.0
    return winner, sum(c for p, c in votes if p == winner) / count


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = max(1, aw * ah + bw * bh - intersection)
    return intersection / union


class ByteTrackLite:
    """Greedy two-stage IoU association with explicit confidence thresholds."""

    def __init__(self, high_threshold=.5, low_threshold=.1,
                 match_threshold=.3, max_age_seconds=3):
        if not 0 <= low_threshold <= high_threshold <= 1:
            raise ValueError('Tracker thresholds must satisfy 0 <= low <= high <= 1')
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.match_threshold = match_threshold
        self.max_age_seconds = max_age_seconds
        self.tracks = {}
        self.next_id = 0

    def _new_track(self, bbox, now):
        self.next_id += 1
        track_id = str(self.next_id)
        self.tracks[track_id] = {
            'bbox': bbox, 'first': now, 'last': now, 'hits': 1,
            'votes': deque(maxlen=5),
        }
        return track_id

    def _match(self, detection_indices, track_ids, detections):
        pairs = sorted(
            (_iou(detections[index]['bbox'], self.tracks[track_id]['bbox']), index, track_id)
            for index in detection_indices for track_id in track_ids
        )[::-1]
        assignments = {}
        used_detections, used_tracks = set(), set()
        for score, index, track_id in pairs:
            if score < self.match_threshold or index in used_detections or track_id in used_tracks:
                continue
            assignments[index] = track_id
            used_detections.add(index)
            used_tracks.add(track_id)
        return assignments

    def update(self, detections, now):
        """Return one track ID per detection and update track state."""
        self.tracks = {
            track_id: track for track_id, track in self.tracks.items()
            if now - track['last'] < self.max_age_seconds
        }
        high = [i for i, d in enumerate(detections) if d.get('confidence', 0) >= self.high_threshold]
        low = [i for i, d in enumerate(detections)
               if self.low_threshold <= d.get('confidence', 0) < self.high_threshold]
        active_ids = list(self.tracks)
        assignments = self._match(high, active_ids, detections)
        remaining_tracks = [track_id for track_id in active_ids if track_id not in assignments.values()]
        assignments.update(self._match(low, remaining_tracks, detections))

        for index in high + low:
            if index not in assignments:
                assignments[index] = self._new_track(detections[index]['bbox'], now)
            track = self.tracks[assignments[index]]
            track.update(bbox=detections[index]['bbox'], last=now, hits=track['hits'] + 1)

        return [assignments.get(index, '') for index in range(len(detections))]
