"""Versioned local hash chain; no distributed consensus or external trust anchor."""
import hashlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from .database import BlockchainLedgerDB
from .config import BLOCKCHAIN_ORG, BLOCKCHAIN_CHANNEL
from .time_utils import utc_iso

ledger_lock = threading.RLock()

def _compute_clip_hash(path):
    if not path or not Path(path).is_file():
        raise FileNotFoundError('Evidence file unavailable')
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

def _payload(block):
    fields = ('schema_version', 'block_number', 'previous_hash', 'tx_id', 'alert_id',
              'camera_id', 'clip_hash', 'event_type', 'risk_score', 'department',
              'channel', 'org', 'evidence_type', 'byte_length', 'evidence_path')
    data = {key: getattr(block, key) for key in fields}
    data['anchored_at'] = utc_iso(block.anchored_at)
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')

def anchor_evidence(db, alert_id, camera_id, clip_path, event_type, risk_score, department, commit=True):
    with ledger_lock:
        previous = db.query(BlockchainLedgerDB).execution_options(integrity_scan=True).order_by(BlockchainLedgerDB.block_number.desc()).first()
        exists = bool(clip_path) and Path(clip_path).is_file()
        block = BlockchainLedgerDB(
            schema_version=2, block_number=previous.block_number + 1 if previous else 1,
            previous_hash=previous.block_hash if previous else '0' * 64,
            tx_id='TX-' + uuid.uuid4().hex.upper(), alert_id=alert_id, camera_id=camera_id,
            clip_hash=_compute_clip_hash(clip_path) if exists else '',
            evidence_path=str(Path(clip_path).resolve()) if exists else '',
            evidence_type=('FRAME' if Path(clip_path).suffix.lower() in ('.jpg', '.png') else 'CLIP') if exists else 'NONE',
            byte_length=Path(clip_path).stat().st_size if exists else 0,
            event_type=event_type, risk_score=risk_score, department=department,
            anchored_at=datetime.utcnow(), channel=BLOCKCHAIN_CHANNEL, org=BLOCKCHAIN_ORG)
        block.block_hash = hashlib.sha256(_payload(block)).hexdigest()
        db.add(block)
        db.flush()
        if commit:
            db.commit()
        return _block_to_dict(block)

def verify_evidence(db, alert_id, clip_path='', evidence_type=None):
    query = db.query(BlockchainLedgerDB).filter_by(alert_id=alert_id)
    if evidence_type:
        query = query.filter_by(evidence_type=evidence_type)
    block = query.order_by(BlockchainLedgerDB.block_number).first()
    if not block:
        return {'verified': False, 'reason': 'No ledger record', 'alert_id': alert_id, 'chain_status': 'UNAVAILABLE'}
    result = dict(_block_to_dict(block), verified=False, current_hash='', chain_status='UNVERIFIABLE',
                  stored_hash=block.clip_hash, verification_time=utc_iso(datetime.utcnow()),
                  trust_scope='Local hash chain; no independent external anchor')
    if block.schema_version != 2:
        return dict(result, reason='Legacy simulation record; no verifiable evidence')
    previous_hash, expected_number = '0' * 64, 1
    legacy_prefix, started_v2 = False, False
    for entry in db.query(BlockchainLedgerDB).execution_options(integrity_scan=True).order_by(BlockchainLedgerDB.block_number).all():
        if entry.schema_version != 2:
            if started_v2:
                return dict(result, reason='Legacy record inside version 2 chain')
            legacy_prefix = True
            previous_hash, expected_number = entry.block_hash, entry.block_number + 1
            continue
        started_v2 = True
        if (entry.block_number != expected_number or entry.previous_hash != previous_hash or
                hashlib.sha256(_payload(entry)).hexdigest() != entry.block_hash):
            return dict(result, chain_status='TAMPERED', reason='Ledger hash or linkage mismatch')
        previous_hash, expected_number = entry.block_hash, expected_number + 1
    result['chain_status'] = 'VERSION_2_SUFFIX_INTACT' if legacy_prefix else 'INTACT'
    if legacy_prefix:
        result['trust_scope'] = 'Version 2 suffix and evidence bytes only; legacy prefix unverifiable; no external anchor'
    if block.evidence_type == 'NONE':
        return dict(result, reason='No evidence captured; metadata only', evidence_status='MISSING')
    try:
        current = _compute_clip_hash(block.evidence_path)
    except (OSError, ValueError):
        return dict(result, reason='Evidence file missing or unreadable', evidence_status='MISSING')
    ok = current == block.clip_hash and Path(block.evidence_path).stat().st_size == block.byte_length
    return dict(result, verified=ok, current_hash=current, evidence_status='INTACT' if ok else 'TAMPERED',
                reason='Evidence bytes and local chain match' if ok else 'Evidence hash or size mismatch')

def get_ledger(db, limit=50):
    return [_block_to_dict(b) for b in db.query(BlockchainLedgerDB).order_by(BlockchainLedgerDB.block_number.desc()).limit(limit)]

def get_block_by_alert(db, alert_id):
    block = db.query(BlockchainLedgerDB).filter_by(alert_id=alert_id).first()
    return _block_to_dict(block) if block else None

def _block_to_dict(block):
    data = {key: getattr(block, key) for key in ('block_number', 'tx_id', 'alert_id', 'camera_id', 'clip_hash', 'event_type',
            'risk_score', 'department', 'channel', 'org', 'previous_hash', 'block_hash', 'schema_version', 'evidence_type', 'byte_length')}
    data['anchored_at'] = utc_iso(block.anchored_at)
    return data
