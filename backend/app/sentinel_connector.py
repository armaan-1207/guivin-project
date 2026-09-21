"""
GUIVIN — Sentinel Sandbox Connector (cctv.corp8.cloud)
Handles authentication, camera catalogue fetch, and stream connection.

API summary (from cctv.corp8.cloud/resource):
  Catalogue:  GET https://cctv.corp8.cloud/cameras.json  (session cookie needed)
  HLS:        https://cctv.corp8.cloud/<id>/index.m3u8   (browser access restrictions apply)
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
import logging
import math
import re
from urllib.parse import quote, urlsplit
from typing import List, Dict, Optional

logger = logging.getLogger("guivin.sentinel")

SENTINEL_HOST  = "cctv.corp8.cloud"
SENTINEL_IP    = "103.250.160.189"
RTSP_PORT      = 8554
WEBRTC_PORT    = 8889
HLS_BASE       = f"https://{SENTINEL_HOST}"
CATALOGUE_URL  = f"https://{SENTINEL_HOST}/cameras.json"
LOGIN_URL      = f"https://{SENTINEL_HOST}/auth/login"


def validate_camera_id(value):
    """Only permit a single catalogue path component, never a URL or traversal."""
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise ValueError('Invalid Sentinel camera ID')
    return value


def parse_catalogue(raw):
    """Validate the whole catalogue before registration or stream side effects."""
    if isinstance(raw, dict):
        raw = raw.get('cameras', raw.get('feeds'))
    if not isinstance(raw, list):
        raise ValueError('Expected a camera catalogue list')
    cameras, seen = [], set()
    for cam in raw:
        if not isinstance(cam, dict):
            raise ValueError('Invalid camera record')
        cam_id = validate_camera_id(cam.get('id') or cam.get('camera_id'))
        if cam_id.upper() in seen:
            raise ValueError('Duplicate camera ID')
        seen.add(cam_id.upper())
        name = cam.get('name') or cam.get('location') or cam_id
        location = cam.get('location') or cam.get('address') or name
        if not isinstance(name, str) or not isinstance(location, str):
            raise ValueError('Invalid camera label')
        coordinates = {}
        for key, alias, limit in [('lat', 'latitude', 90), ('lon', 'longitude', 180)]:
            value = cam.get(key)
            if value is None:
                value = cam.get(alias)
            if value is None:
                coordinates[key] = None
                continue
            if isinstance(value, bool):
                raise ValueError('Invalid camera coordinate')
            value = float(value)
            if not math.isfinite(value) or abs(value) > limit:
                raise ValueError('Invalid camera coordinate')
            coordinates[key] = value
        cameras.append({'id': cam_id, 'name': name, 'location': location,
                        **coordinates, 'raw': cam})
    return cameras


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
        self.last_error = None

    # ── Auth ──────────────────────────────────────────────────────────────────
    def login(self) -> bool:
        """POST credentials and store session cookie."""
        self._logged_in = False
        self.last_error = None
        try:
            resp = self._session.post(
                LOGIN_URL,
                data={"email": self.email, "password": self.password},
                allow_redirects=False,
                timeout=15,
            )
            # The deployed form redirects successful authentication to the grid.
            # Do not follow arbitrary redirects or mistake an HTML login page for success.
            target = urlsplit(resp.headers.get('Location', ''))
            if (resp.status_code in (302, 303) and target.path == '/'
                    and target.query == '' and target.fragment == ''
                    and ((not target.scheme and not target.netloc)
                         or (target.scheme == 'https' and target.netloc == SENTINEL_HOST))):
                self._logged_in = True
                logger.info('[Sentinel] Authenticated session established')
                return True
            logger.error('[Sentinel] Login failed: HTTP %s', resp.status_code)
            self.last_error = 'authentication'
            return False
        except Exception as e:
            logger.error('[Sentinel] Login error: %s', type(e).__name__)
            self.last_error = 'network' if isinstance(e, requests.RequestException) else 'internal'
            return False

    # ── Camera catalogue ──────────────────────────────────────────────────────
    def fetch_cameras(self) -> List[Dict]:
        """
        Fetch the live camera catalogue from cameras.json.
        Returns list of camera dicts with keys: id, name, location, hls_url, rtsp_url.
        """
        # A failed refresh must never leave the previous catalogue available.
        self._cameras = []
        self.last_error = None
        if not self._logged_in:
            if not self.login():
                return []

        try:
            resp = self._session.get(CATALOGUE_URL, timeout=15, allow_redirects=False)
            if resp.status_code != 200:
                self.last_error = 'authentication' if resp.status_code in (401, 403) else 'catalogue'
                if self.last_error == 'authentication':
                    self._logged_in = False
                logger.error(f"[Sentinel] cameras.json returned {resp.status_code}")
                return []

            cameras = parse_catalogue(resp.json())
            for cam in cameras:
                cam['hls_url'] = self.hls_url(cam['id'])
                cam['rtsp_url'] = self.rtsp_url(cam['id'])

            self._cameras = cameras
            logger.info(f"[Sentinel] Fetched {len(cameras)} cameras")
            return cameras

        except Exception as e:
            self._cameras = []
            self.last_error = 'network' if isinstance(e, requests.RequestException) else 'catalogue'
            logger.error('[Sentinel] Catalogue error: %s', type(e).__name__)
            return []

    # ── URL builders ──────────────────────────────────────────────────────────
    def rtsp_url(self, camera_id: str) -> str:
        """
        RTSP URL for AI inference (OpenCV/GStreamer). Requires direct IP access.
        Email @ must be percent-encoded as %40.
        rtsp://<email%40domain>:<password>@103.250.160.189:8554/stream/<id>
        """
        validate_camera_id(camera_id)
        encoded_email = quote(self.email, safe="")  # encodes @ → %40
        return (
            f"rtsp://{encoded_email}:{quote(self.password, safe='')}"
            f"@{SENTINEL_IP}:{RTSP_PORT}/stream/{camera_id}"
        )

    def hls_url(self, camera_id: str) -> str:
        """
        HLS URL. The deployed service may reject programmatic access even with
        an authenticated session; RTSP is the verified inference transport.
        """
        return f"{HLS_BASE}/{validate_camera_id(camera_id)}/index.m3u8"

    def webrtc_url(self, camera_id: str) -> str:
        """WebRTC/WHEP URL for browser preview."""
        validate_camera_id(camera_id)
        encoded_email = quote(self.email, safe="")
        return (
            f"http://{encoded_email}:{quote(self.password, safe='')}"
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
