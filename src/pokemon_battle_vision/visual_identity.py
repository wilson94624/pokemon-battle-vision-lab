"""固定 UI icon／sprite 的可解釋 fingerprint 與相似度。"""

from typing import Any, Dict, Mapping

import cv2
import numpy as np


def visual_fingerprint(image: np.ndarray) -> Dict[str, Any]:
    if image.size == 0:
        return {"dhash": "0" * 16, "hsv_histogram": [0.0] * 24}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0], None, [24], [0, 180]).flatten()
    total = float(hist.sum())
    if total > 0:
        hist /= total
    return {
        "dhash": "{:016x}".format(value),
        "hsv_histogram": [round(float(item), 6) for item in hist],
        "mean_brightness": round(float(hsv[:, :, 2].mean()) / 255.0, 6),
        "mean_saturation": round(float(hsv[:, :, 1].mean()) / 255.0, 6),
    }


def fingerprint_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    left_hash = int(str(left.get("dhash", "0")), 16)
    right_hash = int(str(right.get("dhash", "0")), 16)
    hamming = bin(left_hash ^ right_hash).count("1")
    hash_similarity = 1.0 - hamming / 64.0
    left_hist = np.asarray(left.get("hsv_histogram", []), dtype=np.float32)
    right_hist = np.asarray(right.get("hsv_histogram", []), dtype=np.float32)
    histogram_similarity = 0.0
    if left_hist.size and left_hist.shape == right_hist.shape:
        histogram_similarity = float(
            cv2.compareHist(left_hist, right_hist, cv2.HISTCMP_INTERSECT)
        )
    return round(max(0.0, min(1.0, 0.7 * hash_similarity + 0.3 * histogram_similarity)), 6)


def stable_visual_identity(prefix: str, fingerprint: Mapping[str, Any]) -> str:
    return "{}:{}".format(prefix, str(fingerprint.get("dhash", "0"))[:12])
