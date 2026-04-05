from datetime import datetime

from pydantic import BaseModel

from app.schemas.question import QuestionResponse


class AnswerKeyResponse(BaseModel):
    id: str
    project_id: str
    original_filename: str | None
    file_type: str | None
    num_pages: int | None
    is_processed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProcessedAnswerKeyResponse(BaseModel):
    id: str
    project_id: str
    original_filename: str | None
    file_type: str | None
    num_pages: int | None
    is_processed: bool
    processed_data: dict | None
    questions: list[QuestionResponse]
    created_at: datetime

    model_config = {"from_attributes": True}
