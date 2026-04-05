from datetime import datetime

from pydantic import BaseModel


class ExamAnswerResponse(BaseModel):
    id: str
    question_id: str
    question_number: int | None = None
    question_text: str | None = None
    correct_answer: str | None = None
    extracted_answer: str | None
    is_correct: bool | None
    score: float | None
    max_score: float | None
    feedback: str | None
    confidence: float | None

    model_config = {"from_attributes": True}


class StudentExamResponse(BaseModel):
    id: str
    project_id: str
    student_name: str | None
    student_identifier: str | None
    original_filename: str | None
    file_type: str | None
    status: str
    total_score: float | None
    max_score: float | None
    grade_percentage: float | None
    error_message: str | None
    created_at: datetime
    graded_at: datetime | None

    model_config = {"from_attributes": True}


class StudentExamListResponse(BaseModel):
    items: list[StudentExamResponse]
    total: int
    graded_count: int
    average_score: float | None


class GradingResultResponse(BaseModel):
    student_exam: StudentExamResponse
    answers: list[ExamAnswerResponse]
