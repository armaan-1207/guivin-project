"""
GUIVIN — Sentinel Sandbox Connector (cctv.corp8.cloud)
Handles authentication, camera catalogue fetch, and stream connection.

API summary (from cctv.corp8.cloud/resource):
  Catalogue:  GET https://cctv.corp8.cloud/cameras.json  (session cookie needed)
  HLS:        https://cctv.corp8.cloud/<id>/index.m3u8   (CDN, works anywhere)
  RTSP:       rtsp://<email%40domain>:<password>@103.250.160.189:8554/stream/<id>
  WebRTC:     http://<email%40domain>:<password>@103.250.160.189:8889/stream/<id>/whep

Usage:
  connector = SentinelConnector(email="you@example.com", password="XXXX-XXXX-XXXX")
  cameras = connector.fetch_cameras()
  rtsp_url = connector.rtsp_url("cam01")
  hls_url  = connector.hls_url("cam01")
"""

import os
import requests
import json
import logging
from urllib.parse import quote
from typing import List, Dict, Optional

logger = logging.getLogger("guivin.sentinel")

SENTINEL_HOST  = "cctv.corp8.cloud"
SENTINEL_IP    = "103.250.160.189"
RTSP_PORT      = 8554
WEBRTC_PORT    = 8889
HLS_BASE       = f"https://{SENTINEL_HOST}"
CATALOGUE_URL  = f"https://{SENTINEL_HOST}/cameras.json"
LOGIN_URL      = f"https://{SENTINEL_HOST}/auth/login"


class SentinelConnector:
    """
    Authenticates with cctv.corp8.cloud and exposes camera catalogue + stream URLs.
    """

    def __init__(self, email: str, password: str):
        self.email    = email
        self.password = password
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "GUIVIN/1.0 AI-Pipeline"})
        self._cameras: List[Dict] = []
        self._logged_in = False

    # ── Auth ──────────────────────────────────────────────────────────────────
    def login(self) -> bool:
        """POST credentials and store session cookie."""
        try:
            resp = self._session.post(
                LOGIN_URL,
                data={"email": self.email, "password": self.password},
                allow_redirects=True,
                timeout=15,
            )
            if resp.status_code == 200 and "auth/login" not in resp.url:
                self._logged_in = True
                logger.info(f"[Sentinel] Logged in as {self.email}")
                return True
            logger.error(f"[Sentinel] Login failed — status {resp.status_code}, URL {resp.url}")
            return False
        except Exception as e:
            logger.error(f"[Sentinel] Login error: {e}")
            return False

    # ── Camera catalogue ──────────────────────────────────────────────────────
    def fetch_cameras(self) -> List[Dict]:
        """
        Fetch the live camera catalogue from cameras.json.
        Returns list of camera dicts with keys: id, name, location, hls_url, rtsp_url.
        """
        if not self._logged_in:
            if not self.login():
                return []

        try:
            resp = self._session.get(CATALOGUE_URL, timeout=15)
            if resp.status_code != 200:
                logger.error(f"[Sentinel] cameras.json returned {resp.status_code}")
                return []

            raw = resp.json()
            # The API may return a list or a dict with a "cameras" key
            if isinstance(raw, dict):
                raw = raw.get("cameras", raw.get("feeds", []))

            cameras = []
            for cam in raw:
                cam_id = cam.get("id") or cam.get("camera_id") or cam.get("name", "").lower().replace(" ", "")
                name   = cam.get("name") or cam.get("location") or cam_id
                loc    = cam.get("location") or cam.get("address") or name
                lat    = float(cam.get("lat", 0) or cam.get("latitude", 0) or 0)
                lon    = float(cam.get("lon", 0) or cam.get("longitude", 0) or 0)

                cameras.append({
                    "id":       cam_id,
                    "name":     name,
                    "location": loc,
                    "lat":      lat,
                    "lon":      lon,
                    "hls_url":  self.hls_url(cam_id),
                    "rtsp_url": self.rtsp_url(cam_id),
                    "raw":      cam,
                })

            self._cameras = cameras
            logger.info(f"[Sentinel] Fetched {len(cameras)} cameras")
            return cameras

        except Exception as e:
            logger.error(f"[Sentinel] fetch_cameras error: {e}")
            return []

    # ── URL builders ──────────────────────────────────────────────────────────
    def rtsp_url(self, camera_id: str) -> str:
        """
        RTSP URL for AI inference (OpenCV/GStreamer). Requires direct IP access.
        Email @ must be percent-encoded as %40.
        rtsp://<email%40domain>:<password>@103.250.160.189:8554/stream/<id>
        """
        encoded_email = quote(self.email, safe="")  # encodes @ → %40
        return (
            f"rtsp://{encoded_email}:{self.password}"
            f"@{SENTINEL_IP}:{RTSP_PORT}/stream/{camera_id}"
        )

    def hls_url(self, camera_id: str) -> str:
        """
        HLS URL — works via CDN from anywhere (laptop, cloud, restricted network).
        Requires session cookie; for programmatic use embed password in URL.
        """
        return f"{HLS_BASE}/{camera_id}/index.m3u8"

    def webrtc_url(self, camera_id: str) -> str:
        """WebRTC/WHEP URL for browser preview."""
        encoded_email = quote(self.email, safe="")
        return (
            f"http://{encoded_email}:{self.password}"
            f"@{SENTINEL_IP}:{WEBRTC_PORT}/stream/{camera_id}/whep"
        )

    # ── OpenCV connection snippet ─────────────────────────────────────────────
    def opencv_connect(self, camera_id: str, use_hls: bool = False):
        """
        Returns an open cv2.VideoCapture for a given camera.
        use_hls=True  → CDN HLS  (works anywhere, slight latency)
        use_hls=False → RTSP/TCP (direct IP, best for AI inference)
        """
        import cv2
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        url = self.hls_url(camera_id) if use_hls else self.rtsp_url(camera_id)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            logger.warning(
                f"[Sentinel] {camera_id}: RTSP failed, falling back to HLS"
            )
            cap = cv2.VideoCapture(self.hls_url(camera_id), cv2.CAP_FFMPEG)
        return cap

    # ── Bulk ingest into GUIVIN ───────────────────────────────────────────────
    def ingest_into_guivin(self, db, start_streams: bool = True,
                            max_cameras: int = 5) -> List[str]:
        """
        Fetch all cameras → register in GUIVIN camera registry → start RTSP streams.
        Returns list of started camera IDs.

        max_cameras: limit to avoid overwhelming the local machine during demo.
                     Set to 0 for all 30.
        """
        from .camera_registry import add_camera
        from .stream_manager import stream_manager

        cameras = self.fetch_cameras()
        if not cameras:
            logger.error("[Sentinel] No cameras fetched — check credentials")
            return []

        if max_cameras > 0:
            cameras = cameras[:max_cameras]

        started = []
        for cam in cameras:
            cam_id   = f"SENTINEL-{cam['id'].upper()}"
            rtsp_url = cam["rtsp_url"]

            # Register in camera registry
            cam_data = {
                "id":          cam_id,
                "name":        cam["name"],
                "department":  "Home Department",
                "sub_type":    "Sentinel Feed",
                "district":    _infer_district(cam["location"]),
                "lat":         cam["lat"] or _default_lat(cam["location"]),
                "lon":         cam["lon"] or _default_lon(cam["location"]),
                "address":     cam["location"],
                "vendor":      "Sentinel Corp8",
                "model_name":  "Live Feed",
                "camera_type": "IP/PTZ",
                "resolution":  "1080p",
                "ir_capable":  True,
                "protocol":    "RTSP",
                "stream_url":  rtsp_url,
                "anpr_capable": True,
                "face_capable": False,
                "night_capable": True,
            }
            try:
                add_camera(db, cam_data)
            except Exception:
                pass  # already exists

            # Start stream
            if start_streams:
                stream_manager.add_stream(cam_id, rtsp_url, "Home Department")
                started.append(cam_id)
                logger.info(f"[Sentinel] Started stream: {cam_id} → {rtsp_url}")

        return started


# ── Helpers ───────────────────────────────────────────────────────────────────
_DISTRICT_MAP = {
    "ahmedabad": (23.0225, 72.5714),
    "surat":     (21.1702, 72.8311),
    "vadodara":  (22.3072, 73.1812),
    "rajkot":    (22.3039, 70.8022),
    "gandhinagar": (23.2156, 72.6369),
    "junagadh":  (21.5222, 70.4579),
    "navsari":   (20.9467, 72.9520),
    "patan":     (23.8493, 72.1266),
    "morbi":     (22.8173, 70.8378),
    "bilimora":  (20.7714, 72.9591),
}

def _infer_district(location: str) -> str:
    loc_lower = location.lower()
    for key in _DISTRICT_MAP:
        if key in loc_lower:
            return key.title()
    return "Gujarat"

def _default_lat(location: str) -> float:
    loc_lower = location.lower()
    for key, (lat, lon) in _DISTRICT_MAP.items():
        if key in loc_lower:
            return lat
    return 22.96  # Gujarat centre

def _default_lon(location: str) -> float:
    loc_lower = location.lower()
    for key, (lat, lon) in _DISTRICT_MAP.items():
        if key in loc_lower:
            return lon
    return 72.60  # Gujarat centre
