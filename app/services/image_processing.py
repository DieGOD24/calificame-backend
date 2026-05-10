"""Image processing service for smart cropping and text enhancement.

Two pipelines coexist:

* `process_image` — pure OpenCV (Canny + contour for crop, fixed CLAHE +
  sigmoid + unsharp for enhance). Always available, no network, no cost.
  Used as fallback when AI preprocessing is disabled or fails.

* `process_image_ai` — asks an OpenAI vision model for the document corners
  and per-photo enhancement parameters, then runs the OpenCV warp + a
  parameterized enhancement. This handles real phone photos (shadows,
  wrinkled paper, low contrast backgrounds) much better than the heuristic
  pipeline. On any failure it transparently falls back to `process_image`.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from loguru import logger


def _decode(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image")
    return img


def _encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("Could not encode image to PNG")
    return buf.tobytes()


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    d = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(d)]
    rect[3] = pts[np.argmax(d)]
    return rect


def _warp_with_corners(img: np.ndarray, corners: np.ndarray, margin_ratio: float = 0.03) -> np.ndarray | None:
    """Apply a perspective transform from a 4-corner quadrilateral.

    Returns the warped image, or None if the corners are too small to be
    a meaningful crop (the caller should fall back).
    """
    h, w = img.shape[:2]
    rect = _order_points(corners)
    tl, tr, br, bl = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_w = int(max(width_a, width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_h = int(max(height_a, height_b))

    if max_w <= 100 or max_h <= 100:
        return None

    margin_w = max_w * margin_ratio
    margin_h = max_h * margin_ratio
    center = rect.mean(axis=0)
    expanded = rect.copy()
    for i in range(4):
        direction = rect[i] - center
        norm = np.linalg.norm(direction)
        if norm > 0:
            expanded[i] = rect[i] + direction / norm * max(margin_w, margin_h)
    expanded[:, 0] = np.clip(expanded[:, 0], 0, w - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, h - 1)

    out_w = int(max_w + 2 * margin_w)
    out_h = int(max_h + 2 * margin_h)
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(expanded, dst)
    return cv2.warpPerspective(img, matrix, (out_w, out_h))


def _validate_ai_corners(corners: list[list[float]] | None, img_w: int, img_h: int) -> np.ndarray | None:
    """Return a (4,2) float32 array if corners are sane, else None."""
    if not corners or len(corners) != 4:
        return None
    try:
        arr = np.asarray(corners, dtype="float32").reshape(4, 2)
    except (ValueError, TypeError):
        return None
    if not np.all(np.isfinite(arr)):
        return None
    # Must lie within the image (small tolerance).
    if (arr[:, 0] < -2).any() or (arr[:, 0] > img_w + 2).any():
        return None
    if (arr[:, 1] < -2).any() or (arr[:, 1] > img_h + 2).any():
        return None
    arr[:, 0] = np.clip(arr[:, 0], 0, img_w - 1)
    arr[:, 1] = np.clip(arr[:, 1], 0, img_h - 1)
    # Reject tiny quads (>5% of image area).
    area = cv2.contourArea(arr.astype("float32"))
    if area < img_w * img_h * 0.05:
        return None
    return arr


def smart_crop(image_bytes: bytes) -> bytes:
    """Detect the paper/document in the image and crop to its boundaries.

    Uses Canny edge detection + contour approximation to find the largest
    quadrilateral (= the sheet of paper). If found, applies a perspective
    transform to produce a straight, rectangular crop. Falls back to a
    simple content-aware bounding-box crop if no quadrilateral is found.
    """
    img = _decode(image_bytes)
    h, w = img.shape[:2]
    img_area = h * w

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(blurred, 30, 120)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_quad = None
    best_area = 0

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        area = cv2.contourArea(cnt)
        if area < img_area * 0.15:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4 and area > best_area:
            best_quad = approx.reshape(4, 2).astype("float32")
            best_area = area

    if best_quad is not None:
        warped = _warp_with_corners(img, best_quad)
        if warped is not None:
            logger.debug("Smart crop: perspective transform applied ({}x{})", warped.shape[1], warped.shape[0])
            return _encode_png(warped)

    # Fallback: content-aware bounding box with generous padding.
    margin_ratio = 0.03
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10)
    coords = cv2.findNonZero(thresh)
    if coords is not None and len(coords) > 100:
        x, y, bw, bh = cv2.boundingRect(coords)
        pad_x = int(w * margin_ratio) + 20
        pad_y = int(h * margin_ratio) + 20
        x = max(0, x - pad_x)
        y = max(0, y - pad_y)
        bw = min(w - x, bw + 2 * pad_x)
        bh = min(h - y, bh + 2 * pad_y)
        if bw < w * 0.90 or bh < h * 0.90:
            cropped = img[y : y + bh, x : x + bw]
            logger.debug("Smart crop: bounding box fallback ({}x{})", bw, bh)
            return _encode_png(cropped)

    logger.debug("Smart crop: no crop applied")
    return _encode_png(img)


def _enhance_array(
    img: np.ndarray,
    *,
    clahe_clip: float = 2.0,
    gamma: float = 1.0,
    binarize: bool = False,
    binarize_threshold: int = 180,
    denoise: bool = False,
) -> np.ndarray:
    """Apply text-enhancement pipeline with tunable parameters.

    Pipeline: optional denoise → CLAHE on lightness → gamma correction →
    sigmoid tone curve → light unsharp mask → optional binarization.
    """
    if denoise:
        img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 7, 21)

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=float(clahe_clip), tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)
    enhanced = cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2BGR)

    if abs(gamma - 1.0) > 1e-3:
        inv = 1.0 / max(gamma, 0.05)
        gamma_lut = np.array([((i / 255.0) ** inv) * 255 for i in range(256)], dtype="uint8")
        enhanced = cv2.LUT(enhanced, gamma_lut)

    midpoint = 160
    k = 0.04
    sigmoid_lut = np.array(
        [int(255.0 / (1.0 + np.exp(-k * (i - midpoint)))) for i in range(256)],
        dtype="uint8",
    )
    enhanced = cv2.LUT(enhanced, sigmoid_lut)

    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.5)
    enhanced = cv2.addWeighted(enhanced, 1.3, blurred, -0.3, 0)

    if binarize:
        gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
        _, bw = cv2.threshold(gray, int(binarize_threshold), 255, cv2.THRESH_BINARY)
        enhanced = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)

    return enhanced


def enhance_text(image_bytes: bytes) -> bytes:
    """Enhance text legibility for AI OCR (back-compat wrapper).

    Uses the original parameter set (clahe_clip=2.0, gamma=1.0, no binarize,
    no denoise) so existing tests and the OpenCV-only pipeline stay stable.
    """
    img = _decode(image_bytes)
    enhanced = _enhance_array(img)
    return _encode_png(enhanced)


def _rotate(img: np.ndarray, deg: float) -> np.ndarray:
    h, w = img.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), deg, 1.0)
    return cv2.warpAffine(img, matrix, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def process_image(image_bytes: bytes) -> bytes:
    """Full OpenCV-only pipeline: smart crop → text enhancement."""
    try:
        cropped = smart_crop(image_bytes)
    except Exception as exc:
        logger.warning("Smart crop failed, using original: {}", exc)
        cropped = image_bytes

    try:
        enhanced = enhance_text(cropped)
    except Exception as exc:
        logger.warning("Text enhancement failed, using cropped: {}", exc)
        enhanced = cropped

    return enhanced


def process_image_ai(image_bytes: bytes, preprocessor: Any | None = None) -> bytes:
    """AI-guided pipeline: ask gpt-4.1 for corners + enhance params, then run OpenCV.

    Falls back to `process_image` (OpenCV-only) on any error: model timeout,
    JSON parse error, invalid corners, OpenCV exception, etc.

    `preprocessor` is injectable for tests; defaults to a fresh
    `AIImagePreprocessor()` which lazily builds the OpenAI client.
    """
    try:
        if preprocessor is None:
            from app.services.ai_image_preprocessor import AIImagePreprocessor

            preprocessor = AIImagePreprocessor()
        guidance = preprocessor.analyze(image_bytes)
    except Exception as exc:
        logger.warning("AI preprocess unavailable, falling back to OpenCV: {}", exc)
        return process_image(image_bytes)

    try:
        img = _decode(image_bytes)
        h, w = img.shape[:2]

        if guidance.get("is_document"):
            corners = _validate_ai_corners(guidance.get("corners"), w, h)
            if corners is not None:
                warped = _warp_with_corners(img, corners)
                if warped is not None:
                    img = warped
                else:
                    logger.debug("AI preprocess: corners too small, skipping warp")
            else:
                logger.debug("AI preprocess: corners invalid, skipping warp")

        rotation = float(guidance.get("rotation_deg") or 0.0)
        if abs(rotation) > 0.5:
            img = _rotate(img, rotation)

        params = guidance.get("enhance_params") or {}
        enhanced = _enhance_array(
            img,
            clahe_clip=float(params.get("clahe_clip", 2.0)),
            gamma=float(params.get("gamma", 1.0)),
            binarize=bool(params.get("binarize", False)),
            binarize_threshold=int(params.get("binarize_threshold", 180)),
            denoise=bool(params.get("denoise", False)),
        )
        return _encode_png(enhanced)
    except Exception as exc:
        logger.warning("AI-guided pipeline failed at OpenCV stage, falling back: {}", exc)
        return process_image(image_bytes)
