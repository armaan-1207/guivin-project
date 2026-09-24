"""
GUIVIN — SQLAlchemy Database Setup (SQLite)
"""
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, 'timeout': 30} if DATABASE_URL.startswith('sqlite') else {})
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
    location_known  = Column(Boolean, default=True, nullable=False)
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
    ownership = Column(String, default='')
    storage_details = Column(String, default='')
    maintenance_status = Column(String, default='UNKNOWN')
    coverage_radius_m = Column(Float, default=0)
    archived = Column(Boolean, default=False)
    # ONVIF Profile T/source capability metadata. These fields are declarative
    # until a camera capability probe verifies them; they are never inferred
    # from a missing stream or from a UI-only assumption.
    onvif_profile = Column(String, default='UNKNOWN')
    codec = Column(String, default='')
    metadata_streaming = Column(Boolean, default=False)
    motion_tamper_events = Column(Boolean, default=False)
    https_streaming = Column(Boolean, default=False)
    ptz_capable = Column(Boolean, default=False)
    conformance_status = Column(String, default='UNKNOWN')
    source_freshness_seconds = Column(Float, default=0)


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
    origin = Column(String, default='SIMULATED')
    plate_confidence = Column(Float, default=0)
    watchlist_source = Column(String, default='')


class ClipEvidenceDB(Base):
    __tablename__ = 'clip_evidence'
    alert_id = Column(String, primary_key=True)
    camera_id = Column(String, index=True)
    status = Column(String, default='PENDING')
    reason = Column(String, default='')
    path = Column(String, default='')
    started_at = Column(DateTime)
    ended_at = Column(DateTime)
    frame_count = Column(Integer, default=0)
    pre_seconds = Column(Float, default=0)
    post_seconds = Column(Float, default=0)


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
    origin = Column(String, default='SIMULATED')
    track_id = Column(String, default='')
    raw_plate = Column(String, default='')
    raw_plate_confidence = Column(Float, nullable=True)
    plate_localization = Column(String, default='')
    plate_rectified = Column(Boolean, default=False)


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
    schema_version = Column(Integer, default=2)
    evidence_path = Column(String, default='')
    evidence_type = Column(String, default='NONE')
    byte_length = Column(Integer, default=0)


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


class ACIObservationDB(Base):
    __tablename__ = 'aci_observations'
    id = Column(String, primary_key=True)  # camera + UTC minute; one sample/minute
    camera_id = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    vehicle_count = Column(Integer)
    person_count = Column(Integer)
    dwell_seconds = Column(Float)
    origin = Column(String)


class ACIProfileDB(Base):
    __tablename__ = 'aci_profiles'
    id = Column(String, primary_key=True)
    camera_id = Column(String, index=True)
    status = Column(String, default='CANDIDATE')
    features = Column(Text)
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    approved_by = Column(String, default='')
    approved_at = Column(DateTime)


class CameraLinkDB(Base):
    __tablename__ = 'camera_links'
    source_id = Column(String, primary_key=True)
    target_id = Column(String, primary_key=True)
    min_seconds = Column(Float, nullable=False)
    max_seconds = Column(Float, nullable=False)
    enabled = Column(Boolean, default=True)
    updated_by = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CaseDB(Base):
    __tablename__ = 'cases'
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    department = Column(String, nullable=False, index=True)
    status = Column(String, default='OPEN')
    assigned_unit = Column(String, default='')
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class CaseAlertDB(Base):
    __tablename__ = 'case_alerts'
    case_id = Column(String, primary_key=True)
    alert_id = Column(String, primary_key=True)


class SceneRulesDB(Base):
    __tablename__ = 'scene_rules'
    camera_id = Column(String, primary_key=True)
    version = Column(Integer, default=1)
    rules = Column(Text, default='[]')
    updated_at = Column(DateTime, default=datetime.utcnow)


class CaseAssignmentDB(Base):
    __tablename__ = 'case_assignments'
    case_id = Column(String, primary_key=True)
    username = Column(String, primary_key=True)


class SupervisorReviewDB(Base):
    __tablename__ = 'supervisor_reviews'
    id = Column(String, primary_key=True)
    alert_id = Column(String, nullable=False, index=True)
    camera_id = Column(String, nullable=False, index=True)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    status = Column(String, default='PENDING')
    reviewed_by = Column(String, default='')
    note = Column(Text, default='')
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime)


class CaseAccessDB(Base):
    __tablename__ = 'case_access'
    case_id = Column(String, primary_key=True)
    username = Column(String, primary_key=True)


class AuditDB(Base):
    __tablename__ = 'audit_log'
    id = Column(Integer, primary_key=True)
    actor = Column(String)
    department = Column(String, default='*')
    action = Column(String)
    resource = Column(String)
    details = Column(Text, default='')
    timestamp = Column(DateTime, default=datetime.utcnow)


def init_db():
    # Additive, repeatable migration: existing records are explicitly legacy/simulated.
    # Back up an existing SQLite database before changing its schema.
    import sqlite3
    from pathlib import Path
    additions = {
        'cameras': {
            'ownership': "VARCHAR DEFAULT ''", 'storage_details': "VARCHAR DEFAULT ''",
            'maintenance_status': "VARCHAR DEFAULT 'UNKNOWN'", 'coverage_radius_m': 'FLOAT DEFAULT 0',
            'archived': 'BOOLEAN DEFAULT 0', 'onvif_profile': "VARCHAR DEFAULT 'UNKNOWN'",
            'codec': "VARCHAR DEFAULT ''", 'metadata_streaming': 'BOOLEAN DEFAULT 0',
            'motion_tamper_events': 'BOOLEAN DEFAULT 0', 'https_streaming': 'BOOLEAN DEFAULT 0',
            'ptz_capable': 'BOOLEAN DEFAULT 0', 'conformance_status': "VARCHAR DEFAULT 'UNKNOWN'",
            'source_freshness_seconds': 'FLOAT DEFAULT 0',
            'location_known': 'BOOLEAN NOT NULL DEFAULT 1'
        },
        'alerts': {'origin': "VARCHAR DEFAULT 'SIMULATED'", 'plate_confidence': 'FLOAT DEFAULT 0', 'watchlist_source': "VARCHAR DEFAULT ''"},
        'detections': {
            'origin': "VARCHAR DEFAULT 'SIMULATED'", 'track_id': "VARCHAR DEFAULT ''",
            'raw_plate_confidence': 'FLOAT', 'raw_plate': "VARCHAR DEFAULT ''", 'plate_localization': "VARCHAR DEFAULT ''",
            'plate_rectified': 'BOOLEAN DEFAULT 0'
        },
        'blockchain_ledger': {'schema_version': 'INTEGER DEFAULT 1', 'evidence_path': "VARCHAR DEFAULT ''", 'evidence_type': "VARCHAR DEFAULT 'NONE'", 'byte_length': 'INTEGER DEFAULT 0'},
    }
    inspector = inspect(engine)
    changes = [(table, name, definition) for table, columns in additions.items() if inspector.has_table(table)
               for name, definition in columns.items() if name not in {c['name'] for c in inspector.get_columns(table)}]
    if changes and engine.dialect.name == 'sqlite' and engine.url.database != ':memory:':
        path = Path(engine.url.database)
        backup = path.with_suffix('.db.pre-upgrade.bak')
        if not backup.exists():
            with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
                source.backup(target)
    with engine.begin() as connection:
        for table, name, definition in changes:
            connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
            if table == 'cameras' and name == 'location_known':
                connection.execute(text("UPDATE cameras SET location_known = 0 WHERE id LIKE 'SENTINEL-%' AND vendor = 'Sentinel Corp8 / GUIVIN' AND lat = 23.02 AND lon = 72.57"))
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    from .auth import principal
    db.info['department'] = (principal.get() or {}).get('department', '*')
    db.info['principal'] = principal.get() or {}
    try:
        yield db
    finally:
        db.close()


# Scope every ORM select performed by an API session, including counts and exports.
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

@event.listens_for(Session, 'do_orm_execute')
def department_scope(state):
    dept = state.session.info.get('department', '*')
    if not state.is_select or state.execution_options.get('integrity_scan'):
        return
    from .permissions import role
    who = state.session.info.get('principal', {})
    statement = state.statement
    if role(who) == 'judiciary':
        username = who['username']
        cases = select(CaseAccessDB.case_id).where(CaseAccessDB.username == username)
        alerts = select(CaseAlertDB.alert_id).where(CaseAlertDB.case_id.in_(cases))
        statement = statement.options(with_loader_criteria(CaseDB, CaseDB.id.in_(cases), include_aliases=True))
        statement = statement.options(with_loader_criteria(CaseAlertDB, CaseAlertDB.case_id.in_(cases), include_aliases=True))
        statement = statement.options(with_loader_criteria(AlertDB, AlertDB.id.in_(alerts), include_aliases=True))
        for model in (ClipEvidenceDB, BlockchainLedgerDB):
            statement = statement.options(with_loader_criteria(model, model.alert_id.in_(alerts), include_aliases=True))
        state.statement = statement
        return
    if dept == '*' and role(who) != 'sector_supervisor':
        return
    camera_condition = CameraDB.department == dept if dept != '*' else CameraDB.id.is_not(None)
    if role(who) == 'sector_supervisor':
        camera_condition = camera_condition & CameraDB.id.in_(who.get('camera_ids', []))
    cameras = select(CameraDB.id).where(camera_condition)
    statement = statement.options(with_loader_criteria(CameraDB, camera_condition, include_aliases=True))
    for model in (AlertDB, DetectionDB, ACIBaselineDB, ClipEvidenceDB, ACIObservationDB, ACIProfileDB, SupervisorReviewDB, SceneRulesDB):
        statement = statement.options(with_loader_criteria(model, model.camera_id.in_(cameras), include_aliases=True))
    statement = statement.options(with_loader_criteria(BlockchainLedgerDB, BlockchainLedgerDB.camera_id.in_(cameras), include_aliases=True))
    statement = statement.options(with_loader_criteria(AuditDB, AuditDB.department == dept, include_aliases=True))
    case_condition = CaseDB.department == dept
    if role(who) == 'sector_supervisor':
        assignments = select(CaseAssignmentDB.case_id).where(CaseAssignmentDB.username == who['username'])
        case_condition = (CaseDB.created_by == who['username']) | CaseDB.id.in_(assignments)
        # Core tables intentionally inspect all links: ORM camera filters would
        # hide inaccessible links and make the all-evidence check ineffective.
        links, alerts_table = CaseAlertDB.__table__, AlertDB.__table__
        outside = select(links.c.case_id).join(alerts_table, links.c.alert_id == alerts_table.c.id).where(~alerts_table.c.camera_id.in_(cameras))
        inside = select(links.c.case_id).join(alerts_table, links.c.alert_id == alerts_table.c.id).where(alerts_table.c.camera_id.in_(cameras))
        case_condition = case_condition & CaseDB.id.not_in(outside) & CaseDB.id.in_(inside)
        if dept != '*':
            case_condition = case_condition & (CaseDB.department == dept)
    cases = select(CaseDB.id).where(case_condition)
    statement = statement.options(with_loader_criteria(CaseDB, case_condition, include_aliases=True))
    statement = statement.options(with_loader_criteria(CaseAlertDB, CaseAlertDB.case_id.in_(cases), include_aliases=True))
    statement = statement.options(with_loader_criteria(CameraLinkDB,
        CameraLinkDB.source_id.in_(cameras) & CameraLinkDB.target_id.in_(cameras), include_aliases=True))
    state.statement = statement
