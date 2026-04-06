from uuid import uuid4

import fitz  # pymupdf
from loguru import logger
from sqlalchemy.orm import Session

from app.agents.answer_extraction_agent import AnswerExtractionAgent
from app.models.answer_key import AnswerKey
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.services.storage import get_storage_service


def _pdf_to_images(pdf_bytes: bytes) -> list[bytes]:
    """Convert each PDF page to a PNG image."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: list[bytes] = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


class DocumentProcessor:
    """Processes uploaded documents (answer keys and student exams)."""

    def __init__(self, extraction_agent: AnswerExtractionAgent | None = None) -> None:
        self.storage = get_storage_service()
        self.extraction_agent = extraction_agent or AnswerExtractionAgent()

    def process_answer_key(self, db: Session, answer_key: AnswerKey, project: Project) -> list[Question]:
        """Process an answer key file to extract questions and answers via Vision AI."""
        logger.info("Processing answer key for project {}", project.id)
        file_bytes = self.storage.get_file(answer_key.file_path)

        # Convert to images regardless of file type
        if answer_key.file_type == "pdf":
            images = _pdf_to_images(file_bytes)
        else:
            images = [file_bytes]

        answer_key.num_pages = len(images)

        # Use the extraction agent with images → GPT-4o Vision
        config = project.config or {}
        qa_pairs = self.extraction_agent.execute(images=images, config=config)

        # Store raw processed data
        answer_key.processed_data = {
            "num_images": len(images),
            "extracted_questions": qa_pairs,
        }
        answer_key.is_processed = True

        # Delete existing questions for this project (in case of re-processing)
        db.query(Question).filter(Question.project_id == project.id).delete()

        # Create Question records
        points_per_question = config.get("points_per_question", 1.0)
        questions: list[Question] = []
        for qa in qa_pairs:
            question = Question(
                id=str(uuid4()),
                project_id=project.id,
                question_number=qa.get("question_number", len(questions) + 1),
                question_text=qa.get("question_text", ""),
                correct_answer=qa.get("correct_answer", ""),
                points=points_per_question,
                is_confirmed=False,
            )
            db.add(question)
            questions.append(question)

        # Update project status
        project.status = ProjectStatus.ANSWER_KEY_PROCESSED.value
        db.commit()
        db.refresh(answer_key)

        logger.info("Extracted {} questions from answer key (project {})", len(questions), project.id)
        return questions

    def process_student_exam(
        self,
        db: Session,
        student_exam: StudentExam,
        project: Project,
    ) -> dict:
        """Process a student exam file to extract answers as images."""
        file_bytes = self.storage.get_file(student_exam.file_path)

        if student_exam.file_type == "pdf":
            images = _pdf_to_images(file_bytes)
        else:
            images = [file_bytes]

        student_exam.status = "processing"
        db.commit()

        return {
            "images": images,
            "student_exam_id": student_exam.id,
        }
