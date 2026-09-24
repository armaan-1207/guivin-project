"""Directed topology checks; neither road routing nor appearance re-identification."""
from datetime import datetime
from .database import CameraLinkDB


def assess_transitions(db, sightings):
    camera_ids = {s['camera_id'] for s in sightings}
    links = {(r.source_id, r.target_id): r for r in db.query(CameraLinkDB).filter(
        CameraLinkDB.enabled == True, CameraLinkDB.source_id.in_(camera_ids),
        CameraLinkDB.target_id.in_(camera_ids))}
    previous = None
    for sighting in sightings:
        result = {'status': 'START', 'identity_confirmed': False}
        if previous:
            elapsed = (datetime.fromisoformat(sighting['timestamp']) -
                       datetime.fromisoformat(previous['timestamp'])).total_seconds()
            result.update(from_detection_id=previous['detection_id'], elapsed_seconds=elapsed)
            link = links.get((previous['camera_id'], sighting['camera_id']))
            if sighting['origin'] != 'LIVE' or previous['origin'] != 'LIVE':
                result['status'] = 'UNASSESSED_NON_LIVE'
            elif min(sighting['plate_confidence'] or 0, previous['plate_confidence'] or 0) < .7:
                result['status'] = 'UNCERTAIN_OCR'
            elif previous['camera_id'] == sighting['camera_id']:
                result['status'] = 'SAME_CAMERA'
            elif not link:
                result['status'] = 'UNKNOWN_TOPOLOGY'
            else:
                result.update(min_seconds=link.min_seconds, max_seconds=link.max_seconds,
                              topology_updated_at=link.updated_at.isoformat() + 'Z')
                result['status'] = ('TOO_FAST' if elapsed < link.min_seconds else
                                    'OUTSIDE_WINDOW' if elapsed > link.max_seconds else 'PLAUSIBLE')
        sighting['correlation'] = result
        previous = sighting
    return sightings
