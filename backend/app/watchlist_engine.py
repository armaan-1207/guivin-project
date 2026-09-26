"""
GUIVIN — Watchlist Engine
Mock integration of VAHAN / eGujCop / CCTNS / Missing Persons databases.
"""
import json
import re
from typing import Optional, List
from sqlalchemy.orm import Session

from .database import WatchlistDB
from .config import DATA_DIR
from .time_utils import utc_iso


# ── Indian plate normaliser ──────────────────────────────────────────────────
_PLATE_RE = re.compile(r'[^A-Z0-9]')

def normalise_plate(raw: str) -> str:
    """Uppercase, strip non-alphanumeric, format as GJ-01-AB-1234."""
    cleaned = _PLATE_RE.sub('', raw.upper())
    # Try to insert dashes in standard Indian format (2-2-2-4)
    if len(cleaned) >= 8:
        return f"{cleaned[:2]}-{cleaned[2:4]}-{cleaned[4:6]}-{cleaned[6:]}"
    return cleaned


def seed_watchlist_if_empty(db: Session):
    if db.query(WatchlistDB).count() == 0:
        wl_file = DATA_DIR / "watchlist.json"
        if wl_file.exists():
            entries = json.loads(wl_file.read_text(encoding="utf-8"))
            for e in entries:
                db.add(WatchlistDB(**e))
            db.commit()
            print(f"[WatchlistEngine] Seeded {len(entries)} watchlist entries")


from .vahan_mock import lookup_vahan

def check_plate(db: Session, plate_raw: str) -> Optional[dict]:
    """Check if a plate number is in any watchlist or VAHAN. Returns match or None."""
    plate = normalise_plate(plate_raw)
    plate_nodash = plate.replace('-', '')

    # 1. External Database Mock (VAHAN / CCTNS)
    vahan_info = lookup_vahan(plate_nodash)
    
    # 2. Local Watchlist DB
    entry = (
        db.query(WatchlistDB)
        .filter(
            WatchlistDB.active == True,
            WatchlistDB.entry_type == "VEHICLE",
        )
        .all()
    )
    
    matched = None
    for e in entry:
        db_id = normalise_plate(e.identifier)
        if db_id == plate or db_id.replace('-', '') == plate_nodash:
            matched = {
                "matched": True,
                "identifier": e.identifier,
                "normalised": plate,
                "reason": e.reason,
                "source_db": e.source_db,
                "owner_name": e.owner_name,
                "additional_info": e.additional_info,
                "priority": e.priority,
            }
            break

    # If local DB matched, return that. If VAHAN flagged it, return VAHAN.
    if matched:
        return matched
        
    if vahan_info.get("stolen_flag") or vahan_info.get("wanted_flag"):
        return {
            "matched": True,
            "identifier": plate_nodash,
            "normalised": plate,
            "reason": vahan_info.get("alert_context", "Flagged by CCTNS/VAHAN"),
            "source_db": "VAHAN/CCTNS-MOCK",
            "owner_name": vahan_info.get("owner", "Unknown"),
            "additional_info": f"DEMO MOCK DATA — Registration: {vahan_info.get('registration_status', 'Unknown')}",
            "priority": "CRITICAL" if vahan_info.get("stolen_flag") else "HIGH",
        }
        
    return None


def get_all_watchlist(db: Session) -> List[dict]:
    entries = db.query(WatchlistDB).filter(WatchlistDB.active == True).all()
    return [
        {
            "id": e.id,
            "identifier": e.identifier,
            "entry_type": e.entry_type,
            "reason": e.reason,
            "source_db": e.source_db,
            "owner_name": e.owner_name,
            "additional_info": e.additional_info,
            "priority": e.priority,
            "added_at": utc_iso(e.added_at) if e.added_at else "",
        }
        for e in entries
    ]


def add_watchlist_entry(db: Session, data: dict) -> dict:
    entry = WatchlistDB(**data)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "identifier": entry.identifier, "added": True}
