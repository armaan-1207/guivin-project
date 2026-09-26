import zipfile, io, cv2, numpy as np
import os
os.environ['HOME'] = '/tmp'
os.environ['PADDLE_PDX_CACHE_HOME'] = '/tmp/.paddlex'
import sys
from pathlib import Path
sys.path.insert(0, '/app')
sys.path.insert(0, '/app/tools')
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from compare_paddleocr import create_engine, bounded_crop, observation
from evaluate_bapatla import enhance_plate_clahe

engine = create_engine("/host_tmp/paddle-evaluation/.paddlex/official_models")
z = zipfile.ZipFile("/app/tools/number_plate.zip")
sample_files = [
    ("images/AN1.png", "AN01P9687"),
    ("images/AN2.png", "AN01D4153"),
    ("images/AN4.png", "AN01M4M"),
    ("images/AN5.png", "AN01L6155"),
    ("images/AN6.png", "AN01H1689"),
    ("images/AN7.png", "AN01H0908"),
    ("images/AN10.png", "AN01K9412"),
    ("images/AP1.png", "AP05BY7799"),
    ("images/AP10.png", "AP39E1493"),
    ("images/AP12.png", "AP31OU7562"),
]

exact = 0
for fname, gt in sample_files:
    raw_bytes = z.read(fname)
    img = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_COLOR)
    res = list(engine.predict(img))
    obs = observation(res[0]) if res else {'plate': '', 'confidence': 0.0}
    is_match = (obs['plate'] == gt)
    if is_match:
        exact += 1
    print(f"{fname:20} -> Pred: {obs['plate']:12} | GT: {gt:12} | Match: {is_match}")

print(f"\nExact matches: {exact}/{len(sample_files)} ({exact/len(sample_files)*100:.1f}%)")
