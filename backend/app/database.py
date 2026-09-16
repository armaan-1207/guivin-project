"""
GUIVIN — SQLAlchemy Database Setup (SQLite)
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ─── ORM Models ────────────────────────────────────────────────────────────────

class CameraDB(Base):
    __tablename__ = "cameras"
    id              = Column(String, primary_key=True, index=True)
    name            = Column(String, nullable=False)
    department      = Column(String, nullable=False)
    sub_type        = Column(String, default="Surveillance")
    district        = Column(String, nullable=False)
    lat             = Column(Float, nullable=False)
    lon             = Column(Float, nullable=False)
    address         = Column(String, default="")
    vendor          = Column(String, default="Unknown")
    model_name      = Column(String, default="Unknown")
    camera_type     = Column(String, default="IP/Fixed")
    resolution      = Column(String, default="2MP")
    ir_capable      = Column(Boolean, default=True)
    protocol        = Column(String, default="RTSP")
    stream_url      = Column(String, default="")
    anpr_capable    = Column(Boolean, default=False)
    face_capable    = Column(Boolean, default=False)
    night_capable   = Column(Boolean, default=True)
    cert_fingerprint= Column(String, default="")
    health_status   = Column(String, default="OPERATIONAL")  # OPERATIONAL/OFFLINE/DEGRADED
    aci_baseline_version = Column(String, default="v1.0")
    blockchain_registered = Column(Boolean, default=True)
    onboarded_at    = Column(DateTime, default=datetime.utcnow)


class AlertDB(Base):
    __tablename__ = "alerts"
    id              = Column(String, primary_key=True, index=True)
    camera_id       = Column(String, index=True)
    alert_type      = Column(String)          # WATCHLIST_MATCH / INTRUSION / LOITERING / ACI_ANOMALY / CAMERA_HEALTH
    severity        = Column(String)          # HIGH / MEDIUM / LOW
    risk_score      = Column(Integer)
    plate_number    = Column(String, default="")
    object_class    = Column(String, default="vehicle")
    reason_codes    = Column(Text, default="")   # JSON list
    clip_path       = Column(String, default="")
    frame_path      = Column(String, default="")
    clip_hash       = Column(String, default="")
    block_id        = Column(String, default="")
    blockchain_tx   = Column(String, default="")
    status          = Column(String, default="NEW")   # NEW / ACKNOWLEDGED / VERIFIED / FALSE_ALARM
    acknowledged_by = Column(String, default="")
    timestamp       = Column(DateTime, default=datetime.utcnow, index=True)
    confidence      = Column(Float, default=0.0)
    lat             = Column(Float, default=0.0)
    lon             = Column(Float, default=0.0)


class DetectionDB(Base):
    __tablename__ = "detections"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    camera_id       = Column(String, index=True)
    timestamp       = Column(DateTime, default=datetime.utcnow, index=True)
    pts_ms          = Column(Float, default=0.0)
    object_class    = Column(String)
    confidence      = Column(Float)
    plate_number    = Column(String, default="", index=True)
    plate_confidence= Column(Float, default=0.0)
    bbox_x          = Column(Float, default=0.0)
    bbox_y          = Column(Float, default=0.0)
    bbox_w          = Column(Float, default=0.0)
    bbox_h          = Column(Float, default=0.0)
    frame_path      = Column(String, default="")
    alert_id        = Column(String, default="", index=True)


class WatchlistDB(Base):
    __tablename__ = "watchlist"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    entry_type      = Column(String)    # VEHICLE / PERSON
    identifier      = Column(String, index=True)  # plate number or person ID
    reason          = Column(String)    # STOLEN / WANTED / MISSING / BLACKLISTED
    source_db       = Column(String, default="VAHAN")
    owner_name      = Column(String, default="")
    additional_info = Column(Text, default="")
    priority        = Column(String, default="HIGH")
    added_at        = Column(DateTime, default=datetime.utcnow)
    active          = Column(Boolean, default=True)


class BlockchainLedgerDB(Base):
    __tablename__ = "blockchain_ledger"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    block_number    = Column(Integer, unique=True)
    tx_id           = Column(String, unique=True)
    alert_id        = Column(String, index=True)
    camera_id       = Column(String)
    clip_hash       = Column(String)       # SHA-256 of video clip
    event_type      = Column(String)
    risk_score      = Column(Integer)
    department      = Column(String)
    anchored_at     = Column(DateTime, default=datetime.utcnow)
    channel         = Column(String, default="evidence-integrity")
    org             = Column(String, default="SCRB-Gujarat")
    previous_hash   = Column(String, default="")
    block_hash      = Column(String, default="")


class ACIBaselineDB(Base):
    __tablename__ = "aci_baselines"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    camera_id           = Column(String, index=True, unique=True)
    avg_vehicles_per_hour = Column(Float, default=0.0)
    avg_persons_per_hour  = Column(Float, default=0.0)
    dominant_direction    = Column(String, default="bidirectional")
    avg_dwell_seconds     = Column(Float, default=5.0)
    peak_hours            = Column(String, default="[8,9,17,18]")   # JSON
    night_occupancy_rate  = Column(Float, default=0.05)
    version               = Column(String, default="v1.0")
    validated             = Column(Boolean, default=True)
    validated_by          = Column(String, default="system")
    created_at            = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
