"""
GUIVIN — Adaptive Contextual Intelligence (ACI) Engine
Evaluates detections against pre-computed camera baselines.
Generates anomaly risk scores when behavior deviates from normal.
"""
import json
from datetime import datetime
from .time_utils import IST
from typing import Optional
from sqlalchemy.orm import Session

from .database import ACIBaselineDB, ACIProfileDB
from .source_origin import is_sentinel_camera


NIGHT_HOURS = list(range(22, 24)) + list(range(0, 6))   # 10PM – 6AM


def get_baseline(db: Session, camera_id: str) -> Optional[dict]:
    if is_sentinel_camera(camera_id):
        return None
    profile = db.query(ACIProfileDB).filter_by(camera_id=camera_id, status='ACTIVE').first()
    if profile:
        return dict(json.loads(profile.features), camera_id=camera_id,
                    version=profile.id, validated=True, origin='LEARNED_SAMPLED',
                    validated_by=profile.approved_by)
    bl = db.query(ACIBaselineDB).filter(ACIBaselineDB.camera_id == camera_id).first()
    if not bl:
        return None
    return {
        "camera_id": bl.camera_id,
        "avg_vehicles_per_hour": bl.avg_vehicles_per_hour,
        "avg_persons_per_hour": bl.avg_persons_per_hour,
        "dominant_direction": bl.dominant_direction,
        "avg_dwell_seconds": bl.avg_dwell_seconds,
        "peak_hours": json.loads(bl.peak_hours) if bl.peak_hours else [],
        "night_occupancy_rate": bl.night_occupancy_rate,
        "version": bl.version,
        "validated": bl.validated,
        "origin": "SEEDED_RULES",
    }


def evaluate_dwell_anomaly(camera_id: str, dwell_seconds: float,
                            object_class: str, db: Session) -> dict:
    """
    Evaluate if an object's dwell time is anomalous for this camera.
    Returns: {anomaly: bool, risk_delta: int, reason: str}
    """
    baseline = get_baseline(db, camera_id)
    if not baseline or not baseline['validated']:
        return {"anomaly": False, "risk_delta": 0, "reason": "No baseline"}

    avg_dwell = baseline["avg_dwell_seconds"]
    ratio = dwell_seconds / avg_dwell if avg_dwell > 0 else 1.0
    hour = datetime.now(IST).hour
    is_night = hour in NIGHT_HOURS

    # Night multiplier — anomalies at night carry more weight
    night_multiplier = 2.0 if is_night else 1.0

    if ratio > 10 and is_night:
        return {
            "anomaly": True,
            "risk_delta": int(30 * night_multiplier),
            "reason": f"Dwell {dwell_seconds:.0f}s is {ratio:.0f}× above baseline at night (baseline: {avg_dwell:.0f}s)"
        }
    elif ratio > 15:
        return {
            "anomaly": True,
            "risk_delta": 25,
            "reason": f"Dwell {dwell_seconds:.0f}s is {ratio:.0f}× above baseline (baseline: {avg_dwell:.0f}s)"
        }
    elif ratio > 5:
        return {
            "anomaly": True,
            "risk_delta": 12,
            "reason": f"Extended dwell {dwell_seconds:.0f}s above average baseline"
        }
    return {"anomaly": False, "risk_delta": 0, "reason": "Dwell within normal range"}


def evaluate_night_presence(camera_id: str, object_class: str, db: Session) -> dict:
    """Evaluate if presence at this hour is anomalous for this camera."""
    baseline = get_baseline(db, camera_id)
    hour = datetime.now(IST).hour
    is_night = hour in NIGHT_HOURS

    if not baseline or not baseline['validated'] or not is_night:
        return {"anomaly": False, "risk_delta": 0, "reason": "Daytime — normal"}

    night_rate = baseline["night_occupancy_rate"]
    # Very low night occupancy rate = anomaly when someone appears
    if night_rate < 0.03:
        return {
            "anomaly": True,
            "risk_delta": 20,
            "reason": f"Presence at {hour:02d}:00 — baseline night occupancy is {night_rate*100:.0f}% (near-zero)"
        }
    elif night_rate < 0.08:
        return {
            "anomaly": True,
            "risk_delta": 10,
            "reason": f"Presence at {hour:02d}:00 — low expected night occupancy ({night_rate*100:.0f}%)"
        }
    return {"anomaly": False, "risk_delta": 0, "reason": "Night occupancy within expected range"}


def compute_aci_risk(camera_id: str, object_class: str,
                     dwell_seconds: float, db: Session) -> dict:
    """
    Full ACI risk computation for an observation.
    Returns: {risk_score: int, factors: list, is_anomaly: bool}
    """
    factors = []
    total_risk = 0

    dwell_result = evaluate_dwell_anomaly(camera_id, dwell_seconds, object_class, db)
    if dwell_result["anomaly"]:
        total_risk += dwell_result["risk_delta"]
        factors.append({"factor": "Extended Dwell", "delta": dwell_result["risk_delta"],
                         "reason": dwell_result["reason"]})

    night_result = evaluate_night_presence(camera_id, object_class, db)
    if night_result["anomaly"]:
        total_risk += night_result["risk_delta"]
        factors.append({"factor": "Night Presence", "delta": night_result["risk_delta"],
                         "reason": night_result["reason"]})

    is_anomaly = total_risk >= 10
    severity = "HIGH" if total_risk >= 30 else ("MEDIUM" if total_risk >= 15 else "LOW")

    return {
        "camera_id": camera_id,
        "risk_score": min(total_risk, 50),   # ACI contribution capped at 50; other factors add more
        "is_anomaly": is_anomaly,
        "severity": severity,
        "factors": factors,
        "baseline_version": get_baseline(db, camera_id)["version"] if get_baseline(db, camera_id) else "N/A",
    }
