import json
from typing import Any

from app.agents.base import BaseAgent
from app.models.question import Question

GRADING_SYSTEM_PROMPT = """You are an expert exam grader. You will be shown a student's exam and a list of questions with their correct answers.

For each question:
1. Find and extract the student's answer from the exam image(s).
2. Compare it against the correct answer.
3. Determine if it is correct, partially correct, or incorrect.
4. Assign a score (0 to max_points for the question).
5. Provide brief feedback explaining the grade.
6. Rate your confidence in the extraction (0.0 to 1.0).

Grading rules:
- For multiple choice: exact match required (letter or full answer).
- For open-ended: assess semantic correctness, not exact wording.
- Award partial credit for partially correct open-ended answers.
- If you cannot find or read a student's answer, mark it with score 0 and low confidence.

Return a JSON array of objects with these fields:
- "question_id": string (the provided question ID)
- "extracted_answer": string (what the student wrote)
- "is_correct": boolean
- "score": float (0 to max_points)
- "feedback": string (brief explanation)
- "confidence": float (0.0 to 1.0)

Return ONLY the JSON array, no other text or markdown formatting."""


class GradingAgent(BaseAgent):
    """Agent specialized in grading student exams against answer keys."""

    def execute(self, **kwargs: Any) -> list[dict]:
        """Grade a student exam against the answer key.

        Args:
            student_images: List of image bytes from the student exam.
            questions: List of Question model instances.
            config: Project configuration dict.

        Returns:
            List of grading result dicts per question.
        """
        student_images: list[bytes] = kwargs.get("student_images", [])
        questions: list[Question] = kwargs.get("questions", [])
        config: dict = kwargs.get("config", {})

        if not student_images or not questions:
            return []

        # Build the questions reference for the AI
        questions_ref = []
        for q in questions:
            questions_ref.append(
                {
                    "question_id": q.id,
                    "question_number": q.question_number,
                    "question_text": q.question_text or "",
                    "correct_answer": q.correct_answer,
                    "max_points": q.points or 1.0,
                }
            )

        exam_type = config.get("exam_type", "mixed")
        user_text = (
            f"Grade this student exam. Exam type: {exam_type}\n\n"
            f"Questions and correct answers:\n{json.dumps(questions_ref, indent=2)}\n\n"
            "Look at the student's exam image(s) and grade each question."
        )

        raw_result = self._chat_completion_with_images(
            system_prompt=GRADING_SYSTEM_PROMPT,
            user_text=user_text,
            images=student_images,
        )

        results = self._parse_json_response(raw_result)

        # Ensure all questions have a result
        found_ids = {r["question_id"] for r in results if "question_id" in r}
        for q in questions:
            if q.id not in found_ids:
                results.append(
                    {
                        "question_id": q.id,
                        "extracted_answer": "",
                        "is_correct": False,
                        "score": 0.0,
                        "feedback": "Answer not found in exam.",
                        "confidence": 0.0,
                    }
                )

        return results

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
