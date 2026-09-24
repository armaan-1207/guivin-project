"""Alert persistence and normalized deduplication for the single-process demo."""
import json
import uuid
import threading
from datetime import datetime
from pathlib import Path
from .database import AlertDB, DetectionDB
from .blockchain_ledger import anchor_evidence, ledger_lock
from .watchlist_engine import normalise_plate
from .time_utils import utc_iso

_recent_alerts = {}
_alert_lock = threading.RLock()

def _is_duplicate(camera_id, plate, window_seconds=30, remember=True, scope=''):
    now = datetime.utcnow()
    key = f'{camera_id}:{normalise_plate(plate)}:{scope}'
    with _alert_lock:
        expired = [k for k, value in _recent_alerts.items() if (now - value).total_seconds() >= window_seconds]
        for k in expired:
            _recent_alerts.pop(k, None)
        if key in _recent_alerts:
            return True
        if remember:
            _recent_alerts[key] = now
        return False

def create_alert(db, camera_id, alert_type, plate_number, object_class, confidence,
                 base_risk, reason_codes, frame_path, lat=0.0, lon=0.0,
                 department='Unknown', origin='SIMULATED', plate_confidence=0.0, watchlist_source='', detection_id=None, timestamp=None, dedup_scope=''):
    plate_number = normalise_plate(plate_number)
    with _alert_lock, ledger_lock:
        if _is_duplicate(camera_id, plate_number, remember=False, scope=dedup_scope):
            return None
        risk = max(0, min(int(base_risk), 100))
        bounded_reasons = []
        remaining = risk
        for reason in reason_codes:
            delta = min(remaining, max(0, int(reason.get('delta', 0))))
            if delta:
                bounded_reasons.append(dict(reason, delta=delta))
                remaining -= delta
        if remaining:
            bounded_reasons.append({'factor':'Detection', 'delta':remaining, 'reason':'Base observation score'})
        alert = AlertDB(
            id='ALT-' + uuid.uuid4().hex[:16].upper(), camera_id=camera_id,
            alert_type=alert_type, severity='HIGH' if risk >= 70 else 'MEDIUM' if risk >= 40 else 'LOW',
            risk_score=risk, plate_number=plate_number, object_class=object_class,
            reason_codes=json.dumps(bounded_reasons), clip_path='', frame_path=frame_path,
            timestamp=timestamp or datetime.utcnow(), confidence=confidence, lat=lat, lon=lon,
            origin=origin, plate_confidence=plate_confidence, watchlist_source=watchlist_source, status='NEW')
        try:
            db.add(alert)
            db.flush()
            block = anchor_evidence(db, alert.id, camera_id, frame_path, alert_type, risk, department, commit=False)
            alert.clip_hash, alert.block_id, alert.blockchain_tx = block['clip_hash'], str(block['block_number']), block['tx_id']
            if detection_id is not None:
                detection = db.get(DetectionDB, detection_id)
                detection.alert_id, detection.frame_path = alert.id, frame_path
            db.commit()
            _is_duplicate(camera_id, plate_number, scope=dedup_scope)
        except Exception:
            db.rollback()
            raise
        return _alert_to_dict(alert)

def get_alerts(db, limit=100, severity=None, camera_id=None):
    query = db.query(AlertDB).order_by(AlertDB.timestamp.desc())
    if severity:
        query = query.filter_by(severity=severity)
    if camera_id:
        query = query.filter_by(camera_id=camera_id)
    return [_alert_to_dict(a) for a in query.limit(limit)]

def acknowledge_alert(db, alert_id, operator, action):
    alert = db.query(AlertDB).filter_by(id=alert_id).first()
    if not alert:
        return {'success': False, 'reason': 'Alert not found'}
    from .database import AuditDB, CameraDB, SupervisorReviewDB
    import uuid
    previous = alert.status
    camera = db.get(CameraDB, alert.camera_id)
    db.add(AuditDB(actor=operator, department=camera.department,
        action='ALERT_STATUS', resource=f'/api/alerts/{alert_id}',
        details=json.dumps({'before': previous, 'after': action})))
    if alert.severity == 'HIGH' and action in ('FALSE_ALARM', 'CLOSED') and action != previous:
        db.add(SupervisorReviewDB(id='REV-' + uuid.uuid4().hex, alert_id=alert_id,
            camera_id=alert.camera_id, actor=operator, action=action))
    alert.status, alert.acknowledged_by = action, operator
    db.commit()
    return {'success': True, 'alert_id': alert_id, 'status': action}

def get_vehicle_journey(db, plate_number, limit=1000, offset=0, start=None, end=None):
    query = db.query(DetectionDB).filter_by(plate_number=normalise_plate(plate_number))
    if start is not None:
        query = query.filter(DetectionDB.timestamp >= start)
    if end is not None:
        query = query.filter(DetectionDB.timestamp <= end)
    query = query.order_by(DetectionDB.timestamp, DetectionDB.id).offset(offset).limit(limit)
    return [dict(detection_id=d.id, camera_id=d.camera_id, timestamp=utc_iso(d.timestamp),
                 plate_number=d.plate_number, plate_confidence=d.plate_confidence,
                 object_class=d.object_class, confidence=d.confidence, frame_path='',
                 alert_id=d.alert_id, origin=d.origin, track_id=d.track_id,
                 raw_plate=d.raw_plate, raw_plate_confidence=d.raw_plate_confidence, plate_localization=d.plate_localization,
                 plate_rectified=d.plate_rectified) for d in query]

def record_detection(db, camera_id, object_class, confidence, plate_number='', plate_confidence=0.0,
                     bbox=(0,0,0,0), frame_path='', alert_id='', origin='SIMULATED', track_id='', raw_plate='',
                     plate_localization='', plate_rectified=False, timestamp=None, commit=True, raw_plate_confidence=None):
    det = DetectionDB(camera_id=camera_id, timestamp=timestamp or datetime.utcnow(), object_class=object_class,
                      confidence=confidence, plate_number=normalise_plate(plate_number), plate_confidence=plate_confidence,
                      bbox_x=bbox[0], bbox_y=bbox[1], bbox_w=bbox[2], bbox_h=bbox[3],
                      frame_path=frame_path, alert_id=alert_id, origin=origin, track_id=str(track_id), raw_plate=raw_plate,
                      plate_localization=plate_localization, plate_rectified=plate_rectified,
                      raw_plate_confidence=raw_plate_confidence)
    db.add(det)
    db.flush()
    if commit:
        db.commit()
    return det.id

def _alert_to_dict(a):
    data = {key: getattr(a, key) for key in ('id', 'camera_id', 'alert_type', 'severity', 'risk_score', 'plate_number',
            'object_class', 'status', 'confidence', 'lat', 'lon', 'origin', 'plate_confidence', 'watchlist_source')}
    data.update(reason_codes=json.loads(a.reason_codes or '[]'), clip_path='', frame_path='',
                clip_hash=a.clip_hash or '', block_id=a.block_id or '', blockchain_tx=a.blockchain_tx or '',
                acknowledged_by=a.acknowledged_by or '', timestamp=utc_iso(a.timestamp),
                evidence_url=f'/api/evidence/{a.id}' if a.frame_path else '',
                evidence_status='CAPTURED' if a.frame_path and Path(a.frame_path).is_file() else 'MISSING')
    return data
