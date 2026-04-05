from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.project import Project
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.models.user import User
from app.schemas.grading import GradingSummary
from app.schemas.student_exam import (
    ExamAnswerResponse,
    GradingResultResponse,
    StudentExamResponse,
)
from app.services.grading import GradingService

router = APIRouter(prefix="/projects/{project_id}/grading", tags=["Grading"])


def _get_user_project(project_id: str, db: Session, current_user: User) -> Project:
    """Get a project belonging to the current user."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return project


@router.post("/grade/{exam_id}", response_model=GradingResultResponse)
def grade_single_exam(
    project_id: str,
    exam_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Grade a single student exam."""
    project = _get_user_project(project_id, db, current_user)

    # Verify questions are confirmed
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

    exam = (
        db.query(StudentExam)
        .filter(StudentExam.id == exam_id, StudentExam.project_id == project_id)
        .first()
    )
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student exam not found")

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


@router.post("/grade-all", response_model=list[StudentExamResponse])
def grade_all_exams(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list:
    """Grade all uploaded student exams for a project."""
    project = _get_user_project(project_id, db, current_user)

    # Verify questions are confirmed
    confirmed_count = (
        db.query(Question)
        .filter(Question.project_id == project_id, Question.is_confirmed.is_(True))
        .count()
    )
    if confirmed_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No confirmed questions. Please confirm the answer key first.",
        )

    grading_service = GradingService()
    results = grading_service.grade_all_exams(db, project)

    return results


@router.get("/summary", response_model=GradingSummary)
def get_grading_summary(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Get grading summary statistics for a project."""
    _get_user_project(project_id, db, current_user)

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
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Export all grading results as JSON."""
    project = _get_user_project(project_id, db, current_user)

    questions = (
        db.query(Question)
        .filter(Question.project_id == project_id)
        .order_by(Question.question_number)
        .all()
    )

    exams = (
        db.query(StudentExam)
        .filter(StudentExam.project_id == project_id)
        .order_by(StudentExam.created_at)
        .all()
    )

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
