from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.user import User
from app.schemas.question import (
    BulkConfirmRequest,
    ConfirmQuestionsRequest,
    QuestionResponse,
    QuestionUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/questions", tags=["Questions"])


def _get_user_project(project_id: str, db: Session, current_user: User) -> Project:
    """Get a project belonging to the current user."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return project


@router.get("/", response_model=list[QuestionResponse])
def list_questions(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list:
    """List all questions for a project."""
    project = _get_user_project(project_id, db, current_user)
    return project.questions


@router.put("/{question_id}", response_model=QuestionResponse)
def update_question(
    project_id: str,
    question_id: str,
    question_data: QuestionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Question:
    """Update or correct a question."""
    _get_user_project(project_id, db, current_user)

    question = db.query(Question).filter(Question.id == question_id, Question.project_id == project_id).first()
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    update_data = question_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(question, field, value)

    db.commit()
    db.refresh(question)
    return question


@router.post("/confirm", response_model=list[QuestionResponse])
def confirm_questions(
    project_id: str,
    request: ConfirmQuestionsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[Question]:
    """Confirm selected questions, optionally applying corrections."""
    project = _get_user_project(project_id, db, current_user)

    # Build correction map
    corrections = {c.question_id: c for c in request.confirmations}

    confirmed_questions: list[Question] = []
    for qid in request.question_ids:
        question = db.query(Question).filter(Question.id == qid, Question.project_id == project_id).first()
        if question is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Question {qid} not found",
            )

        # Apply corrections if provided
        if qid in corrections:
            correction = corrections[qid]
            if correction.correct_answer is not None:
                question.correct_answer = correction.correct_answer
            if correction.question_text is not None:
                question.question_text = correction.question_text
            if correction.points is not None:
                question.points = correction.points

        question.is_confirmed = True
        confirmed_questions.append(question)

    # Check if all questions are confirmed
    all_questions = db.query(Question).filter(Question.project_id == project_id).all()
    if all(q.is_confirmed for q in all_questions):
        project.status = ProjectStatus.CONFIRMED.value

    db.commit()
    for q in confirmed_questions:
        db.refresh(q)

    return confirmed_questions


@router.post("/confirm-all", response_model=list[QuestionResponse])
def confirm_all_questions(
    project_id: str,
    request: BulkConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[Question]:
    """Confirm all questions at once."""
    project = _get_user_project(project_id, db, current_user)

    questions = db.query(Question).filter(Question.project_id == project_id).all()
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No questions to confirm",
        )

    for question in questions:
        question.is_confirmed = True

    project.status = ProjectStatus.CONFIRMED.value
    db.commit()

    for q in questions:
        db.refresh(q)

    return questions
