"""Synthetic 1080p bar-blending microbenchmark; not an inference benchmark."""
import json
from statistics import median
from time import perf_counter
import cv2
import numpy as np

frame = np.random.default_rng(42).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)


def previous(image):
    overlay = image.copy()
    cv2.rectangle(overlay, (0, 0), (image.shape[1], 28), (10, 20, 40), -1)
    image[:] = cv2.addWeighted(overlay, .75, image, .25, 0)


def current(image):
    bar = image[:29]
    overlay = np.full_like(bar, (10, 20, 40))
    cv2.addWeighted(overlay, .75, bar, .25, 0, dst=bar)


times = {'previous': [], 'current': []}
for _ in range(100):
    for name, operation in [('previous', previous), ('current', current)]:
        image = frame.copy()
        start = perf_counter()
        operation(image)
        times[name].append((perf_counter() - start) * 1000)
print(json.dumps({name + '_median_ms': round(median(values[10:]), 3)
                  for name, values in times.items()}))
