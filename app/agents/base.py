from abc import ABC, abstractmethod
from typing import Any

from openai import OpenAI

from app.config import settings


class BaseAgent(ABC):
    """Abstract base class for AI agents."""

    def __init__(self, openai_client: OpenAI | None = None) -> None:
        self.client = openai_client or OpenAI(api_key=settings.OPENAI_API_KEY)

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """Execute the agent's task."""
        ...

    def _chat_completion(
        self,
        messages: list[dict],
        model: str = "gpt-4o",
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

    def _chat_completion_with_images(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes],
        model: str = "gpt-4o",
        max_tokens: int = 16384,
        temperature: float = 0.0,
    ) -> str:
        """Make a chat completion API call with image inputs."""
        import base64

        content: list[dict] = [{"type": "text", "text": user_text}]
        for img_bytes in images:
            safe_bytes, media_type = self._to_png(img_bytes)
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
