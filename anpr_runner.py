#!/usr/bin/env python3
"""Headless runner using the same capture, OCR consensus and persistence as the API.
Use a registered camera. Stop the API first when sharing its SQLite database.
"""
import argparse
import csv
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'backend'))
from app.database import init_db, SessionLocal, DetectionDB
from app.camera_registry import get_camera, seed_cameras_if_empty
from app.watchlist_engine import seed_watchlist_if_empty
from app.ai_pipeline import _get_yolo, _get_ocr
from app.stream_manager import CameraWorker
from app.stream_security import validate_source
from app.time_utils import utc_iso

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--camera-id',required=True)
    parser.add_argument('--output',default='GUIVIN_Detections.csv')
    parser.add_argument('--every',type=int,default=5)
    parser.add_argument('--conf',type=float,default=.45)
    parser.add_argument('--max-frames',type=int,default=0)
    args=parser.parse_args()
    if args.every<1 or not 0<=args.conf<=1 or args.max_frames<0:
        parser.error('Invalid interval, confidence or frame limit')
    validate_source(args.source)
    init_db()
    with SessionLocal() as db:
        seed_cameras_if_empty(db)
        seed_watchlist_if_empty(db)
        camera=get_camera(db,args.camera_id)
        last=db.query(DetectionDB.id).order_by(DetectionDB.id.desc()).first()
        start_id=last[0] if last else 0
    if not camera:
        parser.error('Register this camera using the dashboard or API first')
    if _get_yolo() is None or _get_ocr() is None:
        raise SystemExit('Models unavailable. Run tools/setup_models.py --download')
    worker=CameraWorker(args.camera_id,args.source,camera['department'])
    worker.detect_every,worker.conf_threshold,worker.max_frames=args.every,args.conf,args.max_frames
    worker.start()
    try:
        while worker.is_alive():
            time.sleep(.2)
    except KeyboardInterrupt:
        worker.stop()
    if worker.is_alive():
        raise SystemExit('Worker is still stopping; no success reported')
    with SessionLocal() as db, open(args.output,'w',newline='',encoding='utf-8') as output:
        writer=csv.writer(output)
        writer.writerow(['Timestamp UTC (processing time for recordings)','Camera','Vehicle','Plate','OCR confidence','Origin','Track','Alert'])
        query=db.query(DetectionDB).filter(DetectionDB.id>start_id,DetectionDB.camera_id==args.camera_id).order_by(DetectionDB.id)
        count=0
        for row in query:
            values=[utc_iso(row.timestamp),row.camera_id,row.object_class,row.plate_number,row.plate_confidence,row.origin,row.track_id,row.alert_id]
            writer.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in values])
            count+=1
    print(f'Captured {worker.frame_count}, inferred {worker.processed_frames}, dropped {worker.dropped_frames}; exported {count} detections to {Path(args.output).resolve()}')
    if not worker.frame_count or worker.ai_status=='ERROR':
        raise SystemExit('Capture/inference unsuccessful; inspect source and model setup')

if __name__=='__main__':
    main()
