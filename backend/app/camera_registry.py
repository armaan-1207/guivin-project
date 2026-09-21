"""
GUIVIN — Camera Registry Module
Handles onboarding, CRUD, and GIS metadata for all cameras.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session

from .database import CameraDB, ACIBaselineDB, AuditDB
from .auth import principal
from .config import DATA_DIR
from .time_utils import utc_iso


def seed_cameras_if_empty(db: Session):
    """Load cameras.json into DB on first run."""
    if db.query(CameraDB).count() == 0:
        cam_file = DATA_DIR / "cameras.json"
        if cam_file.exists():
            cameras = json.loads(cam_file.read_text(encoding="utf-8"))
            for c in cameras:
                db_cam = CameraDB(**c)
                db.add(db_cam)
            db.commit()
            print(f"[CameraRegistry] Seeded {len(cameras)} cameras from cameras.json")

    # Repair only exact mojibake copies of canonical seed names, preserving user edits.
    seed_file = DATA_DIR / 'cameras.json'
    if seed_file.exists():
        for seed in json.loads(seed_file.read_text(encoding='utf-8')):
            camera = db.get(CameraDB, seed['id'])
            try:
                corrupted = seed['name'].encode('utf-8').decode('cp1252')
            except UnicodeError:
                continue
            if camera and camera.name == corrupted and corrupted != seed['name']:
                camera.name = seed['name']
        db.commit()

    # Also seed ACI baselines
    if db.query(ACIBaselineDB).count() == 0:
        baseline_file = DATA_DIR / "aci_baselines.json"
        if baseline_file.exists():
            baselines = json.loads(baseline_file.read_text(encoding="utf-8"))
            for b in baselines:
                peak_str = json.dumps(b.get("peak_hours", []))
                db_bl = ACIBaselineDB(
                    camera_id=b["camera_id"],
                    avg_vehicles_per_hour=b.get("avg_vehicles_per_hour", 0),
                    avg_persons_per_hour=b.get("avg_persons_per_hour", 0),
                    dominant_direction=b.get("dominant_direction", "bidirectional"),
                    avg_dwell_seconds=b.get("avg_dwell_seconds", 5.0),
                    peak_hours=peak_str,
                    night_occupancy_rate=b.get("night_occupancy_rate", 0.05),
                    validated=b.get("validated", True),
                )
                db.add(db_bl)
            db.commit()
            print(f"[CameraRegistry] Seeded {len(baselines)} ACI baselines")


def get_all_cameras(db: Session) -> List[dict]:
    cameras = db.query(CameraDB).filter(CameraDB.archived == False).all()
    return [_cam_to_dict(c) for c in cameras]


def get_camera(db: Session, camera_id: str) -> Optional[dict]:
    cam = db.query(CameraDB).filter(CameraDB.id == camera_id, CameraDB.archived == False).first()
    return _cam_to_dict(cam) if cam else None


AUDIT_FIELDS = frozenset({
    'id', 'name', 'department', 'sub_type', 'district', 'lat', 'lon', 'location_known', 'address',
    'vendor', 'model_name', 'camera_type', 'resolution', 'ir_capable', 'protocol',
    'anpr_capable', 'face_capable', 'night_capable', 'ownership', 'storage_details',
    'maintenance_status', 'coverage_radius_m', 'onvif_profile', 'codec',
    'metadata_streaming', 'motion_tamper_events', 'https_streaming', 'ptz_capable',
    'conformance_status', 'source_freshness_seconds', 'archived',
})


def add_camera(db: Session, data: dict) -> dict:
    """Stage an upsert and its audit together; the caller owns the transaction.

    Existing records merge explicitly supplied fields. Re-onboarding restores
    archived IDs, with that transition retained in the audit.
    """
    existing = db.query(CameraDB).filter(CameraDB.id == data['id']).first()
    if existing:
        changes = dict(data, archived=False)
        before = {k: getattr(existing, k) for k, v in changes.items()
                  if k in AUDIT_FIELDS and getattr(existing, k) != v}
        for k, v in changes.items():
            setattr(existing, k, v)
        _audit_camera_change(db, existing, 'UPDATE', {
            'changed_fields': sorted(before), 'before': before,
            'after': {k: getattr(existing, k) for k in before},
        })
        db.flush()
        return _cam_to_dict(existing)
    cam = CameraDB(**data)
    db.add(cam)
    db.flush()
    after = {k: getattr(cam, k) for k in AUDIT_FIELDS}
    _audit_camera_change(db, cam, 'CREATE', {
        'fields': sorted(after), 'before': {}, 'after': after,
    })
    return _cam_to_dict(cam)


def update_camera_health(db: Session, camera_id: str, status: str):
    cam = db.query(CameraDB).filter(CameraDB.id == camera_id).first()
    if cam:
        cam.health_status = status
        db.commit()


def update_camera(db: Session, camera_id: str, data: dict) -> Optional[dict]:
    """Apply a validated metadata patch to an active camera."""
    cam = db.query(CameraDB).filter(CameraDB.id == camera_id, CameraDB.archived == False).first()
    if not cam:
        return None
    if not cam.location_known and ('lat' in data or 'lon' in data):
        if 'lat' not in data or 'lon' not in data:
            from fastapi import HTTPException
            raise HTTPException(422, 'Supply both latitude and longitude for an unlocated camera')
        data = dict(data, location_known=True)
    allowed = {
        'name', 'department', 'sub_type', 'district', 'lat', 'lon', 'location_known', 'address',
        'vendor', 'model_name', 'camera_type', 'resolution', 'ir_capable',
        'protocol', 'anpr_capable', 'face_capable', 'night_capable', 'ownership',
        'storage_details', 'maintenance_status', 'coverage_radius_m',
        'onvif_profile', 'codec', 'metadata_streaming', 'motion_tamper_events',
        'https_streaming', 'ptz_capable', 'conformance_status',
        'source_freshness_seconds',
    }
    before = {key: getattr(cam, key) for key in data if key in allowed and getattr(cam, key) != data[key]}
    for key, value in data.items():
        if key in allowed:
            setattr(cam, key, value)
    _audit_camera_change(db, cam, 'PATCH', {
        'changed_fields': sorted(before),
        'before': {key: before[key] for key in before if key in allowed},
        'after': {key: getattr(cam, key, None) for key in before if key in allowed},
    })
    return _cam_to_dict(cam)


def archive_camera(db: Session, camera_id: str) -> Optional[dict]:
    """Soft-delete a camera while retaining its audit/detection history."""
    cam = db.query(CameraDB).filter(CameraDB.id == camera_id, CameraDB.archived == False).first()
    if not cam:
        return None
    cam.archived = True
    _audit_camera_change(db, cam, 'ARCHIVE', {'before': {'archived': False}, 'after': {'archived': True}})
    return {'id': camera_id, 'archived': True}


def _audit_camera_change(db: Session, camera: CameraDB, action: str, details: dict):
    """Record non-secret camera metadata changes for operator review."""
    who = principal.get() or {}
    safe_details = json.dumps(details, default=str, sort_keys=True)
    db.add(AuditDB(actor=who.get('username', 'system'), department=camera.department,
                   action=action, resource=f'/api/cameras/{camera.id}', details=safe_details))
    # Department transfers retain the same change record in both resource scopes.
    previous_department = details.get('before', {}).get('department')
    if previous_department and previous_department != camera.department:
        db.add(AuditDB(actor=who.get('username', 'system'), department=previous_department,
                       action=action, resource=f'/api/cameras/{camera.id}', details=safe_details))


def get_cameras_geojson(db: Session) -> dict:
    """Return cameras as GeoJSON FeatureCollection for Leaflet."""
    cameras = db.query(CameraDB).filter(CameraDB.archived == False).all()
    features = []
    for c in cameras:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c.lon, c.lat]} if c.location_known else None,
            "properties": {
                "id": c.id,
                "location_known": c.location_known,
                "name": c.name,
                "department": c.department,
                "sub_type": c.sub_type,
                "district": c.district,
                "address": c.address,
                "vendor": c.vendor,
                "resolution": c.resolution,
                "anpr": c.anpr_capable,
                "night": c.night_capable,
                "health": _live_health(c.id),
                "coverage_radius_m": c.coverage_radius_m,
                "protocol": c.protocol,
                "onvif_profile": c.onvif_profile,
                "codec": c.codec,
                "conformance_status": c.conformance_status,
                "metadata_streaming": c.metadata_streaming,
                "motion_tamper_events": c.motion_tamper_events,
                "https_streaming": c.https_streaming,
                "ptz_capable": c.ptz_capable,
                "onboarded": utc_iso(c.onboarded_at) if c.onboarded_at else ""
            }
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "total": len(cameras),
            "online": sum(1 for c in cameras if _live_health(c.id) == "OPERATIONAL"),
            "offline": sum(1 for c in cameras if _live_health(c.id) == "OFFLINE"),
            "degraded": sum(1 for c in cameras if _live_health(c.id) == "DEGRADED"),
        }
    }


def get_department_stats(db: Session) -> List[dict]:
    cameras = db.query(CameraDB).filter(CameraDB.archived == False).all()
    dept_map = {}
    for c in cameras:
        if c.department not in dept_map:
            dept_map[c.department] = {"department": c.department, "total": 0, "online": 0, "anpr_capable": 0}
        dept_map[c.department]["total"] += 1
        if _live_health(c.id) == "OPERATIONAL":
            dept_map[c.department]["online"] += 1
        if c.anpr_capable:
            dept_map[c.department]["anpr_capable"] += 1
    return list(dept_map.values())


def _cam_to_dict(c: CameraDB) -> dict:
    live = _live_health_payload(c.id)
    return {
        "id": c.id, "name": c.name, "department": c.department,
        "sub_type": c.sub_type, "district": c.district,
        "lat": c.lat if c.location_known else None, "lon": c.lon if c.location_known else None,
        "location_known": c.location_known, "address": c.address,
        "vendor": c.vendor, "model_name": c.model_name,
        "camera_type": c.camera_type, "resolution": c.resolution,
        "ir_capable": c.ir_capable, "protocol": c.protocol,
        "stream_url": "",
        "source_configured": bool(c.stream_url),
        "ownership": c.ownership, "storage_details": c.storage_details,
        "maintenance_status": c.maintenance_status, "coverage_radius_m": c.coverage_radius_m,
        "anpr_capable": c.anpr_capable, "face_capable": c.face_capable,
        "night_capable": c.night_capable,
        "onvif_profile": c.onvif_profile, "codec": c.codec,
        "metadata_streaming": c.metadata_streaming,
        "motion_tamper_events": c.motion_tamper_events,
        "https_streaming": c.https_streaming, "ptz_capable": c.ptz_capable,
        "conformance_status": c.conformance_status,
        "source_freshness_seconds": c.source_freshness_seconds,
        "cert_fingerprint": "",
        "health_status": live["status"],
        "health_reason": live.get("reason", ""),
        "last_frame_at": live.get("last_frame_at", ""),
        "last_inference_at": live.get("last_inference_at", ""),
        "stream_origin": live.get("origin", ""),
        "tracker": live.get("tracker", ""),
        "aci_baseline_version": c.aci_baseline_version,
        "blockchain_registered": False,
        "onboarded_at": utc_iso(c.onboarded_at) if c.onboarded_at else "",
    }


def _live_health(camera_id):
    return _live_health_payload(camera_id)["status"]


def _live_health_payload(camera_id):
    from .stream_manager import stream_manager
    return stream_manager.get_health(camera_id)
