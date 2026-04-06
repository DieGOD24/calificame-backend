from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, get_user_project
from app.config import settings
from app.models.answer_key import AnswerKey
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.schemas.answer_key import AnswerKeyResponse, ProcessedAnswerKeyResponse
from app.schemas.question import QuestionResponse
from app.services.document_processor import DocumentProcessor
from app.services.storage import get_storage_service

router = APIRouter(prefix="/projects/{project_id}/answer-key", tags=["Answer Keys"])


@router.post("/upload", response_model=AnswerKeyResponse, status_code=status.HTTP_201_CREATED)
async def upload_answer_key(
    project_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    project: Project = Depends(get_user_project),
) -> AnswerKey:
    """Upload an answer key file (PDF or image)."""
    content_type = file.content_type or ""
    if not (content_type.startswith("image/") or content_type == "application/pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF or image (PNG, JPG, etc.)",
        )

    file_bytes = await file.read()

    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
        )

    file_type = "pdf" if content_type == "application/pdf" else "images"
    extension = ".pdf" if file_type == "pdf" else ".png"
    storage_path = f"answer_keys/{project_id}/{uuid4()}{extension}"

    storage = get_storage_service()
    storage.save_file(file_bytes, storage_path)

    existing = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if existing:
        try:
            storage.delete_file(existing.file_path)
        except Exception:
            pass
        db.delete(existing)
        db.flush()

    answer_key = AnswerKey(
        project_id=project_id,
        original_filename=file.filename,
        file_path=storage_path,
        file_type=file_type,
        is_processed=False,
    )
    db.add(answer_key)

    project.status = ProjectStatus.ANSWER_KEY_UPLOADED.value
    db.commit()
    db.refresh(answer_key)

    logger.info("Answer key uploaded for project {} by {}", project_id, current_user.email)
    return answer_key


@router.get("/", response_model=AnswerKeyResponse)
def get_answer_key(
    project_id: str,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> AnswerKey:
    """Get the answer key for a project."""
    answer_key = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if answer_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer key not found")
    return answer_key


@router.post("/process", response_model=ProcessedAnswerKeyResponse)
def process_answer_key(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    project: Project = Depends(get_user_project),
) -> dict:
    """Process the answer key using OCR and AI extraction."""
    answer_key = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if answer_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer key not found")

    logger.info("Processing answer key for project {} by {}", project_id, current_user.email)
    processor = DocumentProcessor()
    questions = processor.process_answer_key(db, answer_key, project)
    logger.info("Extracted {} questions from answer key", len(questions))

    return {
        "id": answer_key.id,
        "project_id": answer_key.project_id,
        "original_filename": answer_key.original_filename,
        "file_type": answer_key.file_type,
        "num_pages": answer_key.num_pages,
        "is_processed": answer_key.is_processed,
        "processed_data": answer_key.processed_data,
        "questions": [QuestionResponse.model_validate(q) for q in questions],
        "created_at": answer_key.created_at,
    }


@router.get("/questions", response_model=list[QuestionResponse])
def get_extracted_questions(
    project_id: str,
    project: Project = Depends(get_user_project),
) -> list:
    """Get extracted questions from the processed answer key."""
    return project.questions
