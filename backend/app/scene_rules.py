"""Opt-in, operator-defined scene rules on sampled detections.

These rules identify geometric observations, not intent, identity or crime.
Coordinates are normalized bottom-center points. All geometry is camera-specific.
"""
import json
import uuid
import threading
from datetime import datetime
from typing import Annotated, Literal
import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session
from .database import SceneRulesDB, CameraDB, AuditDB, get_db, SessionLocal
from .auth import principal
from .tracking import ByteTrackLite

Coordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

class SceneRule(BaseModel):
    id: str = Field(min_length=1, max_length=60, pattern=r'^[A-Za-z0-9_-]+$')
    kind: Literal['REGION_ENTRY','TRIPWIRE','CROWD','LOITERING']
    points: list[tuple[Coordinate, Coordinate]] = Field(min_length=2, max_length=16)
    object_class: Literal['person','car','truck','bus','motorcycle','bicycle'] = 'person'
    count: int = Field(default=5, ge=2, le=1000)
    dwell_seconds: float = Field(default=30, ge=1, le=3600, allow_inf_nan=False)
    enabled: bool = True
    model_config = {'extra':'forbid'}

    @model_validator(mode='after')
    def geometry(self):
        if self.kind == 'TRIPWIRE':
            if len(self.points) != 2 or self.points[0] == self.points[1]:
                raise ValueError('Tripwire requires two distinct points')
        elif len(self.points) < 3 or abs(cv2.contourArea(np.array(self.points,dtype=np.float32))) < .0001:
            raise ValueError('Region requires a nonzero polygon')
        elif not cv2.isContourConvex(np.array(self.points,dtype=np.float32)):
            raise ValueError('Use a convex region without crossing edges')
        return self

class RuleSet(BaseModel):
    rules: list[SceneRule] = Field(max_length=20)
    expected_version: int = Field(ge=0)

router = APIRouter(prefix='/api/cameras', tags=['Scene rules'])
rules_lock = threading.RLock()

@router.get('/{camera_id}/rules')
def get_rules(camera_id: str, db: Session = Depends(get_db)):
    if not db.get(CameraDB,camera_id):
        raise HTTPException(404,'Camera not found')
    row = db.get(SceneRulesDB,camera_id)
    return {'version':row.version if row else 0,'rules':json.loads(row.rules) if row else []}

@router.put('/{camera_id}/rules')
def set_rules(camera_id: str, req: RuleSet, db: Session = Depends(get_db)):
    with rules_lock:
        return save_rules(camera_id,req,db)

def save_rules(camera_id,req,db):
    camera = db.get(CameraDB,camera_id)
    if not camera:
        raise HTTPException(404,'Camera not found')
    if len({r.id for r in req.rules}) != len(req.rules):
        raise HTTPException(422,'Rule IDs must be unique')
    row = db.get(SceneRulesDB,camera_id)
    version = row.version if row else 0
    if req.expected_version != version:
        raise HTTPException(409,'Rule version changed; reload before saving')
    before = json.loads(row.rules) if row else []
    if not row:
        row = SceneRulesDB(camera_id=camera_id)
        db.add(row)
    after = [r.model_dump() for r in req.rules]
    row.rules, row.version, row.updated_at = json.dumps(after), version + 1, datetime.utcnow()
    db.add(AuditDB(actor=principal.get()['username'],department=camera.department,action='SCENE_RULES',
        resource=f'/api/cameras/{camera_id}/rules',details=json.dumps({'before':before,'after':after,'version':row.version})))
    db.commit()
    return {'version':row.version,'rules':after}

def side(a,b,p):
    return (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0])

def crossed(a,b,old,new):
    return side(a,b,old)*side(a,b,new) < 0 and side(old,new,a)*side(old,new,b) <= 0

class SceneEvaluator:
    def __init__(self):
        self.trackers = {}
        self.state = {}
        self.version = None

    def evaluate(self,rules,detections,shape,now,version):
        if version != self.version:
            self.state.clear()
            self.version = version
        self.state = {k:v for k,v in self.state.items() if now-v['last'] <= 3}
        classes = {r['object_class'] for r in rules if r['enabled']}
        self.trackers = {k:v for k,v in self.trackers.items() if k in classes}
        height,width = shape[:2]
        tracked = {}
        for cls in classes:
            tracker = self.trackers.setdefault(cls,ByteTrackLite())
            selected = [d for d in detections if d['class']==cls and d['confidence'] >= .5]
            tracked[cls] = list(zip(selected,tracker.update(selected,now)))
        events = []
        for rule in rules:
            if not rule['enabled']:
                continue
            occupied = 0
            confidences = []
            for det,track in tracked[rule['object_class']]:
                if not track:
                    continue
                x,y,w,h = det['bbox']
                point = ((x+w/2)/width,(y+h)/height)
                key = (rule['id'],track)
                old = self.state.get(key)
                polygon = np.array(rule['points'],dtype=np.float32)
                inside = rule['kind']!='TRIPWIRE' and cv2.pointPolygonTest(polygon,point,False)>=0
                occupied += int(inside)
                if inside:
                    confidences.append(det['confidence'])
                since = old['since'] if old and old['inside'] and inside else now
                trigger = ((rule['kind']=='REGION_ENTRY' and old and inside and not old['inside']) or
                    (rule['kind']=='TRIPWIRE' and old and crossed(*rule['points'],old['point'],point)) or
                    (rule['kind']=='LOITERING' and inside and now-since>=rule['dwell_seconds'] and not (old and old.get('fired'))))
                self.state[key] = dict(point=point,inside=inside,since=since,last=now,
                    fired=bool(trigger) or bool(old and old.get('fired') and inside))
                if trigger:
                    events.append({'rule':rule,'confidence':det['confidence'],'track_id':track})
            if rule['kind']=='CROWD' and occupied>=rule['count']:
                events.append({'rule':rule,'confidence':min(confidences),'count':occupied})
        return events

    def process(self,camera_id,detections,frame,now,timestamp,origin):
        from .ai_pipeline import save_frame
        from .alert_manager import create_alert
        from pathlib import Path
        result = []
        with SessionLocal() as db:
            row = db.get(SceneRulesDB,camera_id)
            if not row:
                return result
            camera = db.get(CameraDB,camera_id)
            for event in self.evaluate(json.loads(row.rules),detections,frame.shape,now,row.version):
                rule = event['rule']
                path = save_frame(frame,'SCENE-'+uuid.uuid4().hex)
                try:
                    alert = create_alert(db,camera_id,'SCENE_'+rule['kind'],'',rule['object_class'],event['confidence'],
                        40,[{'factor':rule['kind'],'delta':40,'reason':'Configured geometric rule; requires operator review',
                             'rule_id':rule['id'],'rule_version':row.version,'count':event.get('count'),
                             'confidence_basis':'Minimum supporting detector confidence; not calibrated event probability'}],path,
                        camera.lat,camera.lon,camera.department,origin=origin,timestamp=timestamp,
                        dedup_scope='scene:'+rule['id'])
                except Exception:
                    Path(path).unlink(missing_ok=True)
                    raise
                if not alert:
                    Path(path).unlink(missing_ok=True)
                else:
                    result.append({'event':'new_alert','alert':alert,'department':camera.department})
        return result
