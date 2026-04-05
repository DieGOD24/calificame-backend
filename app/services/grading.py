from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.exam_answer import ExamAnswer
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.services.document_processor import DocumentProcessor
from app.services.ocr import OCRService
from app.services.storage import get_storage_service


class GradingService:
    """Service for grading student exams against answer keys."""

    def __init__(self, ocr_service: OCRService | None = None) -> None:
        self.ocr_service = ocr_service or OCRService()
        self.storage = get_storage_service()
        self.doc_processor = DocumentProcessor(self.ocr_service)

    def grade_exam(
        self,
        db: Session,
        student_exam: StudentExam,
        questions: list[Question],
    ) -> StudentExam:
        """Grade a single student exam against the answer key questions."""
        from app.agents.grading_agent import GradingAgent

        student_exam.status = "processing"
        db.commit()

        try:
            # Read the student exam file
            file_bytes = self.storage.get_file(student_exam.file_path)

            # Use grading agent
            project = student_exam.project
            config = project.config or {} if project else {}
            agent = GradingAgent(openai_client=self.ocr_service.client)
            grading_results = agent.execute(
                student_images=[file_bytes],
                questions=questions,
                config=config,
            )

            # Delete existing answers for this exam
            db.query(ExamAnswer).filter(ExamAnswer.student_exam_id == student_exam.id).delete()

            total_score = 0.0
            max_score = 0.0

            for result in grading_results:
                question_id = result.get("question_id")
                question = next((q for q in questions if q.id == question_id), None)
                if question is None:
                    continue

                score = result.get("score", 0.0)
                q_max = question.points or 1.0
                is_correct = result.get("is_correct", False)

                answer = ExamAnswer(
                    id=str(uuid4()),
                    student_exam_id=student_exam.id,
                    question_id=question_id,
                    extracted_answer=result.get("extracted_answer", ""),
                    is_correct=is_correct,
                    score=score,
                    max_score=q_max,
                    feedback=result.get("feedback", ""),
                    confidence=result.get("confidence", 0.0),
                )
                db.add(answer)

                total_score += score
                max_score += q_max

            student_exam.total_score = total_score
            student_exam.max_score = max_score
            student_exam.grade_percentage = (total_score / max_score * 100) if max_score > 0 else 0.0
            student_exam.status = "graded"
            student_exam.graded_at = datetime.now(timezone.utc)
            student_exam.grading_details = {"results": grading_results}

            db.commit()
            db.refresh(student_exam)

        except Exception as e:
            student_exam.status = "error"
            student_exam.error_message = str(e)
            db.commit()
            db.refresh(student_exam)

        return student_exam

    def grade_all_exams(self, db: Session, project: Project) -> list[StudentExam]:
        """Grade all uploaded student exams for a project."""
        questions = (
            db.query(Question)
            .filter(Question.project_id == project.id, Question.is_confirmed.is_(True))
            .order_by(Question.question_number)
            .all()
        )

        student_exams = (
            db.query(StudentExam)
            .filter(
                StudentExam.project_id == project.id,
                StudentExam.status.in_(["uploaded", "error"]),
            )
            .all()
        )

        project.status = ProjectStatus.GRADING.value
        db.commit()

        results: list[StudentExam] = []
        for exam in student_exams:
            graded = self.grade_exam(db, exam, questions)
            results.append(graded)

        # Check if all exams are graded
        all_exams = db.query(StudentExam).filter(StudentExam.project_id == project.id).all()
        all_graded = all(e.status == "graded" for e in all_exams)
        if all_graded:
            project.status = ProjectStatus.COMPLETED.value
            db.commit()

        return results
