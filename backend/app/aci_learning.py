"""Conservative sampled scene baselines; no LSTM or directional learning claim."""
import json
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean

from fastapi import HTTPException
from .database import ACIObservationDB, ACIProfileDB, AuditDB, CameraDB
from .auth import principal
from .time_utils import IST, utc_iso
from .source_origin import is_sentinel_camera

profile_lock = threading.RLock()


def observe(db, camera_id, detections, dwell, timestamp, origin):
    # Recorded/simulated data never train a deployed camera profile.
    if origin != 'LIVE' or is_sentinel_camera(camera_id):
        return
    minute = timestamp.replace(second=0, microsecond=0)
    key = camera_id + ':' + minute.isoformat()
    if db.get(ACIObservationDB, key):
        return
    db.add(ACIObservationDB(id=key, camera_id=camera_id, timestamp=minute,
        vehicle_count=sum(bool(d.get('is_vehicle')) for d in detections),
        person_count=sum(d.get('class') == 'person' for d in detections),
        dwell_seconds=max(0, dwell), origin=origin))
    # Seven-day retention bounds observation growth in this local prototype.
    db.query(ACIObservationDB).filter(ACIObservationDB.camera_id == camera_id,
        ACIObservationDB.timestamp < minute - timedelta(days=7)).delete(synchronize_session=False)
    db.commit()


def build_candidate(db, camera_id, now=None):
    # Also reject historical sandbox observations mislabeled LIVE by older builds.
    if is_sentinel_camera(camera_id):
        raise HTTPException(409, 'Sandbox replay cannot establish a live ACI baseline')
    now = now or datetime.utcnow()
    rows = db.query(ACIObservationDB).filter(
        ACIObservationDB.camera_id == camera_id, ACIObservationDB.origin == 'LIVE',
        ACIObservationDB.timestamp >= now - timedelta(days=7),
        ACIObservationDB.timestamp <= now).order_by(ACIObservationDB.timestamp).all()
    hours = Counter(r.timestamp.replace(minute=0, second=0, microsecond=0) for r in rows)
    covered = sum(count >= 30 for count in hours.values())
    span = (rows[-1].timestamp - rows[0].timestamp).total_seconds() / 3600 if rows else 0
    if span < 72 or covered < 72:
        raise HTTPException(409, detail={'reason': 'Insufficient live observation coverage',
            'span_hours': span, 'covered_hours': covered, 'required_hours': 72,
            'minimum_samples_per_covered_hour': 30, 'samples': len(rows)})
    night = [r for r in rows if r.timestamp.replace(tzinfo=timezone.utc).astimezone(IST).hour in (22,23,0,1,2,3,4,5)]
    dwell = [r.dwell_seconds for r in rows if r.dwell_seconds > 0]
    if len(night) < 60 or len(dwell) < 100:
        raise HTTPException(409, 'Need at least 60 night samples and 100 positive dwell samples')
    features = dict(avg_dwell_seconds=mean(dwell),
        night_occupancy_rate=mean(bool(r.vehicle_count or r.person_count) for r in night),
        mean_observed_vehicles=mean(r.vehicle_count for r in rows),
        mean_observed_persons=mean(r.person_count for r in rows), samples=len(rows),
        covered_hours=covered, night_samples=len(night), dwell_samples=len(dwell),
        estimator='One processed-frame sample per UTC minute; mean positive maximum track dwell; not unique traffic volume')
    row = ACIProfileDB(id='ACI-' + uuid.uuid4().hex, camera_id=camera_id,
        features=json.dumps(features), window_start=rows[0].timestamp, window_end=rows[-1].timestamp)
    db.add(row)
    db.commit()
    return serialize(row)


def activate(db, camera_id, profile_id):
    if is_sentinel_camera(camera_id):
        raise HTTPException(409, 'Sandbox replay cannot activate a live ACI baseline')
    # Also supports explicit rollback to a previously approved version.
    with profile_lock:
        row = db.query(ACIProfileDB).filter_by(id=profile_id, camera_id=camera_id).first()
        if not row:
            raise HTTPException(404, 'Profile not found')
        camera = db.get(CameraDB, camera_id)
        actor = (principal.get() or {}).get('username', 'system')
        db.query(ACIProfileDB).filter_by(camera_id=camera_id, status='ACTIVE').update({'status':'SUPERSEDED'}, synchronize_session='fetch')
        row.status, row.approved_by, row.approved_at = 'ACTIVE', actor, datetime.utcnow()
        db.add(AuditDB(actor=actor, department=camera.department, action='ACI_APPROVE',
            resource=f'/api/aci/{camera_id}/profiles/{profile_id}',
            details=json.dumps({'profile_id': profile_id, 'operation': 'activate_or_rollback'})))
        db.commit()
        return serialize(row)


def serialize(row):
    return dict(id=row.id, camera_id=row.camera_id, status=row.status,
        features=json.loads(row.features), window_start=utc_iso(row.window_start),
        window_end=utc_iso(row.window_end), approved_by=row.approved_by,
        approved_at=utc_iso(row.approved_at))
