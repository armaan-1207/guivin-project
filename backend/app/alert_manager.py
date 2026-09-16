"""
GUIVIN — Alert Manager
Creates, stores, and broadcasts alerts. Integrates with blockchain anchoring.
"""
import uuid
import json
from datetime import datetime, timedelta
from typing import List, Optional
from sqlalchemy.orm import Session

from .database import AlertDB, DetectionDB
from .blockchain_ledger import anchor_evidence
from .config import EVIDENCE_DIR


# ── Recent alert dedup cache (in-memory) ─────────────────────────────────────
_recent_alerts: dict = {}   # camera_id:plate → last_alert_time


def _is_duplicate(camera_id: str, plate: str, window_seconds: int = 30) -> bool:
    key = f"{camera_id}:{plate}"
    last = _recent_alerts.get(key)
    if last and (datetime.utcnow() - last).seconds < window_seconds:
        return True
    _recent_alerts[key] = datetime.utcnow()
    return False


def create_alert(db: Session, camera_id: str, alert_type: str,
                 plate_number: str, object_class: str,
                 confidence: float, base_risk: int,
                 reason_codes: list, frame_path: str,
                 lat: float = 0.0, lon: float = 0.0,
                 department: str = "Unknown") -> Optional[dict]:
    """
    Create a new alert, anchor to blockchain, return alert dict.
    Returns None if duplicate suppressed.
    """
    if _is_duplicate(camera_id, plate_number):
        return None

    alert_id = f"ALT-{uuid.uuid4().hex[:8].upper()}"
    clip_path = str(EVIDENCE_DIR / "clips" / f"{alert_id}.mp4")

    # Compute final risk score
    risk_score = min(base_risk, 100)
    severity = "HIGH" if risk_score >= 70 else ("MEDIUM" if risk_score >= 40 else "LOW")

    # Save alert to DB first
    alert = AlertDB(
        id=alert_id,
        camera_id=camera_id,
        alert_type=alert_type,
        severity=severity,
        risk_score=risk_score,
        plate_number=plate_number,
        object_class=object_class,
        reason_codes=json.dumps(reason_codes),
        clip_path=clip_path,
        frame_path=frame_path,
        status="NEW",
        timestamp=datetime.utcnow(),
        confidence=confidence,
        lat=lat,
        lon=lon,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    # Anchor to blockchain
    try:
        block = anchor_evidence(
            db=db,
            alert_id=alert_id,
            camera_id=camera_id,
            clip_path=clip_path,
            event_type=alert_type,
            risk_score=risk_score,
            department=department,
        )
        alert.clip_hash = block["clip_hash"]
        alert.block_id = str(block["block_number"])
        alert.blockchain_tx = block["tx_id"]
        db.commit()
    except Exception as e:
        print(f"[AlertManager] Blockchain anchor failed: {e}")

    return _alert_to_dict(alert)


def get_alerts(db: Session, limit: int = 100, severity: str = None,
               camera_id: str = None) -> List[dict]:
    query = db.query(AlertDB).order_by(AlertDB.timestamp.desc())
    if severity:
        query = query.filter(AlertDB.severity == severity)
    if camera_id:
        query = query.filter(AlertDB.camera_id == camera_id)
    return [_alert_to_dict(a) for a in query.limit(limit).all()]


def acknowledge_alert(db: Session, alert_id: str, operator: str, action: str) -> dict:
    alert = db.query(AlertDB).filter(AlertDB.id == alert_id).first()
    if not alert:
        return {"success": False, "reason": "Alert not found"}
    alert.status = action   # ACKNOWLEDGED / VERIFIED / FALSE_ALARM
    alert.acknowledged_by = operator
    db.commit()
    return {"success": True, "alert_id": alert_id, "status": action}


def get_vehicle_journey(db: Session, plate_number: str) -> List[dict]:
    """
    Return all detections of a given plate across cameras, ordered by time.
    Used to reconstruct cross-camera vehicle journey.
    """
    from .watchlist_engine import normalise_plate
    plate = normalise_plate(plate_number)

    detections = (
        db.query(DetectionDB)
        .filter(DetectionDB.plate_number != "")
        .order_by(DetectionDB.timestamp.asc())
        .all()
    )

    results = []
    for d in detections:
        db_plate = normalise_plate(d.plate_number) if d.plate_number else ""
        if db_plate == plate or db_plate.replace('-', '') == plate.replace('-', ''):
            results.append({
                "detection_id": d.id,
                "camera_id": d.camera_id,
                "timestamp": d.timestamp.isoformat(),
                "plate_number": d.plate_number,
                "plate_confidence": d.plate_confidence,
                "object_class": d.object_class,
                "confidence": d.confidence,
                "frame_path": d.frame_path,
                "alert_id": d.alert_id,
            })
    return results


def record_detection(db: Session, camera_id: str, object_class: str,
                     confidence: float, plate_number: str = "",
                     plate_confidence: float = 0.0,
                     bbox: tuple = (0, 0, 0, 0),
                     frame_path: str = "", alert_id: str = "") -> int:
    """Persist a single detection to DB for journey reconstruction."""
    det = DetectionDB(
        camera_id=camera_id,
        timestamp=datetime.utcnow(),
        object_class=object_class,
        confidence=confidence,
        plate_number=plate_number,
        plate_confidence=plate_confidence,
        bbox_x=bbox[0], bbox_y=bbox[1],
        bbox_w=bbox[2], bbox_h=bbox[3],
        frame_path=frame_path,
        alert_id=alert_id,
    )
    db.add(det)
    db.commit()
    return det.id


def _alert_to_dict(a: AlertDB) -> dict:
    reasons = []
    try:
        reasons = json.loads(a.reason_codes) if a.reason_codes else []
    except Exception:
        reasons = []
    return {
        "id": a.id,
        "camera_id": a.camera_id,
        "alert_type": a.alert_type,
        "severity": a.severity,
        "risk_score": a.risk_score,
        "plate_number": a.plate_number,
        "object_class": a.object_class,
        "reason_codes": reasons,
        "clip_path": a.clip_path,
        "frame_path": a.frame_path,
        "clip_hash": a.clip_hash or "",
        "block_id": a.block_id or "",
        "blockchain_tx": a.blockchain_tx or "",
        "status": a.status,
        "acknowledged_by": a.acknowledged_by or "",
        "timestamp": a.timestamp.isoformat() if a.timestamp else "",
        "confidence": a.confidence,
        "lat": a.lat,
        "lon": a.lon,
    }
