import fitz
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.answer_key import AnswerKey
from app.models.project import Project
from app.models.student_exam import StudentExam
from app.models.user import User
from app.services.auth import decode_access_token
from app.services.storage import get_storage_service

router = APIRouter(prefix="/projects/{project_id}", tags=["Images"])


def _get_user_from_token(
    token: str | None = Query(None),
    db: Session = Depends(get_db),
) -> User:
    """Auth via query param token (for <img> tags that can't send headers)."""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token required")
    token_data = decode_access_token(token)
    if token_data is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.id == token_data.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def _get_user_project(project_id: str, db: Session, current_user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return project


def _file_to_page_image(file_bytes: bytes, file_type: str, page: int) -> bytes:
    """Get a specific page as PNG image."""
    if file_type == "pdf":
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if page < 0 or page >= len(doc):
            doc.close()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Page {page} out of range (0-{len(doc) - 1})",
            )
        pix = doc[page].get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        doc.close()
        return img_bytes
    else:
        import io

        from PIL import Image

        try:
            img = Image.open(io.BytesIO(file_bytes))
            if img.format == "PNG":
                return file_bytes
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return file_bytes


@router.get("/answer-key/image")
def get_answer_key_image(
    project_id: str,
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_user_from_token),
) -> Response:
    """Get answer key as a PNG image (specific page for PDFs)."""
    _get_user_project(project_id, db, current_user)

    answer_key = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if answer_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer key not found")

    storage = get_storage_service()
    file_bytes = storage.get_file(answer_key.file_path)
    img_bytes = _file_to_page_image(file_bytes, answer_key.file_type or "images", page)

    return Response(content=img_bytes, media_type="image/png")


@router.get("/answer-key/pages")
def get_answer_key_page_count(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_user_from_token),
) -> dict:
    """Get number of pages in the answer key."""
    _get_user_project(project_id, db, current_user)

    answer_key = db.query(AnswerKey).filter(AnswerKey.project_id == project_id).first()
    if answer_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer key not found")

    if answer_key.file_type == "pdf":
        storage = get_storage_service()
        file_bytes = storage.get_file(answer_key.file_path)
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
    else:
        count = 1

    return {"pages": count}


@router.get("/exams/{exam_id}/image")
def get_exam_image(
    project_id: str,
    exam_id: str,
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_user_from_token),
) -> Response:
    """Get student exam as a PNG image (specific page for PDFs)."""
    _get_user_project(project_id, db, current_user)

    exam = db.query(StudentExam).filter(StudentExam.id == exam_id, StudentExam.project_id == project_id).first()
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    storage = get_storage_service()
    file_bytes = storage.get_file(exam.file_path)
    img_bytes = _file_to_page_image(file_bytes, exam.file_type or "images", page)

    return Response(content=img_bytes, media_type="image/png")


@router.get("/exams/{exam_id}/pages")
def get_exam_page_count(
    project_id: str,
    exam_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_user_from_token),
) -> dict:
    """Get number of pages in the student exam."""
    _get_user_project(project_id, db, current_user)

    exam = db.query(StudentExam).filter(StudentExam.id == exam_id, StudentExam.project_id == project_id).first()
    if exam is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")

    if exam.file_type == "pdf":
        storage = get_storage_service()
        file_bytes = storage.get_file(exam.file_path)
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        count = len(doc)
        doc.close()
    else:
        count = 1

    return {"pages": count}
