from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.config import settings
from app.models.project import Project
from app.models.student_exam import StudentExam
from app.models.user import User
from app.schemas.student_exam import (
    ExamAnswerResponse,
    GradingResultResponse,
    StudentExamListResponse,
    StudentExamResponse,
)
from app.services.storage import get_storage_service

router = APIRouter(prefix="/projects/{project_id}/exams", tags=["Student Exams"])


def _get_user_project(project_id: str, db: Session, current_user: User) -> Project:
    """Get a project belonging to the current user."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return project


@router.post("/upload", response_model=list[StudentExamResponse], status_code=status.HTTP_201_CREATED)
async def upload_student_exams(
    project_id: str,
    files: list[UploadFile],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[StudentExam]:
    """Upload one or more student exam files."""
    _get_user_project(project_id, db, current_user)

    storage = get_storage_service()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    created: list[StudentExam] = []

    for file in files:
        content_type = file.content_type or ""
        if not (content_type.startswith("image/") or content_type == "application/pdf"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El archivo '{file.filename}' debe ser PDF o imagen",
            )

        file_bytes = await file.read()

        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{file.filename}' excede el limite de {settings.MAX_FILE_SIZE_MB}MB",
            )

        file_type = "pdf" if content_type == "application/pdf" else "images"
        extension = ".pdf" if file_type == "pdf" else ".png"
        storage_path = f"student_exams/{project_id}/{uuid4()}{extension}"

        storage.save_file(file_bytes, storage_path)

        student_exam = StudentExam(
            project_id=project_id,
            original_filename=file.filename,
            file_path=storage_path,
            file_type=file_type,
            status="uploaded",
        )
        db.add(student_exam)
        created.append(student_exam)

    db.commit()
    for exam in created:
        db.refresh(exam)

    return created


@router.get("/", response_model=StudentExamListResponse)
def list_student_exams(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """List all student exams for a project."""
    _get_user_project(project_id, db, current_user)

    exams = (
        db.query(StudentExam)
        .filter(StudentExam.project_id == project_id)
        .order_by(StudentExam.created_at.desc())
        .all()
    )

    graded = [e for e in exams if e.status == "graded"]
    graded_count = len(graded)

    avg_score = None
    if graded:
        percentages = [e.grade_percentage for e in graded if e.grade_percentage is not None]
        avg_score = sum(percentages) / len(percentages) if percentages else None

    return {
        "items": exams,
        "total": len(exams),
        "graded_count": graded_count,
        "average_score": avg_score,
    }


@router.get("/{exam_id}", response_model=GradingResultResponse)
def get_student_exam(
    project_id: str,
    exam_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Get student exam details with answers."""
    _get_user_project(project_id, db, current_user)

    exam = (
        db.query(StudentExam)
        .filter(StudentExam.id == exam_id, StudentExam.project_id == project_id)
        .first()
    )
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student exam not found")

    answers = []
    for a in exam.answers:
        answer_data = ExamAnswerResponse(
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
        answers.append(answer_data)

    return {
        "student_exam": StudentExamResponse.model_validate(exam),
        "answers": answers,
    }


class StudentExamUpdate(BaseModel):
    student_name: str | None = None
    student_identifier: str | None = None


@router.patch("/{exam_id}", response_model=StudentExamResponse)
def update_student_exam(
    project_id: str,
    exam_id: str,
    data: StudentExamUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StudentExam:
    """Update student name or identifier for an exam."""
    _get_user_project(project_id, db, current_user)

    exam = (
        db.query(StudentExam)
        .filter(StudentExam.id == exam_id, StudentExam.project_id == project_id)
        .first()
    )
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student exam not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(exam, field, value)

    db.commit()
    db.refresh(exam)
    return exam


@router.delete("/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student_exam(
    project_id: str,
    exam_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    """Delete a student exam."""
    _get_user_project(project_id, db, current_user)

    exam = (
        db.query(StudentExam)
        .filter(StudentExam.id == exam_id, StudentExam.project_id == project_id)
        .first()
    )
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student exam not found")

    # Delete file from storage
    try:
        storage = get_storage_service()
        storage.delete_file(exam.file_path)
    except Exception:
        pass

    db.delete(exam)
    db.commit()
