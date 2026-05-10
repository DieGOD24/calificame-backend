"""Tests for the AI-driven image preprocessor.

The OpenAI client is fully mocked — these tests never make a network call.
We exercise both the parser and the downscale path end-to-end.
"""

import io
import json
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.services.ai_image_preprocessor import AIImagePreprocessor


def _make_png_bytes(width: int = 800, height: int = 600, color: str = "white") -> bytes:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _stub_openai(content: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    client.chat.completions.create.return_value = response
    return client


def test_analyze_returns_parsed_dict() -> None:
    payload = {
        "is_document": True,
        "corners": [[10, 10], [780, 12], [770, 580], [12, 590]],
        "rotation_deg": 0.0,
        "enhance_params": {
            "clahe_clip": 2.5,
            "gamma": 1.0,
            "binarize": False,
            "binarize_threshold": 180,
            "denoise": False,
        },
        "confidence": 0.9,
    }
    client = _stub_openai(json.dumps(payload))
    result = AIImagePreprocessor(openai_client=client).analyze(_make_png_bytes())

    assert result["is_document"] is True
    assert result["corners"] == [[10, 10], [780, 12], [770, 580], [12, 590]]
    assert result["rotation_deg"] == 0.0
    assert result["enhance_params"]["clahe_clip"] == 2.5
    assert result["confidence"] == 0.9
    assert result["original_width"] == 800
    assert result["original_height"] == 600


def test_analyze_handles_markdown_fenced_json() -> None:
    payload = {
        "is_document": False,
        "corners": None,
        "rotation_deg": 0.0,
        "enhance_params": {},
        "confidence": 0.1,
    }
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    client = _stub_openai(fenced)
    result = AIImagePreprocessor(openai_client=client).analyze(_make_png_bytes())
    assert result["is_document"] is False
    assert result["corners"] is None


def test_analyze_extracts_object_from_extra_text() -> None:
    payload = {
        "is_document": True,
        "corners": [[1, 1], [100, 1], [100, 100], [1, 100]],
        "rotation_deg": 0.0,
        "enhance_params": {},
        "confidence": 0.5,
    }
    noisy = "Aquí va el JSON:\n" + json.dumps(payload) + "\n¡Listo!"
    client = _stub_openai(noisy)
    result = AIImagePreprocessor(openai_client=client).analyze(_make_png_bytes())
    assert result["is_document"] is True


def test_analyze_raises_on_unparseable_json() -> None:
    client = _stub_openai("no es json en absoluto")
    with pytest.raises(ValueError):
        AIImagePreprocessor(openai_client=client).analyze(_make_png_bytes())


def test_analyze_raises_on_invalid_shape() -> None:
    # Missing required `is_document` field, pydantic should fail.
    client = _stub_openai(json.dumps({"corners": None}))
    with pytest.raises(Exception):  # ValidationError subclass of Exception
        AIImagePreprocessor(openai_client=client).analyze(_make_png_bytes())


def test_downscale_large_image_before_send() -> None:
    """A 4000x3000 image should be downscaled before being sent to OpenAI."""
    payload = {
        "is_document": True,
        "corners": [[0, 0], [3999, 0], [3999, 2999], [0, 2999]],
        "rotation_deg": 0.0,
        "enhance_params": {},
        "confidence": 0.99,
    }
    client = _stub_openai(json.dumps(payload))
    big = _make_png_bytes(4000, 3000)
    AIImagePreprocessor(openai_client=client).analyze(big)

    # Capture what was sent: the data URL contains base64-encoded PNG of the
    # downscaled image. We decode it and assert its long edge is <= 1024.
    args, kwargs = client.chat.completions.create.call_args
    user_content = kwargs["messages"][1]["content"]
    image_part = next(p for p in user_content if p.get("type") == "image_url")
    data_url = image_part["image_url"]["url"]
    assert data_url.startswith("data:image/")
    import base64

    b64 = data_url.split(",", 1)[1]
    sent_bytes = base64.b64decode(b64)
    sent_img = Image.open(io.BytesIO(sent_bytes))
    assert max(sent_img.width, sent_img.height) <= 1024


def test_small_image_is_not_downscaled() -> None:
    payload = {
        "is_document": True,
        "corners": [[0, 0], [799, 0], [799, 599], [0, 599]],
        "rotation_deg": 0.0,
        "enhance_params": {},
        "confidence": 0.9,
    }
    client = _stub_openai(json.dumps(payload))
    AIImagePreprocessor(openai_client=client).analyze(_make_png_bytes(800, 600))

    args, kwargs = client.chat.completions.create.call_args
    image_part = next(p for p in kwargs["messages"][1]["content"] if p.get("type") == "image_url")
    import base64

    b64 = image_part["image_url"]["url"].split(",", 1)[1]
    sent_img = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert sent_img.width == 800
    assert sent_img.height == 600
