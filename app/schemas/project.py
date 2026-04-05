from datetime import datetime

from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    exam_type: str = Field(..., pattern="^(multiple_choice|open_ended|mixed)$")
    total_questions: int = Field(..., gt=0)
    points_per_question: float | None = Field(default=None, gt=0)
    has_multiple_pages: bool = False
    additional_instructions: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    subject: str | None = None
    config: ProjectConfig | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    subject: str | None = None
    config: ProjectConfig | None = None


class ProjectOwner(BaseModel):
    id: str
    email: str
    full_name: str

    model_config = {"from_attributes": True}


class ProjectResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    description: str | None
    subject: str | None
    status: str
    config: dict | None
    created_at: datetime
    updated_at: datetime | None
    owner: ProjectOwner | None = None
    question_count: int = 0
    student_exam_count: int = 0

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    total: int
    page: int
    page_size: int
