import json
from typing import Any

from app.agents.base import BaseAgent

EXTRACTION_SYSTEM_PROMPT = """You are an expert at extracting questions and correct answers from exam answer keys.

Your task is to analyze the provided exam answer key image(s) and extract all questions with their correct answers.

Follow this multi-step approach:
1. First, identify ALL questions present in the document (question numbers and text if visible).
2. Second, extract the correct answer for each question.
3. Third, validate and structure the output.

Rules:
- Number questions sequentially if no numbers are visible.
- For multiple choice, include the letter AND the answer text if both are visible.
- For open-ended questions, extract the full correct answer.
- Be precise - every character matters for grading.

Return a JSON array of objects with these fields:
- "question_number": integer
- "question_text": string (the question text, or empty string if not visible)
- "correct_answer": string (the correct answer)

Return ONLY the JSON array, no other text or markdown formatting."""


class AnswerExtractionAgent(BaseAgent):
    """Agent specialized in extracting Q&A pairs from exam answer key images."""

    def execute(self, **kwargs: Any) -> list[dict]:
        """Extract questions and answers from answer key images.

        Args:
            images: List of image bytes from the answer key.
            config: Project configuration dict.

        Returns:
            List of dicts with question_number, question_text, correct_answer.
        """
        images: list[bytes] = kwargs.get("images", [])
        config: dict = kwargs.get("config", {})

        if not images:
            return []

        exam_type = config.get("exam_type", "mixed")
        total_questions = config.get("total_questions")

        user_text = f"Extract all questions and correct answers from this exam answer key.\nExam type: {exam_type}"
        if total_questions:
            user_text += f"\nExpected number of questions: {total_questions}"

        # First pass: extract all questions and answers
        raw_result = self._chat_completion_with_images(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_text=user_text,
            images=images,
        )

        # Parse the result
        result = self._parse_json_response(raw_result)

        if not result:
            return []

        # Second pass: validate count if expected
        if total_questions and len(result) != total_questions:
            validation_prompt = (
                f"I expected {total_questions} questions but found {len(result)}. "
                "Please re-examine the answer key carefully and extract ALL questions. "
                f"Current extraction: {json.dumps(result)}"
            )
            raw_validation = self._chat_completion_with_images(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_text=validation_prompt,
                images=images,
            )
            validated = self._parse_json_response(raw_validation)
            if validated and len(validated) >= len(result):
                result = validated

        return result

    @staticmethod
    def _parse_json_response(text: str) -> list[dict]:
        """Parse a JSON array response, handling markdown code fences."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        return []
