from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.config import settings
from app.models.answer_key import AnswerKey
from app.models.project import Project, ProjectStatus
from app.models.user import User
from app.schemas.answer_key import AnswerKeyResponse, ProcessedAnswerKeyResponse
from app.schemas.question import QuestionResponse
from app.services.document_processor import DocumentProcessor
from app.services.storage import get_storage_service

router = APIRouter(prefix="/projects/{project_id}/answer-key", tags=["Answer Keys"])


def _get_user_project(project_id: str, db: Session, current_user: User) -> Project:
    """Get a project belonging to the current user."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return project


@router.post("/upload", response_model=AnswerKeyResponse, status_code=status.HTTP_201_CREATED)
async def upload_answer_key(
    project_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AnswerKey:
    """Upload an answer key file (PDF or image)."""
    project = _get_user_project(project_id, db, current_user)

    # Validate file type
    content_type = file.content_type or ""
    if not (content_type.startswith("image/") or content_type == "application/pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a PDF or image (PNG, JPG, etc.)",
        )

    # Read file
    file_bytes = await file.read()

    # Check file size
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds {settings.MAX_FILE_SIZE_MB}MB limit",
        )

    # Determine file type
    file_type = "pdf" if content_type == "application/pdf" else "images"
    extension = ".pdf" if file_type == "pdf" else ".png"
    storage_path = f"answer_keys/{project_id}/{uuid4()}{extension}"

    # Save file
    storage = get_storage_service()
    storage.save_file(file_bytes, storage_path)

    # Delete existing answer key if any
    existing = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if existing:
        try:
            storage.delete_file(existing.file_path)
        except Exception:
            pass
        db.delete(existing)
        db.flush()

    # Create answer key record
    answer_key = AnswerKey(
        project_id=project_id,
        original_filename=file.filename,
        file_path=storage_path,
        file_type=file_type,
        is_processed=False,
    )
    db.add(answer_key)

    # Update project status
    project.status = ProjectStatus.ANSWER_KEY_UPLOADED.value
    db.commit()
    db.refresh(answer_key)

    return answer_key


@router.get("/", response_model=AnswerKeyResponse)
def get_answer_key(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AnswerKey:
    """Get the answer key for a project."""
    _get_user_project(project_id, db, current_user)

    answer_key = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if answer_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer key not found")

    return answer_key


@router.post("/process", response_model=ProcessedAnswerKeyResponse)
def process_answer_key(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Process the answer key using OCR and AI extraction."""
    project = _get_user_project(project_id, db, current_user)

    answer_key = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if answer_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer key not found")

    processor = DocumentProcessor()
    questions = processor.process_answer_key(db, answer_key, project)

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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list:
    """Get extracted questions from the processed answer key."""
    project = _get_user_project(project_id, db, current_user)

    return project.questions
