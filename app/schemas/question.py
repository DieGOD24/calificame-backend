from pydantic import BaseModel, Field


class QuestionResponse(BaseModel):
    id: str
    project_id: str
    question_number: int
    question_text: str | None
    correct_answer: str
    points: float
    is_confirmed: bool

    model_config = {"from_attributes": True}


class QuestionUpdate(BaseModel):
    question_text: str | None = None
    correct_answer: str | None = None
    points: float | None = Field(default=None, gt=0)


class QuestionConfirmation(BaseModel):
    question_id: str
    correct_answer: str | None = None
    question_text: str | None = None
    points: float | None = None


class ConfirmQuestionsRequest(BaseModel):
    question_ids: list[str]
    confirmations: list[QuestionConfirmation] = []


class BulkConfirmRequest(BaseModel):
    confirm_all: bool = True
