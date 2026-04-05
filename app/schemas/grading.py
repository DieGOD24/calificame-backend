from pydantic import BaseModel


class GradeExamRequest(BaseModel):
    student_exam_id: str


class GradeAllRequest(BaseModel):
    project_id: str


class GradingSummary(BaseModel):
    project_id: str
    total_exams: int
    graded_count: int
    pending_count: int
    error_count: int
    average_score: float | None
    highest_score: float | None
    lowest_score: float | None
    average_percentage: float | None
