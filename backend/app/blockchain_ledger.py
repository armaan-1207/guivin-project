"""
GUIVIN — Local Blockchain Ledger (Hyperledger Fabric Simulation)
Simulates a permissioned blockchain for evidence anchoring.
Each block contains: block_number, previous_hash, tx_id, alert_id,
clip_hash (SHA-256 of evidence clip), timestamp, and block_hash.
"""
import hashlib
import json
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from .database import BlockchainLedgerDB
from .config import BLOCKCHAIN_ORG, BLOCKCHAIN_CHANNEL


def _compute_block_hash(block_number: int, previous_hash: str, tx_id: str,
                         alert_id: str, clip_hash: str, timestamp: str) -> str:
    """SHA-256 of block contents — simulates Hyperledger block hash."""
    payload = f"{block_number}:{previous_hash}:{tx_id}:{alert_id}:{clip_hash}:{timestamp}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _compute_clip_hash(clip_path: str) -> str:
    """SHA-256 hash of video clip bytes. Returns placeholder hash if file missing."""
    from pathlib import Path
    p = Path(clip_path)
    if p.exists():
        return hashlib.sha256(p.read_bytes()).hexdigest()
    # For demo: hash of the path string as placeholder
    return hashlib.sha256(clip_path.encode()).hexdigest()


def anchor_evidence(db: Session, alert_id: str, camera_id: str,
                    clip_path: str, event_type: str, risk_score: int,
                    department: str) -> dict:
    """
    Anchor an alert's evidence clip to the local blockchain ledger.
    Returns the block record dict.
    """
    # Compute clip hash
    clip_hash = _compute_clip_hash(clip_path)

    # Get previous block for chain linkage
    last_block = db.query(BlockchainLedgerDB).order_by(
        BlockchainLedgerDB.block_number.desc()
    ).first()

    block_number = (last_block.block_number + 1) if last_block else 1
    previous_hash = last_block.block_hash if last_block else ("0" * 64)
    tx_id = f"TX-{uuid.uuid4().hex[:16].upper()}"
    timestamp_str = datetime.utcnow().isoformat()

    block_hash = _compute_block_hash(
        block_number, previous_hash, tx_id,
        alert_id, clip_hash, timestamp_str
    )

    record = BlockchainLedgerDB(
        block_number=block_number,
        tx_id=tx_id,
        alert_id=alert_id,
        camera_id=camera_id,
        clip_hash=clip_hash,
        event_type=event_type,
        risk_score=risk_score,
        department=department,
        anchored_at=datetime.utcnow(),
        channel=BLOCKCHAIN_CHANNEL,
        org=BLOCKCHAIN_ORG,
        previous_hash=previous_hash,
        block_hash=block_hash,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    print(f"[Blockchain] Block #{block_number} anchored | Alert: {alert_id} | Hash: {clip_hash[:16]}...")
    return _block_to_dict(record)


def verify_evidence(db: Session, alert_id: str, clip_path: str = "") -> dict:
    """
    Verify that a clip's current hash matches what was anchored on the chain.
    Returns verification result.
    """
    block = db.query(BlockchainLedgerDB).filter(
        BlockchainLedgerDB.alert_id == alert_id
    ).first()

    if not block:
        return {"verified": False, "reason": "Alert ID not found on chain", "alert_id": alert_id}

    # Recompute current hash
    if clip_path:
        current_hash = _compute_clip_hash(clip_path)
    else:
        current_hash = block.clip_hash   # No clip path → trust stored hash for demo

    integrity_ok = (current_hash == block.clip_hash)

    return {
        "verified": integrity_ok,
        "alert_id": alert_id,
        "block_number": block.block_number,
        "tx_id": block.tx_id,
        "anchored_at": block.anchored_at.isoformat(),
        "stored_hash": block.clip_hash,
        "current_hash": current_hash,
        "channel": block.channel,
        "org": block.org,
        "chain_status": "INTACT" if integrity_ok else "TAMPERED",
        "verification_time": datetime.utcnow().isoformat(),
    }


def get_ledger(db: Session, limit: int = 50) -> list:
    records = db.query(BlockchainLedgerDB).order_by(
        BlockchainLedgerDB.block_number.desc()
    ).limit(limit).all()
    return [_block_to_dict(r) for r in records]


def get_block_by_alert(db: Session, alert_id: str) -> Optional[dict]:
    block = db.query(BlockchainLedgerDB).filter(
        BlockchainLedgerDB.alert_id == alert_id
    ).first()
    return _block_to_dict(block) if block else None


def _block_to_dict(b: BlockchainLedgerDB) -> dict:
    return {
        "block_number": b.block_number,
        "tx_id": b.tx_id,
        "alert_id": b.alert_id,
        "camera_id": b.camera_id,
        "clip_hash": b.clip_hash,
        "event_type": b.event_type,
        "risk_score": b.risk_score,
        "department": b.department,
        "anchored_at": b.anchored_at.isoformat() if b.anchored_at else "",
        "channel": b.channel,
        "org": b.org,
        "previous_hash": b.previous_hash,
        "block_hash": b.block_hash,
    }
