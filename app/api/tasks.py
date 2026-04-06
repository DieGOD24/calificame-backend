from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.task_log import TaskLog
from app.models.user import User, UserRole
from app.schemas.task_log import TaskLogListResponse, TaskLogResponse

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("/", response_model=TaskLogListResponse)
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    task_type: str | None = Query(None, description="Filter by task type"),
    task_status: str | None = Query(None, alias="status", description="Filter by status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """List current user's tasks with optional filters."""
    logger.info(f"User {current_user.id} listing tasks (page={page})")

    query = db.query(TaskLog).filter(TaskLog.user_id == current_user.id)

    if task_type:
        query = query.filter(TaskLog.task_type == task_type)
    if task_status:
        query = query.filter(TaskLog.status == task_status)

    total = query.count()
    tasks = query.order_by(TaskLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return {"items": tasks, "total": total}


@router.get("/{task_id}", response_model=TaskLogResponse)
def get_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> TaskLog:
    """Get task status and progress."""
    task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    # Only own tasks, unless Developer or Admin
    if current_user.role not in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        if task.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    logger.info(f"User {current_user.id} retrieved task {task_id} (status={task.status})")
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    """Cancel a pending task."""
    task = db.query(TaskLog).filter(TaskLog.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if task.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel task with status '{task.status}'",
        )

    task.status = "failed"
    task.error_message = "Cancelled by user"
    db.commit()

    logger.info(f"User {current_user.id} cancelled task {task_id}")
