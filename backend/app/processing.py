"""Shared live/recorded detection handling. Watchlists are representative local data."""
import uuid
from pathlib import Path
from .database import SessionLocal
from .camera_registry import get_camera
from .watchlist_engine import check_plate
from .aci_engine import compute_aci_risk
from .alert_manager import create_alert, record_detection
from .ai_pipeline import save_frame

def process_detection(camera_id, detection, plate, plate_confidence, frame, origin='LIVE',
                      track_id='', dwell_seconds=0, timestamp=None, raw_plate=None,
                      plate_localization='', plate_rectified=False, raw_plate_confidence=None):
    with SessionLocal() as db:
        camera = get_camera(db, camera_id)
        if not camera:
            return None
        match = check_plate(db, plate) if plate else None
        aci = compute_aci_risk(camera_id, detection['class'], dwell_seconds, db)
        factors = []
        risk = 0
        if match:
            risk = 70 if match['reason'] in ('STOLEN', 'WANTED') else 55
            factors.append({'factor': 'Representative watchlist match', 'delta': risk,
                            'reason': match['reason'] + ' / ' + match['source_db']})
        remaining = 50
        for factor in aci['factors']:
            delta = min(remaining, factor['delta'])
            if delta:
                factors.append(dict(factor, delta=delta, baseline_version=aci['baseline_version']))
                risk += delta
                remaining -= delta
        detection_id = record_detection(db, camera_id, detection['class'], detection['confidence'], plate,
            plate_confidence, detection['bbox'], origin=origin, track_id=track_id,
            raw_plate=plate if raw_plate is None else raw_plate,
            plate_localization=plate_localization, plate_rectified=plate_rectified,
            raw_plate_confidence=raw_plate_confidence,
            timestamp=timestamp, commit=False)
        alert = None
        evidence = ''
        if match or aci['is_anomaly']:
            evidence = save_frame(frame, 'EVT-' + uuid.uuid4().hex)
            try:
                alert = create_alert(db, camera_id, 'WATCHLIST_MATCH' if match else 'ACI_ANOMALY',
                    plate, detection['class'], detection['confidence'], risk, factors, evidence,
                    camera['lat'], camera['lon'], camera['department'], origin, plate_confidence,
                    match['source_db'] if match else '', detection_id=detection_id, timestamp=timestamp)
            except Exception:
                Path(evidence).unlink(missing_ok=True)
                raise
            if not alert:
                Path(evidence).unlink(missing_ok=True)
                evidence = ''
        if not alert:
            db.commit()
        return {'event': 'new_alert', 'alert': alert, 'department': camera['department']} if alert else None
