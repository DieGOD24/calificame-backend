from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.answer_key import AnswerKey
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.services.ocr import OCRService
from app.services.storage import get_storage_service


class DocumentProcessor:
    """Processes uploaded documents (answer keys and student exams)."""

    def __init__(self, ocr_service: OCRService | None = None) -> None:
        self.ocr_service = ocr_service or OCRService()
        self.storage = get_storage_service()

    def process_answer_key(self, db: Session, answer_key: AnswerKey, project: Project) -> list[Question]:
        """Process an answer key file to extract questions and answers."""
        file_bytes = self.storage.get_file(answer_key.file_path)

        # Extract text based on file type
        if answer_key.file_type == "pdf":
            pages_text = self.ocr_service.process_pdf(file_bytes)
            combined_text = "\n\n".join(pages_text)
            answer_key.num_pages = len(pages_text)
        else:
            # For images, process via vision OCR
            combined_text = self.ocr_service.process_image(file_bytes)
            answer_key.num_pages = 1

        # Extract structured Q&A
        config = project.config or {}
        qa_pairs = self.ocr_service.extract_questions_and_answers(combined_text, config)

        # Store raw processed data
        answer_key.processed_data = {
            "raw_text": combined_text,
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

        return questions

    def process_student_exam(
        self,
        db: Session,
        student_exam: StudentExam,
        project: Project,
    ) -> dict:
        """Process a student exam file to extract answers."""
        file_bytes = self.storage.get_file(student_exam.file_path)

        if student_exam.file_type == "pdf":
            pages_text = self.ocr_service.process_pdf(file_bytes)
            combined_text = "\n\n".join(pages_text)
        else:
            combined_text = self.ocr_service.process_image(file_bytes)

        student_exam.status = "processing"
        db.commit()

        return {
            "raw_text": combined_text,
            "student_exam_id": student_exam.id,
        }
