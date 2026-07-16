"""有限、可解釋且 deterministic 的 OCR preprocessing variants。"""

from dataclasses import dataclass
from typing import Dict, List, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class OcrPreprocessingConfig:
    upscale_factor: float = 2.0
    max_width: int = 1800
    padding_px: int = 24
    clahe_clip_limit: float = 2.5
    dark_panel_blur_sigma: float = 11.0
    dark_panel_foreground_gain: float = 1.65
    dark_panel_background_gain: float = -0.65
    dark_panel_offset: float = 24.0
    adaptive_block_size: int = 31
    adaptive_c: int = 7


DEFAULT_PREPROCESSING_CONFIG = OcrPreprocessingConfig()


def _upscale(image: np.ndarray, config: OcrPreprocessingConfig) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(config.upscale_factor, config.max_width / float(width))
    if scale <= 1.0:
        return image.copy()
    return cv2.resize(
        image,
        (int(round(width * scale)), int(round(height * scale))),
        interpolation=cv2.INTER_CUBIC,
    )


def _pad(image: np.ndarray, pixels: int, value) -> np.ndarray:
    return cv2.copyMakeBorder(
        image, pixels, pixels, pixels, pixels, cv2.BORDER_CONSTANT, value=value
    )


def _normalize_dark_panel(
    gray: np.ndarray, config: OcrPreprocessingConfig
) -> np.ndarray:
    """移除半透明暗板的低頻亮度起伏，同時保留白色短文字邊緣。"""
    background = cv2.GaussianBlur(
        gray,
        (0, 0),
        sigmaX=config.dark_panel_blur_sigma,
        sigmaY=config.dark_panel_blur_sigma,
    )
    return cv2.addWeighted(
        gray,
        config.dark_panel_foreground_gain,
        background,
        config.dark_panel_background_gain,
        config.dark_panel_offset,
    )


def build_preprocessing_variants(
    crop: np.ndarray,
    event_type: str,
    config: OcrPreprocessingConfig = DEFAULT_PREPROCESSING_CONFIG,
) -> List[Tuple[str, List[str], float, np.ndarray]]:
    if crop.size == 0:
        raise ValueError("OCR ROI crop 不可為空")
    enlarged = _upscale(crop, config)
    color = _pad(enlarged, config.padding_px, (24, 24, 24))

    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    denoised = cv2.fastNlMeansDenoising(gray, None, 7, 7, 21)
    contrast_input = (
        _normalize_dark_panel(denoised, config)
        if event_type == "TRIGGER_NOTIFICATION"
        else denoised
    )
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit, tileGridSize=(8, 8)
    ).apply(contrast_input)
    sharpened = cv2.filter2D(
        clahe,
        -1,
        np.asarray([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32),
    )
    clahe_variant = _pad(sharpened, config.padding_px, 24)

    hsv = cv2.cvtColor(enlarged, cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(hsv, np.asarray((0, 0, 165)), np.asarray((179, 105, 255)))
    white_mask = cv2.morphologyEx(
        white_mask, cv2.MORPH_OPEN, np.ones((2, 2), dtype=np.uint8)
    )
    white_on_light = _pad(255 - white_mask, config.padding_px, 255)

    adaptive = cv2.adaptiveThreshold(
        clahe,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        config.adaptive_block_size,
        config.adaptive_c,
    )
    adaptive = cv2.medianBlur(adaptive, 3)
    adaptive_variant = _pad(adaptive, config.padding_px, 255)

    context_operations = (
        ["dark_panel_normalization"]
        if event_type == "TRIGGER_NOTIFICATION"
        else []
    )
    return [
        (
            "color_upscale",
            ["original_roi_crop", "upscale", "padding"],
            1.0,
            color,
        ),
        (
            "clahe_sharpen",
            [
                "grayscale",
                "denoise",
                *context_operations,
                "clahe",
                "sharpen",
                "padding",
            ],
            0.95,
            clahe_variant,
        ),
        (
            "white_text_mask",
            ["hsv", "low_saturation_highlight_mask", "denoise", "invert", "padding"],
            0.85,
            white_on_light,
        ),
        (
            "adaptive_binary",
            ["grayscale", "clahe", "adaptive_threshold", "denoise", "padding"],
            0.8,
            adaptive_variant,
        ),
    ]
