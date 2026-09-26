"""Bounded local PaddleOCR reader for FastALPR's detected plate crops."""
import math
from pathlib import Path
from .plate_text import assemble_plate

MODEL_NAMES = ('PP-OCRv5_mobile_det', 'en_PP-OCRv4_mobile_rec')
MODEL_FILES = ('inference.json', 'inference.pdiparams', 'inference.yml')


def read_result(result):
    polygons, texts, scores = (result[k] for k in ('rec_polys', 'rec_texts', 'rec_scores'))
    if not len(polygons) == len(texts) == len(scores):
        raise ValueError('Unaligned PaddleOCR output')
    parts = []
    for polygon, text, score in zip(polygons, texts, scores):
        score = float(score)
        if not isinstance(text, str) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Invalid PaddleOCR confidence or text')
        if len(polygon) != 4 or any(len(p) != 2 for p in polygon):
            raise ValueError('Invalid PaddleOCR polygon')
        if any(not math.isfinite(float(v)) for p in polygon for v in p):
            raise ValueError('Nonfinite PaddleOCR polygon')
        parts.append((polygon, text, score))
    return assemble_plate(parts)


class PaddlePlateReader:
    def __init__(self, root):
        root = Path(root)
        if not all((root / name / file).is_file() for name in MODEL_NAMES for file in MODEL_FILES):
            raise FileNotFoundError('Prepare local PaddleOCR assets first')
        from paddleocr import PaddleOCR
        self.engine = PaddleOCR(text_detection_model_name=MODEL_NAMES[0],
            text_recognition_model_name=MODEL_NAMES[1],
            text_detection_model_dir=str(root / MODEL_NAMES[0]),
            text_recognition_model_dir=str(root / MODEL_NAMES[1]),
            device='cpu', cpu_threads=2, enable_mkldnn=False,
            use_doc_orientation_classify=False, use_doc_unwarping=False,
            use_textline_orientation=False)

    def predict(self, crop):
        if crop.size == 0:
            return None
        import cv2
        from fast_alpr.base import OcrResult
        height, width = crop.shape[:2]
        factor = min(480 / width, 256 / height)
        resized = cv2.resize(crop, (max(1, round(width*factor)), max(1, round(height*factor))),
                            interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA)
        image = cv2.copyMakeBorder(resized, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
        results = list(self.engine.predict(image, text_det_limit_side_len=960, text_det_limit_type='max'))
        if len(results) != 1:
            raise ValueError('Expected one PaddleOCR result')
        text, confidence = read_result(results[0])
        return OcrResult(text=text, confidence=confidence)
