"""Scoped cases and explicit judiciary evidence grants."""
import json
import uuid
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .database import get_db, CaseDB, CaseAlertDB, CaseAccessDB, CaseAssignmentDB, AlertDB, CameraDB, AuditDB
from .auth import principal, users
from .permissions import role
from .time_utils import utc_iso
router = APIRouter(prefix='/api/cases', tags=['Cases'])

class CreateCase(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    alert_id: str = Field(min_length=1, max_length=80)
    model_config = {'extra':'forbid'}
class ChangeCase(BaseModel):
    status: Literal['OPEN','ESCALATED','CLOSED']
    assigned_unit: str = Field(default='', max_length=160)
    note: str = Field(default='', max_length=1000)
    model_config = {'extra':'forbid'}
class LinkAlert(BaseModel):
    alert_id: str = Field(min_length=1, max_length=80)
class GrantAccess(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    enabled: bool = True

def payload(row):
    return dict(id=row.id,title=row.title,department=row.department,status=row.status,
        assigned_unit=row.assigned_unit,created_by=row.created_by,
        created_at=utc_iso(row.created_at),updated_at=utc_iso(row.updated_at))
def find_case(db, case_id):
    row=db.get(CaseDB,case_id)
    if not row: raise HTTPException(404,'Case not found')
    return row
def audit(db,row,action,details):
    db.add(AuditDB(actor=principal.get()['username'],department=row.department,
        action=action,resource=f'/api/cases/{row.id}',details=json.dumps(details)))
def visible_alert(db,alert_id):
    alert=db.get(AlertDB,alert_id)
    camera=db.get(CameraDB,alert.camera_id) if alert else None
    if not alert or not camera: raise HTTPException(404,'Alert not found')
    return alert,camera

@router.get('')
def list_cases(limit:int=Query(default=100,ge=1,le=500),db:Session=Depends(get_db)):
    return [payload(row) for row in db.query(CaseDB).order_by(CaseDB.updated_at.desc()).limit(limit)]
@router.post('')
def create_case(req:CreateCase,db:Session=Depends(get_db)):
    alert,camera=visible_alert(db,req.alert_id)
    if not req.title.strip(): raise HTTPException(422,'Title cannot be blank')
    row=CaseDB(id='CASE-'+uuid.uuid4().hex[:16].upper(),title=req.title.strip(),
        department=camera.department,created_by=principal.get()['username'])
    db.add(row)
    db.flush()
    db.add(CaseAlertDB(case_id=row.id,alert_id=alert.id))
    audit(db,row,'CASE_CREATE',{'alert_id':alert.id})
    db.commit()
    return payload(row)
@router.get('/{case_id}')
def case_detail(case_id:str,db:Session=Depends(get_db)):
    row=find_case(db,case_id)
    ids=[link.alert_id for link in db.query(CaseAlertDB).filter_by(case_id=case_id)]
    alerts=db.query(AlertDB).filter(AlertDB.id.in_(ids)).all()
    return dict(payload(row),alerts=[dict(id=a.id,camera_id=a.camera_id,origin=a.origin,
        timestamp=utc_iso(a.timestamp),status=a.status,evidence_url=f'/api/evidence/{a.id}' if a.frame_path else '',
        clip_status_url=f'/api/evidence/{a.id}/clip') for a in alerts])
@router.post('/{case_id}/alerts')
def add_case_alert(case_id:str,req:LinkAlert,db:Session=Depends(get_db)):
    row=find_case(db,case_id)
    alert,camera=visible_alert(db,req.alert_id)
    if camera.department!=row.department: raise HTTPException(422,'Case alerts must belong to the same department')
    if row.status=='CLOSED': raise HTTPException(409,'Reopen the case before adding evidence')
    if not db.get(CaseAlertDB,(case_id,alert.id)):
        db.add(CaseAlertDB(case_id=case_id,alert_id=alert.id))
        audit(db,row,'CASE_LINK_ALERT',{'alert_id':alert.id})
        row.updated_at=datetime.utcnow()
        db.commit()
    return {'linked':True}
@router.patch('/{case_id}')
def change_case(case_id:str,req:ChangeCase,db:Session=Depends(get_db)):
    row=find_case(db,case_id)
    before={'status':row.status,'assigned_unit':row.assigned_unit}
    row.status,row.assigned_unit,row.updated_at=req.status,req.assigned_unit.strip(),datetime.utcnow()
    audit(db,row,'CASE_UPDATE',{'before':before,'after':req.model_dump()})
    db.commit()
    return payload(row)
@router.post('/{case_id}/access')
def grant_legal_access(case_id:str,req:GrantAccess,db:Session=Depends(get_db)):
    row=find_case(db,case_id)
    account=users().get(req.username)
    if not account or role(account)!='judiciary': raise HTTPException(422,'An existing judiciary account is required')
    grant=db.get(CaseAccessDB,(case_id,req.username))
    if req.enabled and not grant: db.add(CaseAccessDB(case_id=case_id,username=req.username))
    elif not req.enabled and grant: db.delete(grant)
    audit(db,row,'CASE_ACCESS',req.model_dump())
    db.commit()
    return {'username':req.username,'enabled':req.enabled}


@router.get('/{case_id}/assignments')
def case_assignments(case_id: str, db: Session = Depends(get_db)):
    find_case(db, case_id)
    return [row.username for row in db.query(CaseAssignmentDB).filter_by(case_id=case_id)]


@router.post('/{case_id}/assignments')
def assign_supervisor(case_id: str, req: GrantAccess, db: Session = Depends(get_db)):
    row = find_case(db, case_id)
    account = users().get(req.username)
    if req.enabled:
        if not account or role(account) != 'sector_supervisor':
            raise HTTPException(422, 'An existing sector supervisor account is required')
        if account.get('department') not in ('*', row.department):
            raise HTTPException(422, 'Supervisor must belong to the case department')
        camera_ids = {a.camera_id for a in db.query(AlertDB).join(CaseAlertDB, CaseAlertDB.alert_id == AlertDB.id).filter(CaseAlertDB.case_id == case_id)}
        if not camera_ids or not camera_ids.issubset(set(account.get('camera_ids', []))):
            raise HTTPException(422, 'Supervisor camera scope must cover all current case alerts')
    assignment = db.get(CaseAssignmentDB, (case_id, req.username))
    if req.enabled and not assignment:
        db.add(CaseAssignmentDB(case_id=case_id, username=req.username))
    elif not req.enabled and assignment:
        db.delete(assignment)
    audit(db, row, 'CASE_ASSIGN_SUPERVISOR', req.model_dump())
    db.commit()
    return {'username': req.username, 'enabled': req.enabled}
