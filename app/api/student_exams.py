from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, UploadFile, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, get_user_project
from app.api.pdf_generator import generate_pdf_from_images
from app.config import settings
from app.database import SessionLocal
from app.models.project import Project
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.models.user import User
from app.rate_limit import limiter
from app.schemas.student_exam import (
    ExamAnswerResponse,
    GradingResultResponse,
    StudentExamListResponse,
    StudentExamResponse,
)
from app.services.grading import GradingService
from app.services.storage import get_storage_service

router = APIRouter(prefix="/projects/{project_id}/exams", tags=["Student Exams"])


def _persist_student_exam(
    *,
    db: Session,
    project_id: str,
    file_bytes: bytes,
    file_type: str,
    original_filename: str | None,
    student_name: str | None,
    student_identifier: str | None,
) -> StudentExam:
    """Persist bytes to storage and create a StudentExam row (caller commits)."""
    extension = ".pdf" if file_type == "pdf" else ".png"
    storage_path = f"student_exams/{project_id}/{uuid4()}{extension}"
    get_storage_service().save_file(file_bytes, storage_path)

    exam = StudentExam(
        project_id=project_id,
        original_filename=original_filename,
        file_path=storage_path,
        file_type=file_type,
        status="uploaded",
        student_name=student_name,
        student_identifier=student_identifier,
    )
    db.add(exam)
    return exam


def _run_single_grade_background(exam_id: str, project_id: str) -> None:
    """Run AI grading for a single exam in the background.

    Mirrors the structure of `_run_grade_all_background` in grading.py, but for
    a single exam triggered by `generate-and-assign` with auto_grade=true.
    On failure the exam is marked status='error' so the UI can offer a retry.
    """
    db = SessionLocal()
    try:
        exam = db.query(StudentExam).filter(StudentExam.id == exam_id).first()
        if exam is None:
            logger.warning("Background grade: exam {} not found", exam_id)
            return

        questions = (
            db.query(Question)
            .filter(Question.project_id == project_id, Question.is_confirmed.is_(True))
            .order_by(Question.question_number)
            .all()
        )
        if not questions:
            logger.warning(
                "Background grade: no confirmed questions for project {}, leaving exam {} as uploaded",
                project_id,
                exam_id,
            )
            exam.status = "uploaded"
            db.commit()
            return

        try:
            GradingService().grade_exam(db, exam, questions)
            logger.info("Background-graded exam {}", exam_id)
        except Exception as exc:
            logger.error("Background grading failed for exam {}: {}", exam_id, exc)
            exam.status = "error"
            exam.error_message = str(exc)[:500]
            db.commit()
    finally:
        db.close()


@router.post("/upload", response_model=list[StudentExamResponse], status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def upload_student_exams(
    request: Request,
    project_id: str,
    files: list[UploadFile],
    student_name: str | None = Form(None, max_length=255),
    student_identifier: str | None = Form(None, max_length=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    project: Project = Depends(get_user_project),
) -> list[StudentExam]:
    """Upload one or more student exam files.

    If `student_identifier` is provided, prevents duplicates: a project can only
    have one exam per identifier. Anonymous bulk uploads (no identifier) are
    always allowed.
    """
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    created: list[StudentExam] = []

    # Pre-check duplicate when student_identifier is provided
    if student_identifier:
        existing = (
            db.query(StudentExam)
            .filter(
                StudentExam.project_id == project_id,
                StudentExam.student_identifier == student_identifier,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Ya existe un examen para el estudiante {student_identifier} "
                    "en este proyecto. Eliminalo primero si quieres reemplazarlo."
                ),
            )

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
        exam = _persist_student_exam(
            db=db,
            project_id=project_id,
            file_bytes=file_bytes,
            file_type=file_type,
            original_filename=file.filename,
            student_name=student_name,
            student_identifier=student_identifier,
        )
        created.append(exam)

    db.commit()
    for exam in created:
        db.refresh(exam)

    logger.info("Uploaded {} exams for project {} by {}", len(created), project_id, current_user.email)
    return created


@router.post(
    "/generate-and-assign",
    response_model=StudentExamResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def generate_and_assign_exam(
    request: Request,
    project_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile],
    student_name: str = Form(..., max_length=255),
    student_identifier: str = Form(..., max_length=100),
    replace_existing: bool = Form(False),
    auto_grade: bool = Form(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    project: Project = Depends(get_user_project),
) -> StudentExam:
    """Generate a PDF from processed image pages and assign it directly as the
    student's exam, optionally enqueueing background AI grading.

    The frontend is expected to first call `/pdf-generator/analyze` on raw
    photos and then send the processed PNGs (one per page) here, so the PDF
    matches the preview the user accepted.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se enviaron imagenes",
        )

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    image_bytes_list: list[bytes] = []
    total_size = 0
    for upload in files:
        content_type = upload.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"'{upload.filename}' debe ser una imagen procesada",
            )
        data = await upload.read()
        total_size += len(data)
        if total_size > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Las imagenes superan el limite de {settings.MAX_FILE_SIZE_MB}MB",
            )
        image_bytes_list.append(data)

    existing = (
        db.query(StudentExam)
        .filter(
            StudentExam.project_id == project_id,
            StudentExam.student_identifier == student_identifier,
        )
        .first()
    )
    if existing:
        if existing.status == "processing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "exam_processing",
                    "existing_exam_id": existing.id,
                    "message": (
                        "El examen actual del estudiante esta siendo calificado. "
                        "Espera a que termine antes de reemplazarlo."
                    ),
                },
            )
        if not replace_existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "exam_exists",
                    "existing_exam_id": existing.id,
                    "message": (
                        f"Ya existe un examen para el estudiante {student_identifier}. "
                        "Confirma el reemplazo para sobrescribirlo."
                    ),
                },
            )
        try:
            get_storage_service().delete_file(existing.file_path)
        except Exception as exc:
            logger.warning(
                "Failed to delete old file {} for exam {}: {}",
                existing.file_path,
                existing.id,
                exc,
            )
        db.delete(existing)
        db.flush()

    try:
        pdf_bytes = generate_pdf_from_images(image_bytes_list)
    except Exception as exc:
        logger.error("Failed to generate PDF for student {}: {}", student_identifier, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo generar el PDF a partir de las imagenes recibidas.",
        )

    student_exam = _persist_student_exam(
        db=db,
        project_id=project_id,
        file_bytes=pdf_bytes,
        file_type="pdf",
        original_filename=f"{student_identifier}.pdf",
        student_name=student_name,
        student_identifier=student_identifier,
    )

    should_grade = (
        auto_grade
        and bool(settings.OPENAI_API_KEY)
        and db.query(Question).filter(Question.project_id == project_id, Question.is_confirmed.is_(True)).count() > 0
    )
    if should_grade:
        student_exam.status = "processing"

    db.commit()
    db.refresh(student_exam)

    if should_grade:
        background_tasks.add_task(_run_single_grade_background, student_exam.id, project_id)
        logger.info(
            "Generated + queued auto-grade for exam {} (project {}, student {})",
            student_exam.id,
            project_id,
            student_identifier,
        )
    else:
        logger.info(
            "Generated and assigned exam {} (project {}, student {}, auto_grade={})",
            student_exam.id,
            project_id,
            student_identifier,
            auto_grade,
        )

    return student_exam


@router.get("", response_model=StudentExamListResponse)
def list_student_exams(
    project_id: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> dict:
    """List all student exams for a project."""
    exams = (
        db.query(StudentExam).filter(StudentExam.project_id == project_id).order_by(StudentExam.created_at.desc()).all()
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
    project: Project = Depends(get_user_project),
) -> dict:
    """Get student exam details with answers."""
    exam = db.query(StudentExam).filter(StudentExam.id == exam_id, StudentExam.project_id == project_id).first()
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
    project: Project = Depends(get_user_project),
) -> StudentExam:
    """Update student name or identifier for an exam."""
    exam = db.query(StudentExam).filter(StudentExam.id == exam_id, StudentExam.project_id == project_id).first()
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
    project: Project = Depends(get_user_project),
) -> None:
    """Delete a student exam."""
    exam = db.query(StudentExam).filter(StudentExam.id == exam_id, StudentExam.project_id == project_id).first()
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student exam not found")

    try:
        storage = get_storage_service()
        storage.delete_file(exam.file_path)
    except Exception as exc:
        logger.warning("Failed to delete file {} for exam {}: {}", exam.file_path, exam_id, exc)

    logger.info("Deleted exam {} from project {}", exam_id, project_id)
    db.delete(exam)
    db.commit()
