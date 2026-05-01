from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

# --- Class ---


class ClassCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=255)
    semester: str = Field(..., min_length=1, max_length=50)
    description: str | None = None
    schedule: str | None = None
    institution_id: str | None = None


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    subject: str | None = Field(default=None, min_length=1, max_length=255)
    semester: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = None
    schedule: str | None = None
    is_active: bool | None = None
    # Admin-only fields. Plain professors get 403 if they include these.
    professor_id: str | None = None
    institution_id: str | None = None


class ClassResponse(BaseModel):
    id: str
    professor_id: str
    institution_id: str | None
    name: str
    subject: str
    semester: str
    description: str | None
    schedule: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None
    professor_name: str = ""
    enrollment_count: int = 0
    project_count: int = 0

    model_config = {"from_attributes": True}


class ClassListResponse(BaseModel):
    items: list[ClassResponse]
    total: int
    page: int
    page_size: int


# --- Enrollment ---


class ClassEnrollmentCreate(BaseModel):
    student_name: str = Field(..., min_length=1, max_length=255)
    student_identifier: str = Field(..., min_length=1, max_length=100)
    student_email: EmailStr | None = None


class ClassEnrollmentResponse(BaseModel):
    id: str
    class_id: str
    student_name: str
    student_identifier: str
    student_email: str | None
    user_id: str | None
    enrolled_at: datetime

    model_config = {"from_attributes": True}


class BulkEnrollResponse(BaseModel):
    added: int
    skipped: int
    errors: list[str]
    used_ai: bool = False


# --- Class Projects ---


class ClassProjectAdd(BaseModel):
    project_id: str


class ClassProjectReorder(BaseModel):
    order: list[str]


class ClassProjectResponse(BaseModel):
    id: str
    project_id: str
    project_name: str = ""
    project_status: str = ""
    display_order: int

    model_config = {"from_attributes": True}


# --- Gradebook ---


class GradebookCell(BaseModel):
    project_id: str
    project_name: str
    score: float | None = None
    max_score: float | None = None
    percentage: float | None = None


class GradebookRow(BaseModel):
    student_name: str
    student_identifier: str
    projects: list[GradebookCell]
    average: float | None = None
    pass_status: str = "pending"  # passing, failing, pending


class GradebookResponse(BaseModel):
    class_id: str
    class_name: str
    semester: str
    columns: list[str]
    rows: list[GradebookRow]


class StudentProgressResponse(BaseModel):
    student_name: str
    student_identifier: str
    class_name: str
    semester: str
    projects: list[GradebookCell]
    average: float | None = None
