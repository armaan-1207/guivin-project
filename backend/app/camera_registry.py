"""
GUIVIN — Camera Registry Module
Handles onboarding, CRUD, and GIS metadata for all cameras.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session

from .database import CameraDB, ACIBaselineDB
from .config import DATA_DIR


def seed_cameras_if_empty(db: Session):
    """Load cameras.json into DB on first run."""
    if db.query(CameraDB).count() == 0:
        cam_file = DATA_DIR / "cameras.json"
        if cam_file.exists():
            cameras = json.loads(cam_file.read_text())
            for c in cameras:
                db_cam = CameraDB(**c)
                db.add(db_cam)
            db.commit()
            print(f"[CameraRegistry] Seeded {len(cameras)} cameras from cameras.json")

    # Also seed ACI baselines
    if db.query(ACIBaselineDB).count() == 0:
        baseline_file = DATA_DIR / "aci_baselines.json"
        if baseline_file.exists():
            baselines = json.loads(baseline_file.read_text())
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
    cameras = db.query(CameraDB).all()
    return [_cam_to_dict(c) for c in cameras]


def get_camera(db: Session, camera_id: str) -> Optional[dict]:
    cam = db.query(CameraDB).filter(CameraDB.id == camera_id).first()
    return _cam_to_dict(cam) if cam else None


def add_camera(db: Session, data: dict) -> dict:
    existing = db.query(CameraDB).filter(CameraDB.id == data["id"]).first()
    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
        db.commit()
        return _cam_to_dict(existing)
    cam = CameraDB(**data)
    db.add(cam)
    db.commit()
    db.refresh(cam)
    return _cam_to_dict(cam)


def update_camera_health(db: Session, camera_id: str, status: str):
    cam = db.query(CameraDB).filter(CameraDB.id == camera_id).first()
    if cam:
        cam.health_status = status
        db.commit()


def get_cameras_geojson(db: Session) -> dict:
    """Return cameras as GeoJSON FeatureCollection for Leaflet."""
    cameras = db.query(CameraDB).all()
    features = []
    for c in cameras:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [c.lon, c.lat]},
            "properties": {
                "id": c.id,
                "name": c.name,
                "department": c.department,
                "sub_type": c.sub_type,
                "district": c.district,
                "address": c.address,
                "vendor": c.vendor,
                "resolution": c.resolution,
                "anpr": c.anpr_capable,
                "night": c.night_capable,
                "health": c.health_status,
                "protocol": c.protocol,
                "onboarded": c.onboarded_at.isoformat() if c.onboarded_at else ""
            }
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "total": len(cameras),
            "online": sum(1 for c in cameras if c.health_status == "OPERATIONAL"),
            "offline": sum(1 for c in cameras if c.health_status == "OFFLINE"),
            "degraded": sum(1 for c in cameras if c.health_status == "DEGRADED"),
        }
    }


def get_department_stats(db: Session) -> List[dict]:
    cameras = db.query(CameraDB).all()
    dept_map = {}
    for c in cameras:
        if c.department not in dept_map:
            dept_map[c.department] = {"department": c.department, "total": 0, "online": 0, "anpr_capable": 0}
        dept_map[c.department]["total"] += 1
        if c.health_status == "OPERATIONAL":
            dept_map[c.department]["online"] += 1
        if c.anpr_capable:
            dept_map[c.department]["anpr_capable"] += 1
    return list(dept_map.values())


def _cam_to_dict(c: CameraDB) -> dict:
    return {
        "id": c.id, "name": c.name, "department": c.department,
        "sub_type": c.sub_type, "district": c.district,
        "lat": c.lat, "lon": c.lon, "address": c.address,
        "vendor": c.vendor, "model_name": c.model_name,
        "camera_type": c.camera_type, "resolution": c.resolution,
        "ir_capable": c.ir_capable, "protocol": c.protocol,
        "stream_url": c.stream_url,
        "anpr_capable": c.anpr_capable, "face_capable": c.face_capable,
        "night_capable": c.night_capable,
        "cert_fingerprint": c.cert_fingerprint,
        "health_status": c.health_status,
        "aci_baseline_version": c.aci_baseline_version,
        "blockchain_registered": c.blockchain_registered,
        "onboarded_at": c.onboarded_at.isoformat() if c.onboarded_at else "",
    }
