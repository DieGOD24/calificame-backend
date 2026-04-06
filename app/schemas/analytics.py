from pydantic import BaseModel


class QuestionDifficulty(BaseModel):
    question_number: int
    question_text: str | None
    correct_count: int
    total_count: int
    success_rate: float


class ScoreDistribution(BaseModel):
    range_label: str
    count: int


class ProjectAnalytics(BaseModel):
    project_id: str
    project_name: str
    total_exams: int
    graded_count: int
    average_score: float | None
    median_score: float | None
    highest_score: float | None
    lowest_score: float | None
    average_percentage: float | None
    pass_rate: float | None
    score_distribution: list[ScoreDistribution]
    question_difficulty: list[QuestionDifficulty]


class StudentProgress(BaseModel):
    student_identifier: str
    student_name: str | None
    project_name: str
    score: float | None
    max_score: float | None
    percentage: float | None
    graded_at: str | None


class InstitutionAnalytics(BaseModel):
    institution_id: str
    institution_name: str
    total_professors: int
    total_students: int
    total_projects: int
    total_exams_graded: int
    average_score_percentage: float | None
