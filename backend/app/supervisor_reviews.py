"""Independent review of high-severity alert dismissal; no external dispatch."""
import json
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .database import get_db, SupervisorReviewDB, AuditDB, CameraDB
from .auth import principal
from .time_utils import utc_iso

router = APIRouter(prefix='/api/reviews', tags=['Supervisor review'])

class ReviewDecision(BaseModel):
    decision: Literal['CONFIRMED', 'REOPEN']
    note: str = Field(min_length=1, max_length=1000)

@router.get('')
def reviews(limit: int = Query(default=100, ge=1, le=500),
            status: Literal['PENDING','CONFIRMED','REOPEN'] = 'PENDING', db: Session = Depends(get_db)):
    return [dict(id=r.id, alert_id=r.alert_id, camera_id=r.camera_id, actor=r.actor,
        action=r.action, status=r.status, reviewed_by=r.reviewed_by, note=r.note,
        created_at=utc_iso(r.created_at)) for r in db.query(SupervisorReviewDB).filter_by(status=status)
        .order_by(SupervisorReviewDB.created_at.desc()).limit(limit)]

@router.post('/{review_id}')
def decide(review_id: str, req: ReviewDecision, db: Session = Depends(get_db)):
    from .database import AlertDB
    row = db.get(SupervisorReviewDB, review_id)
    if not row:
        raise HTTPException(404, 'Review not found')
    actor = principal.get()['username']
    if actor == row.actor:
        raise HTTPException(403, 'A different supervisor must review this action')
    if row.status != 'PENDING':
        raise HTTPException(409, 'This review is already resolved')
    if not req.note.strip():
        raise HTTPException(422, 'A review note is required')
    alert = db.get(AlertDB, row.alert_id)
    if not alert:
        raise HTTPException(404, 'Alert not found')
    if req.decision == 'REOPEN':
        if alert.status != row.action:
            raise HTTPException(409, 'Alert status changed; review the current alert before reopening')
        alert.status, alert.acknowledged_by = 'NEW', ''
    row.status, row.reviewed_by = req.decision, actor
    row.note, row.reviewed_at = req.note.strip(), datetime.utcnow()
    camera = db.get(CameraDB, row.camera_id)
    db.add(AuditDB(actor=actor, department=camera.department, action='SUPERVISOR_REVIEW',
        resource=f'/api/reviews/{row.id}', details=json.dumps(req.model_dump())))
    db.commit()
    return {'id': row.id, 'status': row.status, 'alert_status': alert.status}
