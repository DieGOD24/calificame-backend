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

    def _chat_completion_with_images(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes],
        model: str = "gpt-4o",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> str:
        """Make a chat completion API call with image inputs."""
        import base64

        content: list[dict] = [{"type": "text", "text": user_text}]
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
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
