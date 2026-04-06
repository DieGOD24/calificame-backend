from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_user_project
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.schemas.question import (
    BulkConfirmRequest,
    ConfirmQuestionsRequest,
    QuestionResponse,
    QuestionUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/questions", tags=["Questions"])


@router.get("/", response_model=list[QuestionResponse])
def list_questions(
    project_id: str,
    project: Project = Depends(get_user_project),
) -> list:
    """List all questions for a project."""
    return project.questions


@router.put("/{question_id}", response_model=QuestionResponse)
def update_question(
    project_id: str,
    question_id: str,
    question_data: QuestionUpdate,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> Question:
    """Update or correct a question."""
    question = db.query(Question).filter(Question.id == question_id, Question.project_id == project_id).first()
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    update_data = question_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(question, field, value)

    db.commit()
    db.refresh(question)
    logger.info("Question {} updated in project {}", question_id, project_id)
    return question


@router.post("/confirm", response_model=list[QuestionResponse])
def confirm_questions(
    project_id: str,
    request: ConfirmQuestionsRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> list[Question]:
    """Confirm selected questions, optionally applying corrections."""
    corrections = {c.question_id: c for c in request.confirmations}

    confirmed_questions: list[Question] = []
    for qid in request.question_ids:
        question = db.query(Question).filter(Question.id == qid, Question.project_id == project_id).first()
        if question is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Question {qid} not found",
            )

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

    all_questions = db.query(Question).filter(Question.project_id == project_id).all()
    if all(q.is_confirmed for q in all_questions):
        project.status = ProjectStatus.CONFIRMED.value

    db.commit()
    for q in confirmed_questions:
        db.refresh(q)

    logger.info("Confirmed {} questions in project {}", len(confirmed_questions), project_id)
    return confirmed_questions


@router.post("/confirm-all", response_model=list[QuestionResponse])
def confirm_all_questions(
    project_id: str,
    request: BulkConfirmRequest,
    db: Session = Depends(get_db),
    project: Project = Depends(get_user_project),
) -> list[Question]:
    """Confirm all questions at once."""
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

    logger.info("Confirmed all {} questions in project {}", len(questions), project_id)
    return questions
