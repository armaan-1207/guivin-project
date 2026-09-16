"""
GUIVIN — Main FastAPI Application
All routes, WebSocket broadcast, startup/shutdown lifecycle.
"""
import asyncio
import json
import uuid
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import ALLOWED_ORIGINS, REPORTS_DIR, EVIDENCE_DIR
from .database import init_db, get_db
from .camera_registry import (get_all_cameras, get_camera, add_camera,
                                get_cameras_geojson, get_department_stats,
                                seed_cameras_if_empty, update_camera_health)
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
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
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
    logger.info("GUIVIN starting up...")
    init_db()
    db = next(get_db())
    seed_cameras_if_empty(db)
    seed_watchlist_if_empty(db)
    db.close()
    logger.info("GUIVIN ready. API at http://localhost:8000")
    yield
    # Shutdown
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


# ── Pydantic request/response schemas ─────────────────────────────────────────
class CameraOnboardRequest(BaseModel):
    id: str
    name: str
    department: str
    sub_type: str = "Surveillance"
    district: str
    lat: float
    lon: float
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


class StreamStartRequest(BaseModel):
    camera_id: str
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
    operator: str
    action: str = "ACKNOWLEDGED"   # ACKNOWLEDGED / VERIFIED / FALSE_ALARM


class ANPRDemoRequest(BaseModel):
    camera_id: str
    plate_number: str
    confidence: float = 0.88
    object_class: str = "car"
    dwell_seconds: float = 5.0


class VerifyEvidenceRequest(BaseModel):
    alert_id: str


# ════════════════════════════════════════════════════════════════════════════════
# ── CAMERA REGISTRY ROUTES ────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

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
    return add_camera(db, req.model_dump())


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
def start_stream(req: StreamStartRequest):
    """Start ingesting a camera stream (RTSP/file/webcam)."""
    stream_manager.add_stream(req.camera_id, req.stream_url, req.department)
    return {"started": True, "camera_id": req.camera_id, "url": req.stream_url}


@app.get("/api/stream/{camera_id}", tags=["Streaming"])
def stream_camera(camera_id: str):
    """MJPEG stream endpoint — use as <img src='/api/stream/CAMERA_ID'>"""
    return StreamingResponse(
        stream_manager.mjpeg_generator(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/api/stream/{camera_id}/snapshot", tags=["Streaming"])
def stream_snapshot(camera_id: str):
    """Single JPEG snapshot."""
    data = stream_manager.snapshot_jpeg(camera_id)
    if not data:
        raise HTTPException(404, "No frame available")
    return StreamingResponse(iter([data]), media_type="image/jpeg")


@app.get("/api/stream/{camera_id}/health", tags=["Streaming"])
def stream_health(camera_id: str, db: Session = Depends(get_db)):
    health = stream_manager.get_health(camera_id)
    # For Sentinel HLS streams: backend worker can't reach RTSP (port blocked),
    # but the browser plays HLS directly — report as OPERATIONAL.
    if health["status"] == "OFFLINE" and camera_id.startswith("SENTINEL-"):
        cam = get_camera(db, camera_id)
        if cam and cam.get("protocol") == "HLS":
            return {"status": "OPERATIONAL", "reason": "HLS stream active in browser"}
    return health


@app.get("/api/streams/active", tags=["Streaming"])
def active_streams():
    return {"active": stream_manager.active_cameras(),
            "health": stream_manager.get_all_health()}


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
def list_alerts(limit: int = 100, severity: str = None,
                camera_id: str = None, db: Session = Depends(get_db)):
    return get_alerts(db, limit=limit, severity=severity, camera_id=camera_id)


@app.get("/api/alerts/{alert_id}", tags=["Alerts"])
def get_alert(alert_id: str, db: Session = Depends(get_db)):
    alerts = get_alerts(db, limit=1000)
    for a in alerts:
        if a["id"] == alert_id:
            return a
    raise HTTPException(404, "Alert not found")


@app.post("/api/alerts/{alert_id}/acknowledge", tags=["Alerts"])
async def acknowledge_alert_endpoint(
        alert_id: str, req: AcknowledgeRequest,
        db: Session = Depends(get_db)):
    result = acknowledge_alert(db, alert_id, req.operator, req.action)
    # Broadcast update to all connected WebSocket clients
    await ws_manager.broadcast({
        "event": "alert_updated",
        "alert_id": alert_id,
        "status": req.action,
        "operator": req.operator,
        "timestamp": datetime.utcnow().isoformat()
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

    for factor in aci_result.get("factors", []):
        base_risk += factor["delta"]
        reason_codes.append(factor)

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
def vehicle_journey(plate: str, db: Session = Depends(get_db)):
    """
    Return complete vehicle journey across cameras.
    Enriches each detection with camera metadata for GIS plotting.
    """
    detections = get_vehicle_journey(db, plate)
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
    }


@app.post("/api/journey/seed", tags=["Vehicle Journey"])
async def seed_journey_demo(db: Session = Depends(get_db)):
    """
    Seed demo journey data for a watchlisted vehicle (GJ-01-AB-1234)
    so journey reconstruction can be demonstrated immediately.
    """
    from datetime import timedelta

    demo_plate = "GJ-01-AB-1234"
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
def blockchain_ledger(limit: int = 50, db: Session = Depends(get_db)):
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

@app.post("/api/sentinel/connect", tags=["Sandbox"])
async def connect_sentinel_sandbox(
    email: str,
    password: str,
    max_cameras: int = 5,
    use_hls: bool = False,
    db: Session = Depends(get_db),
):
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

    if not connector.login():
        raise HTTPException(401, "Sentinel login failed — check email and password at cctv.corp8.cloud")

    # Store session globally for HLS proxy use
    global _sentinel_session
    _sentinel_session = connector._session
    logger.info("[Sentinel] Session stored for HLS proxy")

    cameras = connector.fetch_cameras()
    if not cameras:
        raise HTTPException(502, "Sentinel returned no cameras — catalogue fetch failed")

    if max_cameras > 0:
        cameras = cameras[:max_cameras]

    started = []
    registered = []
    for cam in cameras:
        cam_id     = f"SENTINEL-{cam['id'].upper()}"
        stream_url = connector.hls_url(cam["id"]) if use_hls else connector.rtsp_url(cam["id"])
        dept       = "Home Department"

        onboard_data = {
            "id":           cam_id,
            "name":         cam["name"],
            "department":   dept,
            "sub_type":     "Sentinel Live Feed",
            "district":     cam.get("location", "Gujarat"),
            "lat":          cam["lat"] or 23.02,
            "lon":          cam["lon"] or 72.57,
            "address":      cam["location"],
            "vendor":       "Sentinel Corp8 / GUIVIN",
            "model_name":   "Live Feed (cctv.corp8.cloud)",
            "camera_type":  "IP/Fixed",
            "resolution":   "1080p",
            "ir_capable":   True,
            "protocol":     "HLS" if use_hls else "RTSP",
            "stream_url":   stream_url,
            "anpr_capable": True,
            "face_capable": False,
            "night_capable": True,
        }
        try:
            add_camera(db, onboard_data)
            registered.append(cam_id)
        except Exception:
            pass  # already registered on reconnect

        stream_manager.add_stream(cam_id, stream_url, dept)
        started.append({
            "camera_id":  cam_id,
            "name":       cam["name"],
            "location":   cam["location"],
            "stream_url": stream_url,
            "protocol":   "HLS" if use_hls else "RTSP",
        })

    await ws_manager.broadcast({
        "type":            "sentinel_connected",
        "cameras_total":   len(cameras),
        "cameras_started": len(started),
        "protocol":        "HLS" if use_hls else "RTSP",
    })

    return {
        "connected":          True,
        "host":               "cctv.corp8.cloud",
        "email":              email,
        "protocol":           "HLS" if use_hls else "RTSP",
        "cameras_fetched":    len(cameras),
        "cameras_registered": len(registered),
        "streams_started":    started,
        "note": (
            "Streams are live. Open the Live Monitor tab and click a camera to view. "
            "RTSP requires direct IP access to 103.250.160.189:8554. "
            "If on a restricted network, retry with use_hls=true."
        ),
    }


# ════════════════════════════════════════════════════════════════════════════════
# ── HLS PROXY (bypasses CORS — backend fetches cctv.corp8.cloud with session) ──
# ════════════════════════════════════════════════════════════════════════════════

@app.get("/api/proxy/hls/{camera_id}/index.m3u8", tags=["Proxy"])
def proxy_hls_manifest(camera_id: str):
    """
    Proxy the HLS .m3u8 manifest for a Sentinel camera through our backend.
    Rewrites all segment URLs to point back to /api/proxy/hls/{id}/{seg}
    so every subsequent TS segment request also goes through our CORS-free proxy.
    """
    if not _sentinel_session:
        raise HTTPException(403, "No active Sentinel session — connect first via /api/sentinel/connect")

    cam_id_lower = camera_id.lower().replace("sentinel-", "")  # cam01 from SENTINEL-CAM01
    upstream_url  = f"https://cctv.corp8.cloud/{cam_id_lower}/index.m3u8"

    try:
        resp = _sentinel_session.get(upstream_url, timeout=10)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"Upstream returned {resp.status_code}")
    except Exception as e:
        raise HTTPException(502, f"HLS manifest fetch failed: {e}")

    # Rewrite relative .ts / .m3u8 URLs → our proxy path
    import re
    lines = []
    for line in resp.text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            # Segments or sub-playlists: strip any leading slashes or relative directory prefixes
            clean_seg = stripped.split('/')[-1]
            lines.append(f"/api/proxy/hls/{camera_id}/{clean_seg}")
        elif 'URI="' in line:
            def _sub_uri(m):
                u = m.group(1).split('/')[-1]
                return f'URI="/api/proxy/hls/{camera_id}/{u}"'
            lines.append(re.sub(r'URI="([^"]+)"', _sub_uri, line))
        else:
            lines.append(line)

    rewritten = "\n".join(lines)
    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        }
    )


@app.get("/api/proxy/hls/{camera_id}/{segment:path}", tags=["Proxy"])
def proxy_hls_segment(camera_id: str, segment: str):
    """
    Proxy a single HLS .ts segment for a Sentinel camera.
    """
    if not _sentinel_session:
        raise HTTPException(403, "No active Sentinel session")

    cam_id_lower = camera_id.lower().replace("sentinel-", "")
    upstream_url  = f"https://cctv.corp8.cloud/{cam_id_lower}/{segment}"

    try:
        resp = _sentinel_session.get(upstream_url, timeout=15, stream=True)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, f"Segment fetch failed: {resp.status_code}")
    except Exception as e:
        raise HTTPException(502, f"HLS segment fetch failed: {e}")

    return StreamingResponse(
        resp.iter_content(chunk_size=16384),
        media_type="video/MP2T",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Cache-Control": "no-cache",
        }
    )


# ════════════════════════════════════════════════════════════════════════════════
# ── WEBSOCKET ─────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
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
    return {
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat(),
        "system": "GUIVIN v1.0",
        "cameras_registered": db.query(CameraDB).count(),
        "alerts_total": db.query(AlertDB).count(),
        "watchlist_entries": db.query(WatchlistDB).filter(WatchlistDB.active == True).count(),
        "blockchain_blocks": db.query(BlockchainLedgerDB).count(),
        "active_streams": len(stream_manager.active_cameras()),
    }


# ── Serve frontend ────────────────────────────────────────────────────────────
import os
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_dir, "static")), name="static")

    @app.get("/", include_in_schema=False)
    def serve_index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))
