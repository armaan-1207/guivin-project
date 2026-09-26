"""
GUIVIN — Main FastAPI Application
All routes, WebSocket broadcast, startup/shutdown lifecycle.
"""
import asyncio
import json
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional, Literal

from fastapi import Request, Query, FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from .config import ALLOWED_ORIGINS, REPORTS_DIR, EVIDENCE_DIR, WARMUP_MODELS
from .database import init_db, get_db, SessionLocal, AlertDB, CameraDB, AuditDB, DetectionDB
from .auth import AccessMiddleware, register_auth, principal, require_department, identity
from .permissions import role
from .time_utils import utc_iso
from .camera_registry import (get_all_cameras, get_camera, add_camera,
                                get_cameras_geojson, get_department_stats,
                                seed_cameras_if_empty, update_camera_health,
                                update_camera, archive_camera)
from .watchlist_engine import (get_all_watchlist, check_plate, add_watchlist_entry,
                                seed_watchlist_if_empty, normalise_plate)
from .alert_manager import (get_alerts, create_alert, acknowledge_alert,
                             get_vehicle_journey, record_detection, _alert_to_dict)
from .blockchain_ledger import verify_evidence, get_ledger, get_block_by_alert
from .aci_engine import get_baseline, compute_aci_risk
from .report_generator import (generate_csv_report, generate_pdf_report,
                                get_detections_for_report)
import requests as _requests
from .stream_manager import stream_manager

# ── Sentinel session store (set on connect, used by HLS proxy) ─────────────────
_sentinel_session: Optional[_requests.Session] = None


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s")
logger = logging.getLogger("guivin.main")


# ── WebSocket connection manager ──────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        who = identity(ws)
        from urllib.parse import urlsplit
        origin = ws.headers.get('origin')
        if not who or role(who) in ('technical_admin', 'auditor', 'judiciary') or (origin and urlsplit(origin).netloc != ws.url.netloc):
            await ws.close(code=1008)
            return False
        ws.state.principal = who
        await ws.accept()
        self.active.append(ws)
        return True

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        department = message.get('department')
        if not department:
            with SessionLocal() as db:
                cid = message.get('alert', {}).get('camera_id')
                if not cid and message.get('alert_id'):
                    alert = db.query(AlertDB).filter_by(id=message['alert_id']).first()
                    cid = alert.camera_id if alert else None
                camera = db.query(CameraDB).filter_by(id=cid).first() if cid else None
                department = camera.department if camera else None
        for ws in list(self.active):
            who = identity(ws)
            if not who:
                await ws.close(code=1008)
                dead.append(ws)
                continue
            if who['department'] != '*' and who['department'] != department:
                continue
            if role(who) in ('technical_admin', 'auditor', 'judiciary'):
                continue
            if role(who) == 'sector_supervisor':
                camera_id = message.get('alert', {}).get('camera_id')
                if not camera_id and message.get('alert_id'):
                    with SessionLocal() as db:
                        record = db.get(AlertDB, message['alert_id'])
                        camera_id = record.camera_id if record else None
                if camera_id not in who.get('camera_ids', []):
                    continue
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()


# ── App lifespan ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    loop = asyncio.get_running_loop()
    def publish(message):
        asyncio.run_coroutine_threadsafe(ws_manager.broadcast(message), loop).result(timeout=5)
    stream_manager.on_event = publish
    logger.info("GUIVIN starting up...")
    init_db()
    from .clip_evidence import recover_pending_clips
    recover_pending_clips()
    with SessionLocal() as db:
        seed_cameras_if_empty(db)
        seed_watchlist_if_empty(db)
    if WARMUP_MODELS:
        from .ai_pipeline import warmup_models
        status = warmup_models()
        logger.info("AI model warm-up status: %s", status)
    logger.info("GUIVIN ready. API at http://localhost:8000")
    yield
    # Shutdown
    await asyncio.to_thread(stream_manager.stop_all)
    from .hls_proxy import relay
    await asyncio.to_thread(relay.stop)
    logger.info("GUIVIN shutting down...")


app = FastAPI(
    title="GUIVIN API",
    description="Gujarat Unified Intelligent Video Intelligence Network",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.add_middleware(AccessMiddleware)
register_auth(app)
from .cases import router as cases_router
app.include_router(cases_router)
from .supervisor_reviews import router as reviews_router
app.include_router(reviews_router)
from .scene_rules import router as scene_router
app.include_router(scene_router)

# ── Pydantic request/response schemas ─────────────────────────────────────────
class CameraOnboardRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    department: str
    sub_type: str = "Surveillance"
    district: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    address: str = ""
    vendor: str = "Unknown"
    model_name: str = "Unknown"
    camera_type: str = "IP/Fixed"
    resolution: str = "2MP"
    ir_capable: bool = True
    protocol: str = "RTSP"
    stream_url: str = ""
    anpr_capable: bool = False
    face_capable: bool = False
    night_capable: bool = True
    ownership: str = ''
    storage_details: str = ''
    maintenance_status: str = 'UNKNOWN'
    coverage_radius_m: float = Field(default=0, ge=0, le=500)
    # ONVIF Profile T/source capability declarations. A VERIFIED status should
    # only be set after an operator or capability probe confirms the values.
    onvif_profile: str = Field(default='UNKNOWN', max_length=40)
    codec: str = Field(default='', max_length=40)
    metadata_streaming: bool = False
    motion_tamper_events: bool = False
    https_streaming: bool = False
    ptz_capable: bool = False
    conformance_status: Literal['UNKNOWN', 'DECLARED', 'VERIFIED', 'NOT_SUPPORTED'] = 'UNKNOWN'
    source_freshness_seconds: float = Field(default=0, ge=0, le=86400)


class CameraUpdateRequest(BaseModel):
    """Mutable camera registry metadata; stream credentials stay server-side."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    department: Optional[str] = Field(default=None, min_length=1, max_length=100)
    sub_type: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, min_length=1, max_length=100)
    lat: Optional[float] = Field(default=None, ge=-90, le=90)
    lon: Optional[float] = Field(default=None, ge=-180, le=180)
    address: Optional[str] = Field(default=None, max_length=300)
    vendor: Optional[str] = Field(default=None, max_length=100)
    model_name: Optional[str] = Field(default=None, max_length=100)
    camera_type: Optional[str] = Field(default=None, max_length=80)
    resolution: Optional[str] = Field(default=None, max_length=40)
    ir_capable: Optional[bool] = None
    protocol: Optional[str] = Field(default=None, max_length=20)
    anpr_capable: Optional[bool] = None
    face_capable: Optional[bool] = None
    night_capable: Optional[bool] = None
    ownership: Optional[str] = Field(default=None, max_length=160)
    storage_details: Optional[str] = Field(default=None, max_length=300)
    maintenance_status: Optional[str] = Field(default=None, max_length=40)
    coverage_radius_m: Optional[float] = Field(default=None, ge=0, le=500)
    onvif_profile: Optional[str] = Field(default=None, max_length=40)
    codec: Optional[str] = Field(default=None, max_length=40)
    metadata_streaming: Optional[bool] = None
    motion_tamper_events: Optional[bool] = None
    https_streaming: Optional[bool] = None
    ptz_capable: Optional[bool] = None
    conformance_status: Optional[Literal['UNKNOWN', 'DECLARED', 'VERIFIED', 'NOT_SUPPORTED']] = None
    source_freshness_seconds: Optional[float] = Field(default=None, ge=0, le=86400)

    model_config = {'extra': 'forbid'}

    @model_validator(mode='before')
    @classmethod
    def reject_explicit_null(cls, values):
        if isinstance(values, dict) and any(value is None for value in values.values()):
            raise ValueError('Camera metadata cannot be null; omit unchanged fields')
        return values


class StreamStartRequest(BaseModel):
    camera_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    stream_url: str
    department: str = "Default"


class WatchlistAddRequest(BaseModel):
    identifier: str
    entry_type: str = "VEHICLE"
    reason: str
    source_db: str = "Manual"
    owner_name: str = ""
    additional_info: str = ""
    priority: str = "HIGH"


class AcknowledgeRequest(BaseModel):
    operator: str = Field(min_length=1, max_length=100)
    action: Literal["ACKNOWLEDGED", "VERIFIED", "FALSE_ALARM", "ESCALATED", "CLOSED"] = "ACKNOWLEDGED"


class ANPRDemoRequest(BaseModel):
    camera_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    plate_number: str = Field(min_length=1, max_length=20)
    confidence: float = Field(default=0.88, ge=0, le=1)
    object_class: str = "car"
    dwell_seconds: float = Field(default=5.0, ge=0, le=86400)


class VerifyEvidenceRequest(BaseModel):
    alert_id: str


# ════════════════════════════════════════════════════════════════════════════════
# ── CAMERA REGISTRY ROUTES ────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/metrics", response_class=Response, tags=["Observability"])
def get_metrics():
    """Prometheus-compatible metrics endpoint."""
    active = len(stream_manager.active_streams)
    metrics = [
        f'guivin_active_streams {active}',
        'guivin_app_info{version="1.0"} 1'
    ]
    return Response(content="\n".join(metrics) + "\n", media_type="text/plain")

@app.get("/api/cameras", tags=["Camera Registry"])
def list_cameras(db: Session = Depends(get_db)):
    return get_all_cameras(db)


@app.get("/api/cameras/geojson", tags=["Camera Registry"])
def cameras_geojson(db: Session = Depends(get_db)):
    return get_cameras_geojson(db)


@app.get("/api/cameras/stats", tags=["Camera Registry"])
def department_stats(db: Session = Depends(get_db)):
    return get_department_stats(db)


@app.get("/api/cameras/{camera_id}", tags=["Camera Registry"])
def get_single_camera(camera_id: str, db: Session = Depends(get_db)):
    cam = get_camera(db, camera_id)
    if not cam:
        raise HTTPException(404, f"Camera {camera_id} not found")
    return cam


@app.post("/api/cameras", tags=["Camera Registry"])
def onboard_camera(req: CameraOnboardRequest, db: Session = Depends(get_db)):
    require_department(req.department)
    existing = db.query(CameraDB).execution_options(integrity_scan=True).filter_by(id=req.id).first()
    if existing:
        require_department(existing.department)
    result = add_camera(db, req.model_dump(exclude_unset=True))
    db.commit()
    return result


@app.patch("/api/cameras/{camera_id}", tags=["Camera Registry"])
def patch_camera(camera_id: str, req: CameraUpdateRequest, db: Session = Depends(get_db)):
    existing = get_camera(db, camera_id)
    if not existing:
        raise HTTPException(404, f"Camera {camera_id} not found")
    data = req.model_dump(exclude_unset=True)
    if not data:
        return existing
    require_department(data.get('department', existing['department']))
    updated = update_camera(db, camera_id, data)
    if not updated:
        raise HTTPException(404, f"Camera {camera_id} not found")
    db.commit()
    return updated


@app.post("/api/cameras/{camera_id}/archive", tags=["Camera Registry"])
def archive_camera_route(camera_id: str, db: Session = Depends(get_db)):
    existing = get_camera(db, camera_id)
    if not existing:
        raise HTTPException(404, f"Camera {camera_id} not found")
    require_department(existing['department'])
    stream_manager.remove_stream(camera_id)
    result = archive_camera(db, camera_id)
    db.commit()
    return result


@app.get("/api/cameras/{camera_id}/baseline", tags=["Camera Registry"])
def camera_aci_baseline(camera_id: str, db: Session = Depends(get_db)):
    baseline = get_baseline(db, camera_id)
    if not baseline:
        return {"camera_id": camera_id, "baseline_available": False}
    return {**baseline, "baseline_available": True}


# ════════════════════════════════════════════════════════════════════════════════
# ── STREAMING ROUTES ──────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.post("/api/stream/start", tags=["Streaming"])
def start_stream(req: StreamStartRequest, db: Session = Depends(get_db)):
    """Start ingesting a camera stream (RTSP/file/webcam)."""
    from .stream_security import validate_source
    validate_source(req.stream_url)
    cam = get_camera(db, req.camera_id)
    if not cam:
        raise HTTPException(404, 'Register this camera before starting analysis')
    require_department(cam['department'] if cam else req.department)
    from .stream_manager import StreamStillStopping
    try:
        stream_manager.add_stream(req.camera_id, req.stream_url, cam['department'] if cam else req.department)
    except StreamStillStopping:
        raise HTTPException(409, 'Previous worker is still stopping; retry after it stops')
    return {"started": True, "camera_id": req.camera_id, "source": "configured"}


@app.get("/api/stream/{camera_id}", tags=["Streaming"])
def stream_camera(camera_id: str, db: Session = Depends(get_db)):
    """MJPEG stream endpoint — use as <img src='/api/stream/CAMERA_ID'>"""
    authorize_stream(db, camera_id)
    return StreamingResponse(
        stream_manager.mjpeg_generator(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/stream/{camera_id}/snapshot", tags=["Streaming"])
def stream_snapshot(camera_id: str, db: Session = Depends(get_db)):
    """Single JPEG snapshot."""
    authorize_stream(db, camera_id)
    data = stream_manager.snapshot_jpeg(camera_id)
    if not data:
        raise HTTPException(404, "No frame available")
    return StreamingResponse(iter([data]), media_type="image/jpeg")


@app.get("/api/stream/{camera_id}/health", tags=["Streaming"])
def stream_health(camera_id: str, db: Session = Depends(get_db)):
    authorize_stream(db, camera_id)
    health = stream_manager.get_health(camera_id)
    return health


@app.get("/api/streams/active", tags=["Streaming"])
def active_streams(db: Session = Depends(get_db)):
    allowed = {c['id'] for c in get_all_cameras(db)}
    unrestricted = (principal.get() or {}).get('department', '*') == '*' and role(principal.get() or {}) != 'sector_supervisor'
    ids = [cid for cid in stream_manager.active_cameras() if unrestricted or cid in allowed]
    return {'active': ids, 'health': {cid: stream_manager.get_health(cid) for cid in ids}}


# ════════════════════════════════════════════════════════════════════════════════
# ── WATCHLIST ROUTES ──────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/watchlist", tags=["Watchlist"])
def list_watchlist(db: Session = Depends(get_db)):
    return get_all_watchlist(db)


@app.post("/api/watchlist", tags=["Watchlist"])
def add_to_watchlist(req: WatchlistAddRequest, db: Session = Depends(get_db)):
    return add_watchlist_entry(db, req.model_dump())


@app.get("/api/watchlist/check/{plate}", tags=["Watchlist"])
def check_plate_endpoint(plate: str, db: Session = Depends(get_db)):
    result = check_plate(db, plate)
    if result:
        return result
    return {"matched": False, "identifier": normalise_plate(plate)}


# ════════════════════════════════════════════════════════════════════════════════
# ── ALERTS ROUTES ─────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/alerts", tags=["Alerts"])
def list_alerts(limit: int = Query(default=100, ge=1, le=1000), severity: str = None,
                camera_id: str = None, db: Session = Depends(get_db)):
    return get_alerts(db, limit=limit, severity=severity, camera_id=camera_id)


@app.get("/api/alerts/{alert_id}", tags=["Alerts"])
def get_alert(alert_id: str, db: Session = Depends(get_db)):
    alert = db.query(AlertDB).filter_by(id=alert_id).first()
    if alert:
        return _alert_to_dict(alert)
    raise HTTPException(404, "Alert not found")


@app.post("/api/alerts/{alert_id}/acknowledge", tags=["Alerts"])
async def acknowledge_alert_endpoint(
        alert_id: str, req: AcknowledgeRequest,
        db: Session = Depends(get_db)):
    if role(principal.get() or {}) == 'field_operator' and req.action != 'ACKNOWLEDGED':
        raise HTTPException(403, 'Field operators may acknowledge; supervisor review is required for other actions')
    result = acknowledge_alert(db, alert_id, (principal.get() or {}).get('username', req.operator), req.action)
    if not result["success"]:
        raise HTTPException(404, "Alert not found")
    # Broadcast update to all connected WebSocket clients
    await ws_manager.broadcast({
        "event": "alert_updated",
        "alert_id": alert_id,
        "status": req.action,
        "operator": (principal.get() or {}).get("username", req.operator),
        "timestamp": utc_iso(datetime.utcnow())
    })
    return result


@app.post("/api/alerts/demo/anpr", tags=["Alerts"])
async def trigger_anpr_demo(req: ANPRDemoRequest,
                             background_tasks: BackgroundTasks,
                             db: Session = Depends(get_db)):
    """
    Demo endpoint: simulate an ANPR detection + watchlist check + alert.
    Used for evaluation demos without requiring live stream.
    """
    cam = get_camera(db, req.camera_id)
    if not cam:
        raise HTTPException(404, f"Camera {req.camera_id} not found")

    # Check watchlist
    wl_match = check_plate(db, req.plate_number)

    # ACI evaluation
    aci_result = compute_aci_risk(req.camera_id, req.object_class,
                                   req.dwell_seconds, db)

    # Build risk score
    base_risk = 0
    reason_codes = []

    if wl_match:
        reason = wl_match.get("reason", "")
        # Risk delta by reason severity
        if reason in ("STOLEN", "WANTED"):
            reason_delta = 70
        elif reason in ("BLACKLISTED", "MISSING_LINK"):
            reason_delta = 55
        else:
            reason_delta = 45 if wl_match.get("priority") == "HIGH" else 30
        base_risk += reason_delta
        reason_codes.append({
            "factor": f"Watchlist Match ({wl_match['source_db']})",
            "delta": reason_delta,
            "reason": f"{reason}: {wl_match.get('additional_info', '')[:80]}"
        })
        alert_type = "WATCHLIST_MATCH"
    else:
        alert_type = "VEHICLE_DETECTION"

    remaining = 50
    for factor in aci_result.get("factors", []):
        delta = min(remaining, factor["delta"])
        base_risk += delta
        remaining -= delta
        if delta:
            reason_codes.append(dict(factor, delta=delta))

    if base_risk == 0:
        base_risk = 15   # Minimum for any detection

    # Save detection to DB
    record_detection(db, req.camera_id, req.object_class, req.confidence,
                     req.plate_number, req.confidence * 0.9)

    # Create alert
    alert = create_alert(
        db=db,
        camera_id=req.camera_id,
        alert_type=alert_type,
        plate_number=req.plate_number,
        object_class=req.object_class,
        confidence=req.confidence,
        base_risk=base_risk,
        reason_codes=reason_codes,
        frame_path="",
        lat=cam["lat"],
        lon=cam["lon"],
        department=cam["department"],
        origin="SIMULATED", plate_confidence=req.confidence * 0.9,
        watchlist_source=wl_match["source_db"] if wl_match else "",
    )

    if alert:
        # Broadcast to WebSocket clients
        await ws_manager.broadcast({
            "event": "new_alert",
            "alert": alert,
            "watchlist_match": wl_match,
            "aci": aci_result,
        })
        return {"alert": alert, "watchlist_match": wl_match, "aci": aci_result}

    return {"suppressed": True, "reason": "Duplicate alert within dedup window"}


# ════════════════════════════════════════════════════════════════════════════════
# ── JOURNEY RECONSTRUCTION ────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/journey/{plate}", tags=["Vehicle Journey"])
def vehicle_journey(plate: str, db: Session = Depends(get_db),
                    limit: int = Query(default=1000, ge=1, le=1000),
                    offset: int = Query(default=0, ge=0, le=100000),
                    start: Optional[datetime] = None, end: Optional[datetime] = None):
    """
    Return complete vehicle journey across cameras.
    Enriches each detection with camera metadata for GIS plotting.
    """
    from .journey_correlation import assess_transitions
    if any(value is not None and value.tzinfo is None for value in (start, end)):
        raise HTTPException(422, 'Time filters require a UTC offset')
    start = start.astimezone(timezone.utc).replace(tzinfo=None) if start else None
    end = end.astimezone(timezone.utc).replace(tzinfo=None) if end else None
    if start and end and start > end:
        raise HTTPException(422, 'Start must not exceed end')
    detections = get_vehicle_journey(db, plate, limit + 1, offset, start, end)
    has_more = len(detections) > limit
    detections = assess_transitions(db, detections[:limit])
    cam_cache = {}
    enriched = []
    for d in detections:
        cid = d["camera_id"]
        if cid not in cam_cache:
            cam_cache[cid] = get_camera(db, cid) or {}
        cam = cam_cache[cid]
        enriched.append({
            **d,
            "lat": cam.get("lat", 0),
            "lon": cam.get("lon", 0),
            "camera_name": cam.get("name", cid),
            "department": cam.get("department", ""),
            "district": cam.get("district", ""),
            "address": cam.get("address", ""),
        })
    return {
        "plate": normalise_plate(plate),
        "total_sightings": len(enriched),
        "journey": enriched,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "correlation_scope": "Adjacent sightings within this page; configured travel windows and OCR threshold 0.7. Host capture timestamps; not verified road routes or identity.",
    }


class CameraLinkRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=80)
    target_id: str = Field(min_length=1, max_length=80)
    min_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    max_seconds: float = Field(gt=0, le=86400, allow_inf_nan=False)
    enabled: bool = True
    model_config = {'extra': 'forbid'}

    @model_validator(mode='after')
    def validate_link(self):
        if self.source_id == self.target_id or self.min_seconds > self.max_seconds:
            raise ValueError('A directed link needs distinct cameras and ordered travel bounds')
        return self


@app.put('/api/topology/links', tags=['Vehicle Journey'])
def configure_camera_link(req: CameraLinkRequest, db: Session = Depends(get_db)):
    from .database import CameraLinkDB
    cameras = [get_camera(db, camera_id) for camera_id in (req.source_id, req.target_id)]
    if not all(cameras):
        raise HTTPException(404, 'Camera not found')
    row = db.get(CameraLinkDB, (req.source_id, req.target_id))
    before = {k: getattr(row, k) for k in ('min_seconds', 'max_seconds', 'enabled')} if row else {}
    if row is None:
        row = CameraLinkDB(source_id=req.source_id, target_id=req.target_id)
        db.add(row)
    row.min_seconds, row.max_seconds, row.enabled = req.min_seconds, req.max_seconds, req.enabled
    row.updated_by = (principal.get() or {}).get('username', 'system')
    row.updated_at = datetime.utcnow()
    departments = {camera['department'] for camera in cameras}
    # Cross-department edge details are visible only to network-wide admins.
    for department in departments if len(departments) == 1 else {'*'}:
        db.add(AuditDB(actor=row.updated_by, department=department, action='TOPOLOGY_UPDATE',
            resource='/api/topology/links', details=json.dumps({'before': before, 'after': req.model_dump()})))
    db.commit()
    return req.model_dump()


@app.get('/api/topology/links', tags=['Vehicle Journey'])
def list_camera_links(db: Session = Depends(get_db)):
    from .database import CameraLinkDB
    return [dict(source_id=r.source_id, target_id=r.target_id, min_seconds=r.min_seconds,
                 max_seconds=r.max_seconds, enabled=r.enabled, updated_at=utc_iso(r.updated_at))
            for r in db.query(CameraLinkDB).order_by(CameraLinkDB.source_id, CameraLinkDB.target_id)]


@app.post("/api/journey/seed", tags=["Vehicle Journey"])
async def seed_journey_demo(db: Session = Depends(get_db)):
    """
    Seed demo journey data for a watchlisted vehicle (GJ-01-AB-1234)
    so journey reconstruction can be demonstrated immediately.
    """
    from datetime import timedelta

    require_department("*")
    demo_plate = normalise_plate("GJ01AB1234")
    cameras_and_times = [
        ("RTO-AHMD-NAROL-004",  -15),  # 15 min ago
        ("HD-AHMD-ASHRAM-001",  -10),  # 10 min ago
        ("HD-AHMD-NEHRU-002",    -5),  # 5 min ago
        ("RTO-AHMD-SARDAR-005",  -2),  # 2 min ago
    ]
    now = datetime.utcnow()
    for cam_id, minutes_offset in cameras_and_times:
        from .database import DetectionDB, SessionLocal
        db2 = SessionLocal()
        try:
            d = DetectionDB(
                camera_id=cam_id,
                timestamp=now + timedelta(minutes=minutes_offset),
                object_class="car",
                confidence=0.91,
                plate_number=demo_plate,
                plate_confidence=0.88,
            )
            db2.add(d)
            db2.commit()
        finally:
            db2.close()

    return {"seeded": True, "plate": demo_plate,
            "cameras": [c[0] for c in cameras_and_times]}


# ════════════════════════════════════════════════════════════════════════════════
# ── BLOCKCHAIN ROUTES ─────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/blockchain/ledger", tags=["Blockchain"])
def blockchain_ledger(limit: int = Query(default=50, ge=1, le=1000), db: Session = Depends(get_db)):
    return get_ledger(db, limit)


@app.post("/api/blockchain/verify", tags=["Blockchain"])
def verify_alert_evidence(req: VerifyEvidenceRequest, db: Session = Depends(get_db)):
    return verify_evidence(db, req.alert_id)


@app.get("/api/blockchain/block/{alert_id}", tags=["Blockchain"])
def get_alert_block(alert_id: str, db: Session = Depends(get_db)):
    block = get_block_by_alert(db, alert_id)
    if not block:
        raise HTTPException(404, "Alert not found on chain")
    return block


# ════════════════════════════════════════════════════════════════════════════════
# ── REPORTS ROUTES ────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/reports/csv", tags=["Reports"])
def download_csv_report(db: Session = Depends(get_db)):
    alerts = get_alerts(db, limit=1000)
    cams = {c["id"]: c for c in get_all_cameras(db)}
    rows = get_detections_for_report(alerts, cams)
    filepath = generate_csv_report(rows)
    return FileResponse(filepath, filename="GUIVIN_Detection_Report.csv",
                        media_type="text/csv")


@app.get("/api/reports/pdf", tags=["Reports"])
def download_pdf_report(db: Session = Depends(get_db)):
    alerts = get_alerts(db, limit=500)
    cams = {c["id"]: c for c in get_all_cameras(db)}
    filepath = generate_pdf_report(alerts, cams)
    if not filepath:
        raise HTTPException(500, "PDF generation failed — ensure reportlab is installed")
    return FileResponse(filepath, filename="GUIVIN_Incident_Report.pdf",
                        media_type="application/pdf")


# ════════════════════════════════════════════════════════════════════════════════
# ── ACI ROUTES ────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/aci/{camera_id}", tags=["ACI"])
def get_aci_baseline_endpoint(camera_id: str, db: Session = Depends(get_db)):
    baseline = get_baseline(db, camera_id)
    if not baseline:
        return {"camera_id": camera_id, "message": "No baseline configured"}
    return baseline


@app.post("/api/aci/{camera_id}/evaluate", tags=["ACI"])
def evaluate_aci(camera_id: str, object_class: str = "car",
                 dwell_seconds: float = 10.0,
                 db: Session = Depends(get_db)):
    return compute_aci_risk(camera_id, object_class, dwell_seconds, db)


# ════════════════════════════════════════════════════════════════════════════════
# ── SENTINEL SANDBOX INTEGRATION ─────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get('/api/aci/{camera_id}/profiles', tags=['ACI'])
def aci_profiles(camera_id: str, db: Session = Depends(get_db)):
    from .database import ACIProfileDB
    from .aci_learning import serialize
    if not get_camera(db, camera_id):
        raise HTTPException(404, 'Camera not found')
    return [serialize(row) for row in db.query(ACIProfileDB).filter_by(camera_id=camera_id)
            .order_by(ACIProfileDB.created_at.desc()).limit(100)]


@app.post('/api/aci/{camera_id}/profiles', tags=['ACI'])
def aci_build(camera_id: str, db: Session = Depends(get_db)):
    from .aci_learning import build_candidate
    if not get_camera(db, camera_id):
        raise HTTPException(404, 'Camera not found')
    return build_candidate(db, camera_id)


@app.post('/api/aci/{camera_id}/profiles/{profile_id}/approve', tags=['ACI'])
def aci_approve(camera_id: str, profile_id: str, db: Session = Depends(get_db)):
    from .aci_learning import activate
    if not get_camera(db, camera_id):
        raise HTTPException(404, 'Camera not found')
    return activate(db, camera_id, profile_id)


class SentinelRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    max_cameras: int = Field(default=5, ge=1, le=50)
    use_hls: bool = False

@app.post("/api/sentinel/connect", tags=["Sandbox"])
async def connect_sentinel_sandbox(req: SentinelRequest, db: Session = Depends(get_db)):
    email, password, max_cameras, use_hls = req.email, req.password, req.max_cameras, req.use_hls
    require_department('Home Department')
    """
    Connect to cctv.corp8.cloud using your registered email + access password.
    Fetches the live cameras.json catalogue, registers all cameras in the GUIVIN
    registry, and starts RTSP or HLS streams feeding into the AI pipeline.

    Stream URL format used:
      RTSP: rtsp://email%40domain:password@103.250.160.189:8554/stream/<id>
      HLS:  https://cctv.corp8.cloud/<id>/index.m3u8  (CDN, any network)

    Set use_hls=true if TCP port 8554 is blocked on your network.
    """
    from .sentinel_connector import SentinelConnector

    connector = SentinelConnector(email=email, password=password)

    if not await asyncio.to_thread(connector.login):
        if connector.last_error in ('network', 'internal'):
            raise HTTPException(503, 'Cannot reach Camera Grid. Check server network access and retry.')
        raise HTTPException(401, "Sentinel login failed — check email and password at cctv.corp8.cloud")

    # Store session globally for HLS proxy use
    global _sentinel_session
    _sentinel_session = connector._session
    logger.info("[Sentinel] Session stored for HLS proxy")

    cameras = await asyncio.to_thread(connector.fetch_cameras)
    if not cameras:
        raise HTTPException(502, "Sentinel returned no cameras — catalogue fetch failed")

    if max_cameras > 0:
        cameras = cameras[:max_cameras]

    started = []
    failed = []
    registered = []
    onboarding = []
    for cam in cameras:
        cam_id     = f"SENTINEL-{cam['id'].upper()}"
        stream_url = connector.hls_url(cam["id"]) if use_hls else connector.rtsp_url(cam["id"])
        dept       = "Home Department"
        existing_camera = get_camera(db, cam_id)
        located = cam['lat'] is not None and cam['lon'] is not None
        if not located and existing_camera and existing_camera.get('location_known'):
            cam['lat'], cam['lon'] = existing_camera['lat'], existing_camera['lon']
            located = True

        onboard_data = {
            "id":           cam_id,
            "name":         cam["name"],
            "department":   dept,
            "sub_type":     "Sentinel Live Feed",
            "district":     "Unverified",
            "lat":          cam['lat'] if located else 0,
            "lon":          cam['lon'] if located else 0,
            "location_known": located,
            "address":      cam["location"],
            "vendor":       "Sentinel Corp8 / GUIVIN",
            "model_name":   "Unverified",
            "camera_type":  "Unverified",
            "resolution":   "Unverified",
            "ir_capable":   False,
            "protocol":     "HLS" if use_hls else "RTSP",
            "stream_url":   "",
            "anpr_capable": False,
            "face_capable": False,
            "night_capable": False,
            "ownership": "Source department unverified; Home Department is the local access scope",
            "conformance_status": "UNKNOWN",
        }
        onboarding.append((cam, onboard_data, stream_url))

    # Stage all camera records and their audit events before starting any feed.
    try:
        for cam, onboard_data, stream_url in onboarding:
            add_camera(db, onboard_data)
            registered.append(onboard_data['id'])
        db.commit()
    except Exception:
        db.rollback()
        raise

    for cam, onboard_data, stream_url in onboarding:
        cam_id, dept = onboard_data['id'], onboard_data['department']
        from .hls_proxy import relay
        from .stream_manager import StreamStillStopping
        try:
            analysis_url = await asyncio.to_thread(relay.register, cam_id, connector._session) if use_hls else stream_url
            await asyncio.to_thread(stream_manager.add_stream, cam_id, analysis_url, dept)
        except Exception as error:
            reason = 'PREVIOUS_WORKER_STOPPING' if isinstance(error, StreamStillStopping) else 'WORKER_START_FAILED'
            failed.append({'camera_id': cam_id, 'reason': reason})
            logger.warning('[Sentinel] Worker request failed for %s: %s', cam_id, reason)
            continue
        started.append({
            "camera_id":  cam_id,
            "name":       cam["name"],
            "location":   cam["location"],
            "stream_url": "",
            "protocol":   "HLS" if use_hls else "RTSP",
        })

    await ws_manager.broadcast({
        "type":            "sentinel_connected",
        "cameras_total":   len(cameras),
        "cameras_started": len(started),
        "cameras_failed": len(failed),
        "protocol":        "HLS" if use_hls else "RTSP",
    })

    return {
        "connected":          bool(started),
        "host":               "cctv.corp8.cloud",
        "email":              email,
        "protocol":           "HLS" if use_hls else "RTSP",
        "cameras_fetched":    len(cameras),
        "cameras_registered": len(registered),
        "streams_started":    started,
        "streams_failed":     failed,
        "partial":            bool(started and failed),
        "note": (
            "Worker requests have completed; inspect per-camera health for feed readiness. "
            "Failed workers may be retried after any previous worker has stopped."
        ),
    }


# ════════════════════════════════════════════════════════════════════════════════
# ── HLS PROXY (bypasses CORS — backend fetches cctv.corp8.cloud with session) ──
# ════════════════════════════════════════════════════════════════════════════════

@app.get('/api/proxy/hls/{camera_id}/{segment:path}', tags=['Proxy'])
def proxy_hls(camera_id: str, segment: str, u: str = '', db: Session = Depends(get_db)):
    if not _sentinel_session:
        raise HTTPException(403, 'Connect Sentinel first')
    authorize_stream(db, camera_id)
    from .hls_proxy import fetch_asset, camera_base
    content, media_type = fetch_asset(_sentinel_session, camera_id, u or camera_base(camera_id) + segment,
                                     f'/api/proxy/hls/{camera_id}/asset')
    return Response(content, media_type=media_type, headers={'Cache-Control':'no-store'})

# ════════════════════════════════════════════════════════════════════════════════
# ── WEBSOCKET ─────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not await ws_manager.connect(ws):
        return
    try:
        while True:
            data = await ws.receive_text()
            # Echo ping back as pong
            if data == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ════════════════════════════════════════════════════════════════════════════════
# ── HEALTH & INFO ─────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/health", tags=["System"])
def health_check(db: Session = Depends(get_db)):
    from .database import CameraDB, AlertDB, WatchlistDB, BlockchainLedgerDB
    from .ai_pipeline import model_status
    models = model_status()
    required = (models.get('yolo'), models.get('ocr'))
    if 'FAILED' in required:
        analysis_readiness = 'FAILED'
    elif all(state == 'READY' for state in required):
        analysis_readiness = 'READY'
    else:
        analysis_readiness = 'NOT_INITIALIZED'
    return {
        "models": models,
        "status": "operational",
        "readiness": {
            "api": "READY",
            "database": "READY",
            "analysis": analysis_readiness,
            "capture": "READY" if stream_manager.active_cameras() else "IDLE",
        },
        "timestamp": utc_iso(datetime.utcnow()),
        "system": "GUIVIN v1.0",
        "cameras_registered": db.query(CameraDB).count(),
        "alerts_total": db.query(AlertDB).count(),
        "watchlist_entries": db.query(WatchlistDB).filter(WatchlistDB.active == True).count(),
        "blockchain_blocks": db.query(BlockchainLedgerDB).count(),
        "active_streams": len(active_streams(db)["active"]),
    }


# ── Serve frontend ────────────────────────────────────────────────────────────
import os
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_dir, "static")), name="static")

    @app.get("/", include_in_schema=False)
    def serve_index():
        from fastapi.responses import HTMLResponse
        from pathlib import Path
        import re
        html = Path(frontend_dir, 'index.html').read_text(encoding='utf-8')
        def version(match):
            asset = Path(frontend_dir, match.group(0).lstrip('/'))
            return match.group(0) + '?v=' + str(asset.stat().st_mtime_ns)
        html = re.sub(r'/static/(?:js|css)/[A-Za-z0-9_.-]+', version, html)
        return HTMLResponse(html)

@app.get('/api/map-config', tags=['System'])
def map_config():
    from urllib.parse import urlsplit
    tile_url = os.getenv('GUIVIN_TILE_URL', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png')
    try:
        parsed = urlsplit(tile_url)
        valid = parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    return {'tile_url':tile_url if valid else '',
            'attribution':os.getenv('GUIVIN_MAP_ATTRIBUTION', '© OpenStreetMap contributors')}

def authorize_stream(db, camera_id):
    scoped = (principal.get() or {}).get('department', '*') != '*' or role(principal.get() or {}) == 'sector_supervisor'
    if scoped and not get_camera(db, camera_id):
        raise HTTPException(404, 'Camera not found')

@app.post('/api/stream/{camera_id}/stop', tags=['Streaming'])
def stop_stream(camera_id: str, db: Session = Depends(get_db)):
    authorize_stream(db,camera_id)
    stream_manager.request_stop(camera_id)
    return {'stopped': stream_manager.get_health(camera_id)['status'] == 'NOT_STREAMING',
            'health': stream_manager.get_health(camera_id)}

@app.get('/api/evidence/{alert_id}', tags=['Evidence'])
def evidence_file(alert_id: str, db: Session = Depends(get_db)):
    from pathlib import Path
    record=db.query(AlertDB).filter_by(id=alert_id).first()
    if not record or not record.frame_path:
        raise HTTPException(404,'No captured evidence')
    path=Path(record.frame_path).resolve()
    if not path.is_relative_to(EVIDENCE_DIR.resolve()) or not path.is_file():
        raise HTTPException(404,'Evidence unavailable')
    return FileResponse(path,media_type='image/jpeg')

@app.get('/api/evidence/{alert_id}/clip', tags=['Evidence'])
def clip_status(alert_id: str, db: Session = Depends(get_db)):
    from .database import ClipEvidenceDB
    if not db.get(AlertDB, alert_id):
        raise HTTPException(404, 'Alert not found')
    row = db.get(ClipEvidenceDB, alert_id)
    if not row:
        return {'alert_id': alert_id, 'status': 'UNAVAILABLE', 'reason': 'No clip requested'}
    return dict(alert_id=alert_id, status=row.status, reason=row.reason,
                started_at=utc_iso(row.started_at), ended_at=utc_iso(row.ended_at),
                frame_count=row.frame_count, pre_seconds=row.pre_seconds, post_seconds=row.post_seconds,
                sampling_fps=5, media_type='video/x-msvideo',
                timestamp_basis='Host capture time; sampled video, not original source timing',
                download_url=f'/api/evidence/{alert_id}/clip/download' if row.path else '')


@app.get('/api/evidence/{alert_id}/clip/download', tags=['Evidence'])
def clip_download(alert_id: str, db: Session = Depends(get_db)):
    from .database import ClipEvidenceDB
    from pathlib import Path
    row = db.get(ClipEvidenceDB, alert_id)
    if not row or row.status not in ('COMPLETE', 'PARTIAL') or not row.path:
        raise HTTPException(404, 'Clip unavailable')
    path = Path(row.path).resolve()
    if not path.is_relative_to(EVIDENCE_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, 'Clip unavailable')
    return FileResponse(path, media_type='video/x-msvideo', filename=alert_id + '.avi')


@app.get('/api/evidence/{alert_id}/clip/verify', tags=['Evidence'])
def clip_verify(alert_id: str, db: Session = Depends(get_db)):
    if not db.get(AlertDB, alert_id):
        raise HTTPException(404, 'Alert not found')
    result = verify_evidence(db, alert_id, evidence_type='CLIP')
    result['verification_scope'] = 'Clip bytes and local chain; not temporal completeness or authenticity'
    return result


@app.get('/api/evidence/{alert_id}/clip/preview', tags=['Evidence'])
def clip_preview(alert_id: str, db: Session = Depends(get_db)):
    from .database import ClipEvidenceDB
    from pathlib import Path
    import cv2
    import time
    row = db.get(ClipEvidenceDB, alert_id)
    if not row or row.status not in ('COMPLETE', 'PARTIAL') or not row.path:
        raise HTTPException(404, 'Clip unavailable')
    path = Path(row.path).resolve()
    if not path.is_relative_to(EVIDENCE_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, 'Clip unavailable')
    def frames():
        cap = cv2.VideoCapture(str(path))
        try:
            for _ in range(1000):
                ok, frame = cap.read()
                if not ok:
                    break
                ok, jpeg = cv2.imencode('.jpg', frame)
                if ok:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
                time.sleep(.2)
        finally:
            cap.release()
    return StreamingResponse(frames(), media_type='multipart/x-mixed-replace; boundary=frame')


class BulkCameras(BaseModel):
    cameras: List[CameraOnboardRequest] = Field(min_length=1,max_length=1000)

@app.post('/api/cameras/bulk', tags=['Camera Registry'])
def bulk_cameras(req: BulkCameras, db: Session = Depends(get_db)):
    ids=[c.id for c in req.cameras]
    if len(set(ids)) != len(ids):
        raise HTTPException(422,'Duplicate camera IDs in import')
    for camera in req.cameras:
        require_department(camera.department)
        existing=db.query(CameraDB).execution_options(integrity_scan=True).filter_by(id=camera.id).first()
        if existing:
            require_department(existing.department)
    # Reuse the registry write path so each row receives the same safe audit
    # details as manual/API onboarding. Stream credentials are intentionally
    # omitted from those details by add_camera().
    for camera in req.cameras:
        add_camera(db, camera.model_dump(exclude_unset=True))
    db.commit()
    return {'imported':len(ids),'camera_ids':ids}

@app.get('/api/registry/audit', tags=['Camera Registry'])
def audit_log(limit: int = Query(default=100,ge=1,le=1000), db: Session = Depends(get_db)):
    return [dict(id=a.id,actor=a.actor,action=a.action,resource=a.resource,timestamp=utc_iso(a.timestamp),
                 details=json.loads(a.details) if a.details else {})
            for a in db.query(AuditDB).order_by(AuditDB.id.desc()).limit(limit)]

@app.get('/api/registry/coverage', tags=['Camera Registry'])
def coverage(lat: float = Query(ge=-90,le=90), lon: float = Query(ge=-180,le=180), db: Session = Depends(get_db)):
    from math import radians,sin,cos,asin,sqrt
    matches=[]
    for camera in db.query(CameraDB).filter(CameraDB.archived == False):
        if not camera.coverage_radius_m:
            continue
        a=sin(radians(camera.lat-lat)/2)**2+cos(radians(lat))*cos(radians(camera.lat))*sin(radians(camera.lon-lon)/2)**2
        distance=6371000*2*asin(min(1,sqrt(a)))
        if distance <= camera.coverage_radius_m:
            matches.append(camera.id)
    return {'camera_ids':matches,'status':'APPROXIMATE_COVERAGE' if matches else 'NO_DECLARED_COVERAGE',
            'assumption':'Declared circular radius only; no field-of-view, terrain or obstruction model'}

@app.get('/api/reports/detections', tags=['Reports'])
def detection_report(db: Session = Depends(get_db)):
    import csv, io
    buffer=io.StringIO()
    writer=csv.writer(buffer)
    writer.writerow(['Timestamp UTC','Camera','Plate','OCR confidence','Detection confidence','Origin','Track','Alert'])
    for det in db.query(DetectionDB).order_by(DetectionDB.timestamp.desc()).limit(10000):
        values=[utc_iso(det.timestamp),det.camera_id,det.plate_number,det.plate_confidence,det.confidence,det.origin,det.track_id,det.alert_id]
        writer.writerow([("'" + str(v)) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in values])
    return Response(buffer.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="GUIVIN_Detections.csv"'})
