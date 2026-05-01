"""Cross-user authorization regression suite.

For every protected resource (project, class, exam, task, etc.), verify that
user B cannot read, modify, or delete user A's data. This prevents future
regressions from accidentally exposing data through new endpoints.
"""

import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models.clase import Class, ClassEnrollment
from app.models.project import Project, ProjectStatus
from app.models.student_exam import StudentExam
from app.models.task_log import TaskLog
from app.models.user import User
from app.services.storage import LocalStorageService


@pytest.fixture()
def user_a_resources(
    db,
    test_user: User,
    auth_headers: dict,
    client: TestClient,
    temp_storage: LocalStorageService,
) -> dict:
    """Create a project + exam + class + enrollment + task owned by test_user."""
    project = Project(
        id=str(uuid4()),
        owner_id=test_user.id,
        name="User A Project",
        status=ProjectStatus.DRAFT.value,
        config={},
    )
    clase = Class(
        id=str(uuid4()),
        professor_id=test_user.id,
        name="User A Class",
        subject="Math",
        semester="2026-1",
        is_active=True,
    )
    db.add_all([project, clase])
    db.commit()

    exam = StudentExam(
        id=str(uuid4()),
        project_id=project.id,
        student_name="Alice",
        student_identifier="A-1",
        file_path=f"projects/{project.id}/exam.pdf",
        file_type="pdf",
        status="uploaded",
    )
    enrollment = ClassEnrollment(
        id=str(uuid4()),
        class_id=clase.id,
        student_name="Alice",
        student_identifier="A-1",
    )
    task = TaskLog(
        id=str(uuid4()),
        user_id=test_user.id,
        task_type="grading",
        status="completed",
        progress=100.0,
        project_id=project.id,
    )
    db.add_all([exam, enrollment, task])
    db.commit()
    db.refresh(project)
    db.refresh(clase)
    db.refresh(exam)
    db.refresh(enrollment)
    db.refresh(task)

    return {
        "project_id": project.id,
        "class_id": clase.id,
        "exam_id": exam.id,
        "enrollment_id": enrollment.id,
        "task_id": task.id,
    }


class TestProjectCrossUserAccess:
    def test_user_b_cannot_read_user_a_project(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/projects/{user_a_resources['project_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_update_user_a_project(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.put(
            f"/api/v1/projects/{user_a_resources['project_id']}",
            headers=auth_headers_2,
            json={"name": "Hijacked"},
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_delete_user_a_project(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.delete(
            f"/api/v1/projects/{user_a_resources['project_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)


class TestExamCrossUserAccess:
    def test_user_b_cannot_read_user_a_exam(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/projects/{user_a_resources['project_id']}/exams/{user_a_resources['exam_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_list_user_a_exams(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/projects/{user_a_resources['project_id']}/exams",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_delete_user_a_exam(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.delete(
            f"/api/v1/projects/{user_a_resources['project_id']}/exams/{user_a_resources['exam_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_upload_to_user_a_project(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{user_a_resources['project_id']}/exams/upload",
            headers=auth_headers_2,
            files={"files": ("e.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        )
        assert r.status_code in (403, 404)


class TestClassCrossUserAccess:
    def test_user_b_cannot_read_user_a_class(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/classes/{user_a_resources['class_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_list_user_a_enrollments(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/classes/{user_a_resources['class_id']}/enrollments",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_bulk_enroll_to_user_a_class(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/classes/{user_a_resources['class_id']}/enrollments/bulk",
            headers=auth_headers_2,
            files={"file": ("r.csv", b"codigo,nombre,email\nX,Y,z@x.com\n", "text/csv")},
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_view_user_a_gradebook(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/classes/{user_a_resources['class_id']}/gradebook",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)


class TestTaskCrossUserAccess:
    def test_user_b_cannot_read_user_a_task(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/tasks/{user_a_resources['task_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)


class TestGradingCrossUserAccess:
    def test_user_b_cannot_grade_user_a_project(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{user_a_resources['project_id']}/grading/grade-all",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_view_user_a_summary(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/projects/{user_a_resources['project_id']}/grading/summary",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_reset_user_a_stuck_exams(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{user_a_resources['project_id']}/grading/reset-stuck",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)


class TestQuestionsCrossUserAccess:
    def test_user_b_cannot_list_user_a_questions(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/projects/{user_a_resources['project_id']}/questions",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_confirm_user_a_questions(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{user_a_resources['project_id']}/questions/confirm-all",
            headers=auth_headers_2,
            json={"confirm_all": True},
        )
        assert r.status_code in (403, 404)


class TestAnswerKeyCrossUserAccess:
    def test_user_b_cannot_upload_answer_key_to_user_a_project(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{user_a_resources['project_id']}/answer-key/upload",
            headers=auth_headers_2,
            files={"file": ("ak.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_process_user_a_answer_key(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{user_a_resources['project_id']}/answer-key/process",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)


class TestAnalyticsCrossUserAccess:
    def test_user_b_cannot_view_user_a_project_analytics(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/analytics/projects/{user_a_resources['project_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)

    def test_user_b_cannot_view_user_a_class_analytics(
        self, client: TestClient, user_a_resources: dict, auth_headers_2: dict
    ) -> None:
        r = client.get(
            f"/api/v1/analytics/classes/{user_a_resources['class_id']}",
            headers=auth_headers_2,
        )
        assert r.status_code in (403, 404)
