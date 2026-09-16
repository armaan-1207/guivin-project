"""
GUIVIN — Gujarat Unified Intelligent Video Intelligence Network
Configuration & Constants
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
EVIDENCE_DIR = BASE_DIR / "evidence"
REPORTS_DIR = BASE_DIR / "reports"

# Ensure dirs exist
for d in [EVIDENCE_DIR / "clips", EVIDENCE_DIR / "frames", REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{BASE_DIR}/guivin.db"

# AI Settings
YOLO_MODEL = "yolov8n.pt"          # Nano model — fast for PoC
DETECTION_INTERVAL = 5             # Process every Nth frame
CONFIDENCE_THRESHOLD = 0.45
PLATE_CONFIDENCE_THRESHOLD = 0.3

# Stream Settings
RTSP_TRANSPORT = "tcp"
RECONNECT_INITIAL_DELAY = 2        # seconds
RECONNECT_MAX_DELAY = 30           # seconds
STREAM_BUFFER_FRAMES = 10

# Blockchain Settings
BLOCKCHAIN_ORG = "SCRB-Gujarat"
BLOCKCHAIN_CHANNEL = "evidence-integrity"

# ACI Settings
ACI_LEARNING_WINDOW_HOURS = 72
ACI_UPDATE_WINDOW_DAYS = 7
ACI_ANOMALY_THRESHOLD = 2.5        # Standard deviations from baseline

# Alert Settings
ALERT_EVIDENCE_PRE_SECONDS = 5
ALERT_EVIDENCE_POST_SECONDS = 10
ALERT_DEDUP_WINDOW_SECONDS = 30

# Sentinel Sandbox
SENTINEL_HOST = os.getenv("SENTINEL_HOST", "")          # Set via env var when sandbox available
SENTINEL_CATALOGUE_PATH = "/api/ingest"

# CORS
ALLOWED_ORIGINS = ["*"]
