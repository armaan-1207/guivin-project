"""SQLite stores UTC without offsets; API boundaries always supply an offset."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')

def utc_iso(value):
    if not value:
        return ''
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()

def ist_time(value):
    return datetime.fromisoformat(utc_iso(value)).astimezone(IST)
