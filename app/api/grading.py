from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, get_user_project
from app.database import SessionLocal
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.models.task_log import TaskLog
from app.models.user import User
from app.schemas.grading import GradingSummary
from app.schemas.student_exam import (
    ExamAnswerResponse,
    GradingResultResponse,
    StudentExamResponse,
)
from app.schemas.task_log import TaskLogResponse
from app.services.grading import GradingService

router = APIRouter(prefix="/projects/{project_id}/grading", tags=["Grading"])


def _run_grade_all_background(task_id: str, project_id: str, regrade: bool) -> None:
    """Background task to grade all exams."""
    db = SessionLocal()
    try:
        task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
        if not task:
            return

        task.status = "processing"
        task.started_at = datetime.now(UTC)
        task.current_step = "Iniciando calificacion..."
        db.commit()

        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            task.status = "failed"
            task.error_message = "Project not found"
            task.completed_at = datetime.now(UTC)
            db.commit()
            return

        questions = (
            db.query(Question)
            .filter(Question.project_id == project_id, Question.is_confirmed.is_(True))
            .order_by(Question.question_number)
            .all()
        )

        if regrade:
            statuses = ["uploaded", "error", "graded"]
        else:
            statuses = ["uploaded", "error"]

        student_exams = (
            db.query(StudentExam)
            .filter(
                StudentExam.project_id == project_id,
                StudentExam.status.in_(statuses),
            )
            .all()
        )

        total = len(student_exams)
        if total == 0:
            task.status = "completed"
            task.progress = 100.0
            task.current_step = "No hay examenes para calificar"
            task.completed_at = datetime.now(UTC)
            task.result_data = {"graded_count": 0}
            db.commit()
            return

        project.status = ProjectStatus.GRADING.value
        db.commit()

        grading_service = GradingService()
        graded_count = 0

        for i, exam in enumerate(student_exams):
            task.current_step = f"Calificando examen {i + 1} de {total}..."
            task.progress = (i / total) * 100
            db.commit()

            try:
                grading_service.grade_exam(db, exam, questions)
                graded_count += 1
                logger.info("Graded exam {}/{} for project {}", i + 1, total, project_id)
            except Exception as e:
                logger.error("Error grading exam {}: {}", exam.id, str(e))

        # Check if all exams are graded
        all_exams = db.query(StudentExam).filter(StudentExam.project_id == project_id).all()
        all_graded = all(e.status == "graded" for e in all_exams)
        if all_graded:
            project.status = ProjectStatus.COMPLETED.value

        task.status = "completed"
        task.progress = 100.0
        task.current_step = f"Calificacion completada: {graded_count}/{total} examenes"
        task.completed_at = datetime.now(UTC)
        task.result_data = {"graded_count": graded_count, "total": total}
        db.commit()

        logger.info("Grading complete for project {}: {}/{}", project_id, graded_count, total)

    except Exception as e:
        logger.error("Background grading failed for project {}: {}", project_id, str(e))
        try:
            task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
            if task:
                task.status = "failed"
                task.error_message = str(e)
                task.completed_at = datetime.now(UTC)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/grade/{exam_id}", response_model=GradingResultResponse)
def grade_single_exam(
    project_id: str,
    exam_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    project: Project = Depends(get_user_project),
) -> dict:
    """Grade a single student exam."""
    confirmed_questions = (
        db.query(Question)
        .filter(Question.project_id == project_id, Question.is_confirmed.is_(True))
        .order_by(Question.question_number)
        .all()
    )
    if not confirmed_questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No confirmed questions. Please confirm the answer key first.",
        )

    exam = db.query(StudentExam).filter(StudentExam.id == exam_id, StudentExam.project_id == project_id).first()
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student exam not found")

    logger.info("Grading single exam {} for project {} by {}", exam_id, project_id, current_user.email)
    grading_service = GradingService()
    graded_exam = grading_service.grade_exam(db, exam, confirmed_questions)

    answers = []
    for a in graded_exam.answers:
        answers.append(
            ExamAnswerResponse(
                id=a.id,
                question_id=a.question_id,
                question_number=a.question.question_number if a.question else None,
                question_text=a.question.question_text if a.question else None,
                correct_answer=a.question.correct_answer if a.question else None,
                extracted_answer=a.extracted_answer,
                is_correct=a.is_correct,
                score=a.score,
                max_score=a.max_score,
                feedback=a.feedback,
                confidence=a.confidence,
            )
        )

    return {
        "student_exam": StudentExamResponse.model_validate(graded_exam),
        "answers": answers,
    }


@router.post("/grade-all", response_model=TaskLogResponse)
def grade_all_exams(
    project_id: str,
    background_tasks: BackgroundTasks,
    regrade: bool = Query(False, description="Re-grade already graded exams too"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    project: Project = Depends(get_user_project),
) -> TaskLog:
    """Grade all student exams in background. Returns a task to poll for progress."""
    confirmed_count = (
        db.query(Question).filter(Question.project_id == project_id, Question.is_confirmed.is_(True)).count()
    )
    if confirmed_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No confirmed questions. Please confirm the answer key first.",
        )

    task = TaskLog(
        id=str(uuid4()),
        user_id=current_user.id,
        task_type="grading",
        status="pending",
        progress=0.0,
        current_step="En cola de procesamiento...",
        project_id=project_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    logger.info("Starting background grading for project {} (task {})", project_id, task.id)
    background_tasks.add_task(_run_grade_all_background, task.id, project_id, regrade)

    return task


@router.get("/summary", response_model=GradingSummary)
def get_grading_summary(
    project_id: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> dict:
    """Get grading summary statistics for a project."""
    exams = db.query(StudentExam).filter(StudentExam.project_id == project_id).all()
    total = len(exams)
    graded = [e for e in exams if e.status == "graded"]
    errors = [e for e in exams if e.status == "error"]
    pending = total - len(graded) - len(errors)

    avg_score = None
    highest = None
    lowest = None
    avg_pct = None

    if graded:
        scores = [e.total_score for e in graded if e.total_score is not None]
        percentages = [e.grade_percentage for e in graded if e.grade_percentage is not None]

        if scores:
            avg_score = sum(scores) / len(scores)
            highest = max(scores)
            lowest = min(scores)
        if percentages:
            avg_pct = sum(percentages) / len(percentages)

    return {
        "project_id": project_id,
        "total_exams": total,
        "graded_count": len(graded),
        "pending_count": pending,
        "error_count": len(errors),
        "average_score": avg_score,
        "highest_score": highest,
        "lowest_score": lowest,
        "average_percentage": avg_pct,
    }


@router.get("/export")
def export_results(
    project_id: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> dict:
    """Export all grading results as JSON."""
    questions = db.query(Question).filter(Question.project_id == project_id).order_by(Question.question_number).all()
    exams = db.query(StudentExam).filter(StudentExam.project_id == project_id).order_by(StudentExam.created_at).all()

    export_data = {
        "project": {
            "id": project.id,
            "name": project.name,
            "subject": project.subject,
            "status": project.status,
        },
        "questions": [
            {
                "question_number": q.question_number,
                "question_text": q.question_text,
                "correct_answer": q.correct_answer,
                "points": q.points,
            }
            for q in questions
        ],
        "results": [
            {
                "student_name": e.student_name,
                "student_identifier": e.student_identifier,
                "original_filename": e.original_filename,
                "status": e.status,
                "total_score": e.total_score,
                "max_score": e.max_score,
                "grade_percentage": e.grade_percentage,
                "answers": [
                    {
                        "question_number": a.question.question_number if a.question else None,
                        "extracted_answer": a.extracted_answer,
                        "is_correct": a.is_correct,
                        "score": a.score,
                        "max_score": a.max_score,
                        "feedback": a.feedback,
                    }
                    for a in e.answers
                ],
            }
            for e in exams
        ],
    }

    return export_data
