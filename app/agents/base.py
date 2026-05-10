from abc import ABC, abstractmethod
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

# Long-edge cap for images sent to OpenAI vision. 1800px keeps handwritten
# text legible while cutting tokens by ~50-65% vs raw phone photos
# (4000-4500px). Anything smaller is passed through unchanged.
_VISION_MAX_EDGE = 1800


class BaseAgent(ABC):
    """Abstract base class for AI agents."""

    def __init__(self, openai_client: OpenAI | None = None) -> None:
        self.client = openai_client or OpenAI(api_key=settings.OPENAI_API_KEY)

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """Execute the agent's task."""
        ...

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((APITimeoutError, APIConnectionError, RateLimitError)),
    )
    def _chat_completion(
        self,
        messages: list[dict],
        model: str = settings.AI_MODEL,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Make a chat completion API call."""
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _to_png(img_bytes: bytes) -> tuple[bytes, str]:
        """Convert any image to PNG for OpenAI compatibility."""
        import io

        from PIL import Image

        try:
            img = Image.open(io.BytesIO(img_bytes))
            # Already a supported format with correct magic bytes
            if img.format in ("PNG", "JPEG", "GIF", "WEBP"):
                fmt = img.format.lower()
                if fmt == "jpeg":
                    fmt = "jpeg"
                return img_bytes, f"image/{fmt}"
            # Convert unsupported formats (BMP, TIFF, HEIC, etc.) to PNG
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), "image/png"
        except Exception:
            # If PIL can't open it, assume PNG and let OpenAI reject if invalid
            return img_bytes, "image/png"

    @staticmethod
    def _downscale_for_vision(img_bytes: bytes, max_edge: int = _VISION_MAX_EDGE) -> bytes:
        """Resize so the long edge is <= max_edge. Pass-through if already smaller.

        Cuts OpenAI vision token cost roughly in half for typical phone photos
        without measurable quality loss for handwriting recognition.
        """
        import io

        from PIL import Image

        try:
            img = Image.open(io.BytesIO(img_bytes))
            long_edge = max(img.width, img.height)
            if long_edge <= max_edge:
                return img_bytes
            scale = max_edge / float(long_edge)
            new_w = max(1, int(img.width * scale))
            new_h = max(1, int(img.height * scale))
            resized = img.convert("RGB").resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception:
            # If PIL can't decode, leave it for _to_png to handle.
            return img_bytes

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type((APITimeoutError, APIConnectionError, RateLimitError)),
    )
    def _chat_completion_with_images(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes],
        model: str = settings.AI_MODEL,
        max_tokens: int = 16384,
        temperature: float = 0.0,
    ) -> str:
        """Make a chat completion API call with image inputs.

        Each image is downscaled to a long edge of `_VISION_MAX_EDGE` px before
        encoding. Cuts roughly half the visual tokens, which keeps a single
        request well under the per-minute TPM limits of low-tier OpenAI
        accounts and avoids triggering 429 retry storms.
        """
        import base64

        content: list[dict] = [{"type": "text", "text": user_text}]
        for img_bytes in images:
            shrunk = self._downscale_for_vision(img_bytes)
            safe_bytes, media_type = self._to_png(shrunk)
            b64 = base64.b64encode(safe_bytes).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64}"},
                }
            )

        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
