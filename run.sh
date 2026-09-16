#!/usr/bin/env bash
# GUIVIN — Start script
# Run: bash run.sh

set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         GUIVIN — Gujarat Unified Intelligent VIN         ║"
echo "║     Sentinel Police Innovation Challenge 2026            ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

BACKEND_DIR="backend"
VENV_DIR="backend/.venv"

# ── Create venv if not exists ─────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
  echo "[1/3] Creating Python virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

# ── Activate venv ─────────────────────────────────────────────────────────
source "$VENV_DIR/bin/activate"

# ── Install dependencies ──────────────────────────────────────────────────
echo "[2/3] Installing dependencies (first run may take a few minutes)..."
pip install -q --upgrade pip
pip install -q -r "$BACKEND_DIR/requirements.txt"

# ── Start FastAPI server ──────────────────────────────────────────────────
echo "[3/3] Starting GUIVIN backend server..."
echo ""
echo "  Dashboard: http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo "  Health:    http://localhost:8000/api/health"
echo ""
echo "  Press Ctrl+C to stop."
echo ""

cd "$BACKEND_DIR"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level info
