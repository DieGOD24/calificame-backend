from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.task_log import TaskLog
from app.models.user import User


def _make_task(
    db: Session,
    user: User,
    task_type: str = "grading",
    task_status: str = "pending",
) -> TaskLog:
    """Helper to create a TaskLog record."""
    task = TaskLog(
        id=str(uuid4()),
        user_id=user.id,
        task_type=task_type,
        status=task_status,
        progress=0.0 if task_status == "pending" else 100.0,
        current_step="waiting" if task_status == "pending" else "done",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


class TestListTasks:
    def test_list_user_tasks(
        self, client: TestClient, db: Session, test_user: User, auth_headers: dict
    ) -> None:
        _make_task(db, test_user, task_type="grading")
        _make_task(db, test_user, task_type="ocr_extraction")
        response = client.get("/api/v1/tasks/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_empty_list(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/tasks/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_filter_by_type(
        self, client: TestClient, db: Session, test_user: User, auth_headers: dict
    ) -> None:
        _make_task(db, test_user, task_type="grading")
        _make_task(db, test_user, task_type="pdf_generation")
        response = client.get(
            "/api/v1/tasks/", headers=auth_headers, params={"task_type": "grading"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["task_type"] == "grading"


class TestGetTask:
    def test_get_by_id(
        self, client: TestClient, db: Session, test_user: User, auth_headers: dict
    ) -> None:
        task = _make_task(db, test_user)
        response = client.get(f"/api/v1/tasks/{task.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == task.id

    def test_not_found(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/tasks/nonexistent-id", headers=auth_headers)
        assert response.status_code == 404

    def test_other_user_task_forbidden(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_user_2: User,
        auth_headers_2: dict,
    ) -> None:
        task = _make_task(db, test_user)
        response = client.get(f"/api/v1/tasks/{task.id}", headers=auth_headers_2)
        assert response.status_code == 403


class TestCancelTask:
    def test_cancel_pending(
        self, client: TestClient, db: Session, test_user: User, auth_headers: dict
    ) -> None:
        task = _make_task(db, test_user, task_status="pending")
        response = client.delete(f"/api/v1/tasks/{task.id}", headers=auth_headers)
        assert response.status_code == 204

    def test_cannot_cancel_completed(
        self, client: TestClient, db: Session, test_user: User, auth_headers: dict
    ) -> None:
        task = _make_task(db, test_user, task_status="completed")
        response = client.delete(f"/api/v1/tasks/{task.id}", headers=auth_headers)
        assert response.status_code == 400
