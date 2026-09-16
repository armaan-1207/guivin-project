# GUIVIN — Gujarat Unified Intelligent Video Intelligence Network

> **Hackathon:** Sentinel Gujarat | **Problem:** Model 1 — Centralised CCTV Registry + AI Intelligence Layer

GUIVIN is a full-stack AI-powered CCTV analytics platform built on top of the Sentinel Gujarat camera grid. It provides real-time ANPR (Automatic Number Plate Recognition), vehicle journey reconstruction, blockchain-anchored evidence, and an ACI (Anomaly & Context Intelligence) engine — all through a single unified dashboard.

---

## Features

| Feature | Details |
|---|---|
| 📷 GIS Camera Registry | 12+ seed cameras across Gujarat with Leaflet map |
| 🔍 ANPR (Live + Demo) | YOLOv8n + EasyOCR, processes every 5th frame |
| 🚗 Vehicle Journey | Cross-camera journey reconstruction with timestamps |
| 🔗 Blockchain Ledger | SHA-256 tamper-evident chain anchoring every alert |
| ⚠️ Watchlist Engine | VAHAN + eGujCop mock DB, stolen/wanted/blacklisted plates |
| 📊 ACI Baseline | Anomaly scoring per camera based on historical baselines |
| 📡 Live Streams | HLS via proxied backend (CORS-free), MJPEG for local RTSP |
| 🌐 Sentinel Sandbox | Connects to cctv.corp8.cloud — 30 live Gujarat cameras |
| 🔒 Encrypted Evidence | Evidence frames stored with SHA-256 fingerprints |
| 📄 Reports | CSV + PDF report generation |

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/guivin.git
cd guivin/backend

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in your browser.

---

## Connecting to Sentinel Sandbox (cctv.corp8.cloud)

1. Register at [cctv.corp8.cloud](https://cctv.corp8.cloud)
2. Open **Live Monitor** tab → click **Connect Sentinel**
3. Enter your registered email + access password
4. Choose number of cameras (1–30)

Stream URL format used internally:
```
HLS:  https://cctv.corp8.cloud/<id>/index.m3u8  (proxied via /api/proxy/hls/)
RTSP: rtsp://email%40domain:password@103.250.160.189:8554/stream/<id>
```

---

## Project Structure

```
guivin/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI — all routes + WebSocket
│   │   ├── ai_pipeline.py       # YOLOv8n + EasyOCR ANPR
│   │   ├── stream_manager.py    # RTSP/HLS capture threads + MJPEG
│   │   ├── sentinel_connector.py# cctv.corp8.cloud auth + catalogue
│   │   ├── alert_manager.py     # Alert creation + dedup + blockchain
│   │   ├── blockchain_ledger.py # SHA-256 tamper-evident chain
│   │   ├── watchlist_engine.py  # Plate lookup — VAHAN/eGujCop mock
│   │   ├── aci_engine.py        # Anomaly & Context Intelligence
│   │   ├── camera_registry.py   # Camera CRUD + GeoJSON
│   │   ├── report_generator.py  # CSV + PDF reports
│   │   ├── database.py          # SQLAlchemy models
│   │   └── config.py            # Paths + constants
│   ├── data/
│   │   ├── cameras.json         # 12 seed cameras
│   │   ├── watchlist.json       # 8 watchlist entries
│   │   └── aci_baselines.json   # 6 camera ACI baselines
│   └── requirements.txt
├── frontend/
│   ├── index.html               # 6-tab dashboard
│   └── static/
│       ├── css/styles.css
│       └── js/
│           ├── api.js
│           ├── dashboard.js     # Streams + Sentinel connect
│           ├── gis-map.js       # Leaflet GIS
│           ├── alerts.js        # Alert feed
│           ├── journey.js       # Journey reconstruction
│           └── blockchain.js    # Ledger + verify
├── anpr_runner.py               # Standalone CLI ANPR script
├── run.sh                       # One-command startup
└── README.md
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | System health |
| GET | `/api/cameras` | All cameras |
| GET | `/api/cameras/geojson` | GeoJSON for map |
| GET | `/api/alerts` | All alerts |
| POST | `/api/alerts/demo/anpr` | Trigger demo ANPR alert |
| GET | `/api/journey/{plate}` | Vehicle journey |
| GET | `/api/blockchain/ledger` | Blockchain blocks |
| POST | `/api/blockchain/verify` | Verify evidence |
| GET | `/api/watchlist/check/{plate}` | Watchlist lookup |
| POST | `/api/sentinel/connect` | Connect sandbox |
| GET | `/api/proxy/hls/{cam}/index.m3u8` | Proxied HLS stream |
| GET | `/api/reports/csv` | Download CSV report |
| GET | `/api/reports/pdf` | Download PDF report |

Full interactive docs: **http://localhost:8000/docs**

---

## Standalone ANPR

Process any video file or RTSP stream without the full server:

```bash
python anpr_runner.py \
  --source /path/to/video.mp4 \
  --camera-id DEMO-001 \
  --output report.csv
```

---

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy (SQLite) + Uvicorn
- **AI:** YOLOv8n (Ultralytics) + EasyOCR
- **Streams:** OpenCV RTSP/TCP + HLS proxy
- **Security:** SHA-256 blockchain ledger, HMAC evidence signing
- **Frontend:** Vanilla JS + Leaflet.js + hls.js
- **Sandbox:** cctv.corp8.cloud (30 live Gujarat cameras)

---

## Demo Data (pre-seeded)

| Plate | Status | Risk |
|---|---|---|
| GJ-01-AB-1234 | STOLEN | HIGH (70) |
| GJ-01-CD-5678 | WANTED | HIGH (70) |
| MH-12-IJ-7890 | WANTED | HIGH (70) |
| GJ-05-EF-9012 | BLACKLISTED | MEDIUM (55) |

---

## License

MIT — built for Sentinel Gujarat Hackathon 2026.
