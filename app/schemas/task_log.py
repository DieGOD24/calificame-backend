from datetime import datetime

from pydantic import BaseModel


class TaskLogResponse(BaseModel):
    id: str
    user_id: str
    task_type: str
    status: str
    progress: float
    current_step: str | None
    result_data: dict | None
    error_message: str | None
    project_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TaskLogListResponse(BaseModel):
    items: list[TaskLogResponse]
    total: int
