"""Image processing service for smart cropping and text enhancement.

Uses OpenCV for document detection and contrast enhancement so that
photos of exam sheets (taken on desks, with faint pencil/gray text)
produce clean, high-contrast images suitable for OCR and PDF generation.
"""

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

    # --- Try contour-based document detection ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Adaptive thresholding helps with varied lighting
    edges = cv2.Canny(blurred, 30, 120)
    # Dilate to close gaps in edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=2)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_quad = None
    best_area = 0

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        area = cv2.contourArea(cnt)
        # Candidate must cover at least 15% of the image
        if area < img_area * 0.15:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4 and area > best_area:
            best_quad = approx.reshape(4, 2).astype("float32")
            best_area = area

    # Generous margin (% of detected dimensions) to avoid cutting border content
    margin_ratio = 0.03  # 3% extra on each side

    if best_quad is not None:
        # Perspective transform to straighten the document
        rect = _order_points(best_quad)
        tl, tr, br, bl = rect

        width_a = np.linalg.norm(br - bl)
        width_b = np.linalg.norm(tr - tl)
        max_w = int(max(width_a, width_b))

        height_a = np.linalg.norm(tr - br)
        height_b = np.linalg.norm(tl - bl)
        max_h = int(max(height_a, height_b))

        if max_w > 100 and max_h > 100:
            # Expand the detected quad outward to keep margins safe
            margin_w = max_w * margin_ratio
            margin_h = max_h * margin_ratio
            center = rect.mean(axis=0)
            expanded = rect.copy()
            for i in range(4):
                direction = rect[i] - center
                norm = np.linalg.norm(direction)
                if norm > 0:
                    expanded[i] = rect[i] + direction / norm * max(margin_w, margin_h)
            # Clamp to image bounds
            expanded[:, 0] = np.clip(expanded[:, 0], 0, w - 1)
            expanded[:, 1] = np.clip(expanded[:, 1], 0, h - 1)

            out_w = int(max_w + 2 * margin_w)
            out_h = int(max_h + 2 * margin_h)
            dst = np.array(
                [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
                dtype="float32",
            )
            matrix = cv2.getPerspectiveTransform(expanded, dst)
            warped = cv2.warpPerspective(img, matrix, (out_w, out_h))
            logger.debug("Smart crop: perspective transform applied ({}x{}) with margin", out_w, out_h)
            return _encode_png(warped)

    # --- Fallback: content-aware bounding box crop with generous padding ---
    thresh = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 10)
    coords = cv2.findNonZero(thresh)
    if coords is not None and len(coords) > 100:
        x, y, bw, bh = cv2.boundingRect(coords)
        # Generous padding: 3% of image size on each side
        pad_x = int(w * margin_ratio) + 20
        pad_y = int(h * margin_ratio) + 20
        x = max(0, x - pad_x)
        y = max(0, y - pad_y)
        bw = min(w - x, bw + 2 * pad_x)
        bh = min(h - y, bh + 2 * pad_y)
        # Only crop if we're removing meaningful area (>10% total)
        if bw < w * 0.90 or bh < h * 0.90:
            cropped = img[y : y + bh, x : x + bw]
            logger.debug("Smart crop: bounding box fallback ({}x{})", bw, bh)
            return _encode_png(cropped)

    # Nothing to crop — return original
    logger.debug("Smart crop: no crop applied")
    return _encode_png(img)


def enhance_text(image_bytes: bytes) -> bytes:
    """Enhance text legibility for AI OCR: bright white paper + dark sharp strokes.

    Strategy:
    1. CLAHE for local contrast (makes faint strokes stand out).
    2. Sigmoid-like tone curve: pushes light pixels → white (paper brightens)
       while pulling dark/mid pixels → darker (strokes intensify).
    3. Light unsharp-mask sharpening to crisp up edges for OCR.
    """
    img = _decode(image_bytes)

    # --- Step 1: CLAHE on lightness channel (gentle) ---
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_ch = clahe.apply(l_ch)

    enhanced = cv2.merge([l_ch, a_ch, b_ch])
    enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

    # --- Step 2: Sigmoid tone curve ---
    # Light pixels (paper, >180) → pushed towards 255 (bright white)
    # Dark/mid pixels (strokes, <160) → pulled towards 0 (darker)
    # This creates a clean "white paper + dark ink" look.
    #
    # f(x) = 255 / (1 + exp(-k * (x - midpoint)))
    # midpoint=160: the transition point between "stroke" and "paper"
    # k=0.04: steepness of the transition
    midpoint = 160
    k = 0.04
    lut = np.array(
        [int(255.0 / (1.0 + np.exp(-k * (i - midpoint)))) for i in range(256)],
        dtype="uint8",
    )
    enhanced = cv2.LUT(enhanced, lut)

    # --- Step 3: Light unsharp mask for edge crispness ---
    blurred = cv2.GaussianBlur(enhanced, (0, 0), sigmaX=1.5)
    # unsharp mask: original + alpha * (original - blurred)
    enhanced = cv2.addWeighted(enhanced, 1.3, blurred, -0.3, 0)

    return _encode_png(enhanced)


def process_image(image_bytes: bytes) -> bytes:
    """Full pipeline: smart crop → text enhancement."""
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
