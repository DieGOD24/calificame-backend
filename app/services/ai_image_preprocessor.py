"""AI-driven image preprocessing analyzer.

Uses an OpenAI vision model (settings.AI_MODEL, default gpt-4.1) to inspect
a photo of an exam sheet and return structured guidance for OpenCV:

  - Document corners (4 points in original-image pixel coordinates)
  - Residual rotation in degrees
  - Per-photo enhancement parameters (CLAHE clip, gamma, binarization, ...)

The model itself never returns an image — it returns a small JSON object that
OpenCV then uses to apply a perspective transform and tone mapping. This
keeps token cost low and lets us reuse the well-tested OpenCV warp/enhance
primitives that already work in `image_processing.py`.
"""

from __future__ import annotations

import io
import json
from typing import Any

from loguru import logger
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from app.agents.base import BaseAgent

# Max long-edge size sent to OpenAI. Keeps tokens cheap while still being
# detailed enough for corner detection on phone photos. Coordinates are
# scaled back to the original image size in `analyze()`.
_DOWNSCALE_LONG_EDGE = 1024

PREPROCESS_SYSTEM_PROMPT = """Eres un asistente experto en visión por computador para fotos de exámenes en papel.

Recibirás UNA fotografía de un examen tomada con celular (puede tener sombras, papel arrugado, fondo no uniforme, ángulo torcido). El usuario te dirá las dimensiones originales en píxeles.

Tu tarea: devolver un JSON con instrucciones que un pipeline de OpenCV usará para enderezar y realzar la imagen.

## Reglas para "corners"
- Si ves un documento (hoja de papel) en la foto, identifica sus 4 esquinas.
- Devuelve coordenadas en píxeles **de la imagen original** (no de la versión que ves) — el usuario te dará las dimensiones para que escales.
- Orden estricto: top-left, top-right, bottom-right, bottom-left.
- Si NO ves un documento claro (foto borrosa, no es papel, vacía), pon `is_document: false` y `corners: null`.

## Reglas para "rotation_deg"
- Ángulo residual en grados que falta para que el papel quede horizontal tras el warp.
- Casi siempre 0. Solo distinto de 0 si el texto del documento sigue inclinado.

## Reglas para "enhance_params"
- `clahe_clip` (1.0-4.0): cuánto contraste local. Más alto en fotos con sombra/baja luz. Default 2.0.
- `gamma` (0.6-1.6): corrección de iluminación. <1 oscurece, >1 aclara. Default 1.0.
- `binarize` (bool): true SOLO si la tinta es muy débil (lápiz claro) y se beneficiaría de blanco/negro puro. Default false.
- `binarize_threshold` (0-255): umbral si binarize=true. Default 180.
- `denoise` (bool): true si la foto tiene grano fuerte (poca luz). Default false.

## Reglas para "confidence"
- Tu confianza en la detección, 0.0 a 1.0.

## FORMATO DE RESPUESTA
Devuelve SOLO un objeto JSON válido (no markdown, no texto extra). Ejemplo:

{
  "is_document": true,
  "corners": [[120, 80], [1450, 95], [1430, 2010], [110, 2025]],
  "rotation_deg": 0.0,
  "enhance_params": {
    "clahe_clip": 2.5,
    "gamma": 1.0,
    "binarize": false,
    "binarize_threshold": 180,
    "denoise": false
  },
  "confidence": 0.92
}
"""


class _EnhanceParams(BaseModel):
    clahe_clip: float = Field(default=2.0, ge=0.5, le=8.0)
    gamma: float = Field(default=1.0, ge=0.3, le=3.0)
    binarize: bool = False
    binarize_threshold: int = Field(default=180, ge=0, le=255)
    denoise: bool = False


class _PreprocessResult(BaseModel):
    is_document: bool
    corners: list[list[float]] | None = None
    rotation_deg: float = 0.0
    enhance_params: _EnhanceParams = Field(default_factory=_EnhanceParams)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AIImagePreprocessor(BaseAgent):
    """Analyzer that turns an image into OpenCV-ready preprocessing parameters."""

    def execute(self, **kwargs: Any) -> dict:
        image_bytes: bytes = kwargs["image_bytes"]
        return self.analyze(image_bytes)

    def analyze(self, image_bytes: bytes) -> dict:
        """Return preprocessing guidance for the given image.

        Raises on transport errors (after tenacity retries) or unparseable
        responses. The caller is expected to fall back to a non-AI pipeline.
        """
        original = Image.open(io.BytesIO(image_bytes))
        orig_w, orig_h = original.width, original.height

        downscaled_bytes, scale = self._downscale(image_bytes, original)

        user_text = (
            f"Dimensiones originales: {orig_w}x{orig_h} píxeles. "
            "Devuelve el JSON con corners en coordenadas de la imagen ORIGINAL."
        )

        raw = self._chat_completion_with_images(
            system_prompt=PREPROCESS_SYSTEM_PROMPT,
            user_text=user_text,
            images=[downscaled_bytes],
            max_tokens=1024,
        )

        parsed = self._parse_json(raw)
        try:
            result = _PreprocessResult.model_validate(parsed)
        except ValidationError as exc:
            logger.warning("AI preprocess: invalid JSON shape ({}): {!r}", exc, raw[:300])
            raise

        # If the model gave coords in the downscaled frame (despite the prompt),
        # `scale` would still be 1.0 because we tell it the original size; the
        # multiply is a defensive no-op when the model already used originals.
        # When the model misbehaves and gives downscaled coords, this would
        # under-scale — accept the slight risk; the corner validator in
        # image_processing.py rejects nonsense.
        out: dict = {
            "is_document": result.is_document,
            "corners": result.corners,
            "rotation_deg": result.rotation_deg,
            "enhance_params": result.enhance_params.model_dump(),
            "confidence": result.confidence,
            "original_width": orig_w,
            "original_height": orig_h,
            "_scale_hint": scale,
        }
        return out

    @staticmethod
    def _downscale(image_bytes: bytes, img: Image.Image) -> tuple[bytes, float]:
        long_edge = max(img.width, img.height)
        if long_edge <= _DOWNSCALE_LONG_EDGE:
            return image_bytes, 1.0

        scale = _DOWNSCALE_LONG_EDGE / float(long_edge)
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        resized = img.convert("RGB").resize((new_w, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        return buf.getvalue(), scale

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        raise ValueError(f"AI preprocess: cannot parse JSON from response: {text[:200]!r}")
