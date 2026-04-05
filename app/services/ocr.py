import base64
import io
import json

from openai import OpenAI
from PIL import Image
from PyPDF2 import PdfReader

from app.config import settings


class OCRService:
    """Service for OCR processing using OpenAI Vision API."""

    def __init__(self, client: OpenAI | None = None) -> None:
        self.client = client or OpenAI(api_key=settings.OPENAI_API_KEY)

    def process_image(self, image_bytes: bytes) -> str:
        """Extract text from an image using GPT-4o vision."""
        base64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Detect image format
        try:
            img = Image.open(io.BytesIO(image_bytes))
            fmt = img.format or "PNG"
            media_type = f"image/{fmt.lower()}"
        except Exception:
            media_type = "image/png"

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert OCR system. Extract all text from the provided image "
                        "accurately and completely. Preserve the structure, including question numbers, "
                        "answer labels, and formatting. Return only the extracted text."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text from this image:"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{base64_image}"},
                        },
                    ],
                },
            ],
            max_tokens=4096,
        )

        return response.choices[0].message.content or ""

    def process_pdf(self, pdf_bytes: bytes) -> list[str]:
        """Extract text from a PDF. Returns text per page."""
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages_text: list[str] = []

        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages_text.append(text)
            else:
                # If text extraction fails, the page likely contains images
                # In a full implementation, we would render the page to image and OCR it
                pages_text.append("[Image-based page - requires image OCR]")

        return pages_text

    def extract_questions_and_answers(self, text: str, config: dict | None = None) -> list[dict]:
        """Use AI to extract structured questions and answers from text."""
        exam_type = (config or {}).get("exam_type", "mixed")
        total_questions = (config or {}).get("total_questions")

        system_prompt = (
            "You are an expert at analyzing exam answer keys. "
            "Extract all questions and their correct answers from the provided text. "
            f"The exam type is: {exam_type}. "
        )
        if total_questions:
            system_prompt += f"Expected number of questions: {total_questions}. "

        system_prompt += (
            "Return a JSON array of objects with these fields: "
            '"question_number" (int), "question_text" (str, the question if visible), '
            '"correct_answer" (str, the correct answer). '
            "Return ONLY the JSON array, no other text."
        )

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract questions and answers from:\n\n{text}"},
            ],
            max_tokens=4096,
            temperature=0.0,
        )

        content = response.choices[0].message.content or "[]"
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:])
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        try:
            questions = json.loads(content)
        except json.JSONDecodeError:
            questions = []

        return questions
