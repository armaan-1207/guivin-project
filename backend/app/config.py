"""
GUIVIN — Gujarat Unified Intelligent Video Intelligence Network
Configuration & Constants
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_DIR = Path(os.getenv('GUIVIN_STATE_DIR', str(BASE_DIR)))
STATE_DIR.mkdir(parents=True, exist_ok=True)
EVIDENCE_DIR = STATE_DIR / "evidence"
REPORTS_DIR = STATE_DIR / "reports"

# Ensure dirs exist
for d in [EVIDENCE_DIR / "clips", EVIDENCE_DIR / "frames", REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv('DATABASE_URL', f"sqlite:///{STATE_DIR}/guivin.db")
MODEL_DIR = BASE_DIR.parent / 'tmp' / 'models'
os.environ.setdefault('YOLO_CONFIG_DIR', str(BASE_DIR.parent / 'tmp' / 'yolo'))
os.environ.setdefault('MPLCONFIGDIR', str(BASE_DIR.parent / 'tmp' / 'matplotlib'))
os.environ.setdefault('EASYOCR_MODULE_PATH', str(BASE_DIR.parent / 'tmp' / 'easyocr'))
os.environ.setdefault('PADDLE_HOME', str(BASE_DIR.parent / 'tmp' / '.paddle'))
os.environ.setdefault('PADDLEX_HOME', str(BASE_DIR.parent / 'tmp' / '.paddlex'))
os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'

# AI Settings
YOLO_MODEL = os.getenv('YOLO_MODEL', str(BASE_DIR.parent / 'yolov8n.pt'))
PLATE_MODEL = os.getenv('GUIVIN_PLATE_MODEL', '')
ANPR_BACKEND = os.getenv('GUIVIN_ANPR_BACKEND', 'easyocr').lower()
if ANPR_BACKEND not in {'easyocr', 'fast_alpr'}:
    raise ValueError('GUIVIN_ANPR_BACKEND must be easyocr or fast_alpr')
ANPR_MODEL_DIR = os.getenv('GUIVIN_ANPR_MODEL_DIR', str(BASE_DIR.parent / 'tmp' / 'fast-alpr'))
DETECTION_INTERVAL = 5             # Process every Nth frame
CONFIDENCE_THRESHOLD = 0.45
PLATE_CONFIDENCE_THRESHOLD = 0.3
PLATE_DETECTION_THRESHOLD = 0.25
OCR_MAX_TRACKS_PER_FRAME = max(1, min(20, int(os.getenv('GUIVIN_OCR_MAX_TRACKS', '3'))))
OCR_TRACK_INTERVAL = max(0.0, min(10.0, float(os.getenv('GUIVIN_OCR_INTERVAL', '0.5'))))
OCR_PREPROCESSING = os.getenv('GUIVIN_OCR_PREPROCESSING', 'preserve')
if OCR_PREPROCESSING not in {'legacy', 'preserve'}:
    raise ValueError('GUIVIN_OCR_PREPROCESSING must be legacy or preserve')
# Model setup is explicit and never downloads during startup. Disable only for
# lightweight tooling/tests that intentionally defer model initialization.
WARMUP_MODELS = os.getenv('GUIVIN_WARMUP_MODELS', '1').strip().lower() not in {'0', 'false', 'no'}

# Tracking uses a two-threshold association when enabled. This is a local,
# testable ByteTrack-style association layer, not cross-camera identity.
TRACKER_BACKEND = os.getenv('GUIVIN_TRACKER', 'BYTE_TRACK_LITE').upper()
TRACKER_HIGH_THRESHOLD = float(os.getenv('GUIVIN_TRACKER_HIGH', '0.5'))
TRACKER_LOW_THRESHOLD = float(os.getenv('GUIVIN_TRACKER_LOW', '0.1'))
TRACKER_MAX_AGE_SECONDS = float(os.getenv('GUIVIN_TRACKER_MAX_AGE', '3'))

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
ALLOWED_ORIGINS = [v.strip() for v in os.getenv('GUIVIN_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000').split(',') if v.strip()]
