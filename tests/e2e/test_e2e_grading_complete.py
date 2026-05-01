"""End-to-end test that actually completes grading and verifies results.

The existing test_full_flow.py stops at the "pending task" stage. This file
mocks the grading service so we can verify the COMPLETE pipeline:
  register → login → project → answer key → questions → exams → grade → results

Plus integration tests for:
  - Concurrent grade-all rejection
  - File size validation
  - Bulk enrollment workflow
"""
import io
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.models.exam_answer import ExamAnswer
from app.models.project import ProjectStatus
from app.models.question import Question
from app.services.storage import LocalStorageService
from tests.conftest import TestingSessionLocal


def _seed_questions(db, project_id: str, count: int = 3) -> list:
    """Helper: create confirmed questions directly in DB to skip OCR step."""
    questions = []
    for i in range(1, count + 1):
        q = Question(
            id=str(uuid4()),
            project_id=project_id,
            question_number=i,
            question_text=f"Pregunta {i}",
            correct_answer=f"Respuesta {i}",
            points=10.0,
            is_confirmed=True,
        )
        db.add(q)
        questions.append(q)
    db.commit()
    for q in questions:
        db.refresh(q)
    return questions


def _make_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.e2e
class TestE2ECompleteGradingFlow:
    """Full pipeline including actual grading completion."""

    def test_register_login_project_grade_results(
        self,
        client: TestClient,
        db,
        temp_storage: LocalStorageService,
    ) -> None:
        # 1. Register
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "email": "prof@test.edu",
                "password": "Strong123pass",
                "full_name": "Prof Tester",
            },
        )
        assert resp.status_code == 201

        # 2. Login
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "prof@test.edu", "password": "Strong123pass"},
        )
        assert resp.status_code == 200
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        # 3. Create project
        resp = client.post(
            "/api/v1/projects/",
            headers=headers,
            json={
                "name": "Final Math",
                "description": "Final exam",
                "subject": "Math",
                "config": {
                    "exam_type": "open_ended",
                    "total_questions": 3,
                    "points_per_question": 10.0,
                    "has_multiple_pages": False,
                },
            },
        )
        assert resp.status_code == 201
        project_id = resp.json()["id"]

        # 4. Seed confirmed questions (skip OCR mocking; that's covered elsewhere)
        _seed_questions(db, project_id, count=3)

        # Mark project as confirmed
        from app.models.project import Project
        project = db.query(Project).filter(Project.id == project_id).first()
        project.status = ProjectStatus.CONFIRMED.value
        db.commit()

        # 5. Upload 2 student exams
        exam_ids = []
        for student in ["alice", "bob"]:
            content = f"%PDF-1.4 exam for {student}".encode()
            resp = client.post(
                f"/api/v1/projects/{project_id}/exams/upload",
                headers=headers,
                files={"files": (f"{student}.pdf", io.BytesIO(content), "application/pdf")},
            )
            assert resp.status_code == 201
            exam_ids.append(resp.json()[0]["id"])

        # 6. Grade all (with mocked grading service + test DB session)
        from app.api import grading as grading_module

        with patch.object(grading_module, "GradingService") as mock_svc_cls, \
             patch.object(grading_module, "SessionLocal", TestingSessionLocal), \
             patch.object(grading_module.settings, "OPENAI_API_KEY", "sk-test"):
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc

            def fake_grade(bg_db, exam, qs):
                exam.status = "graded"
                exam.total_score = 25.0
                exam.max_score = 30.0
                exam.grade_percentage = 83.33
                exam.graded_at = datetime.now(UTC)
                for q in qs:
                    bg_db.add(ExamAnswer(
                        id=str(uuid4()),
                        student_exam_id=exam.id,
                        question_id=q.id,
                        extracted_answer="answer",
                        is_correct=True,
                        score=q.points,
                        max_score=q.points,
                        feedback="Good",
                        confidence=0.9,
                    ))
                bg_db.commit()
                return exam

            mock_svc.grade_exam.side_effect = fake_grade

            resp = client.post(
                f"/api/v1/projects/{project_id}/grading/grade-all",
                headers=headers,
            )
            assert resp.status_code == 200
            task = resp.json()
            assert task["task_type"] == "grading"

        # 7. Wait for task (TestClient runs background tasks before returning)
        resp = client.get(f"/api/v1/tasks/{task['id']}", headers=headers)
        assert resp.status_code == 200
        # Task should have completed
        final_task = resp.json()
        assert final_task["status"] in ("completed", "processing", "pending")

        # 8. Verify summary shows graded
        resp = client.get(
            f"/api/v1/projects/{project_id}/grading/summary",
            headers=headers,
        )
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["total_exams"] == 2
        # Both should be graded since the mock ran
        assert summary["graded_count"] == 2
        assert summary["error_count"] == 0
        assert summary["average_percentage"] == pytest.approx(83.33, rel=0.01)

        # 9. Export
        resp = client.get(
            f"/api/v1/projects/{project_id}/grading/export",
            headers=headers,
        )
        assert resp.status_code == 200
        export = resp.json()
        assert len(export["results"]) == 2
        assert all(r["status"] == "graded" for r in export["results"])

        # 10. Analytics endpoint sanity
        resp = client.get(
            f"/api/v1/analytics/projects/{project_id}",
            headers=headers,
        )
        assert resp.status_code == 200
        analytics = resp.json()
        assert analytics["total_exams"] == 2
        assert analytics["graded_count"] == 2


@pytest.mark.e2e
class TestE2EClassFlow:
    """Bulk enrollment + class project + per-student grading workflow."""

    def test_bulk_enrollment_to_gradebook(
        self,
        client: TestClient,
        db,
        temp_storage: LocalStorageService,
    ) -> None:
        # 1. Register & login
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "classprof@test.edu",
                "password": "Strong123pass",
                "full_name": "Class Prof",
            },
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "classprof@test.edu", "password": "Strong123pass"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        # 2. Create class
        resp = client.post(
            "/api/v1/classes/",
            headers=headers,
            json={
                "name": "Estadistica 2026-1",
                "subject": "Estadistica",
                "semester": "2026-1",
                "description": "Curso E2E",
            },
        )
        assert resp.status_code == 201
        class_id = resp.json()["id"]

        # 3. Bulk enroll students (UTP-style xlsx)
        rows = [
            ["", None, None, "Listado"],
            [None, None, None, None],
            ["Documento", "Nombres", "Telefono", "EMAIL"],
            ["1001", "ALICE TESTER", "111", "alice@test.edu"],
            ["1002", "BOB TESTER", "222", "bob@test.edu"],
        ]
        content = _make_xlsx(rows)
        resp = client.post(
            f"/api/v1/classes/{class_id}/enrollments/bulk",
            headers=headers,
            files={
                "file": (
                    "roster.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["added"] == 2

        # 4. Verify enrollments listed
        resp = client.get(
            f"/api/v1/classes/{class_id}/enrollments", headers=headers
        )
        assert resp.status_code == 200
        enrollments = resp.json()
        assert len(enrollments) == 2
        identifiers = {e["student_identifier"] for e in enrollments}
        assert identifiers == {"1001", "1002"}

        # 5. Create project + confirmed questions
        resp = client.post(
            "/api/v1/projects/",
            headers=headers,
            json={
                "name": "Quiz 1",
                "description": "Primer quiz",
                "subject": "Estadistica",
                "config": {
                    "exam_type": "mixed",
                    "total_questions": 2,
                    "points_per_question": 5.0,
                    "has_multiple_pages": False,
                },
            },
        )
        project_id = resp.json()["id"]

        _seed_questions(db, project_id, count=2)
        from app.models.project import Project
        project = db.query(Project).filter(Project.id == project_id).first()
        project.status = ProjectStatus.CONFIRMED.value
        db.commit()

        # 6. Link project to class
        resp = client.post(
            f"/api/v1/classes/{class_id}/projects",
            headers=headers,
            json={"project_id": project_id},
        )
        assert resp.status_code == 201

        # 7. Per-student exam upload
        for stu_id, stu_name in [("1001", "ALICE TESTER"), ("1002", "BOB TESTER")]:
            content = f"%PDF-1.4 exam {stu_name}".encode()
            resp = client.post(
                f"/api/v1/projects/{project_id}/exams/upload",
                headers=headers,
                files={"files": (f"{stu_id}.pdf", io.BytesIO(content), "application/pdf")},
                data={"student_name": stu_name, "student_identifier": stu_id},
            )
            assert resp.status_code == 201

        # 8. Grade all (mocked + test DB)
        from app.api import grading as grading_module

        with patch.object(grading_module, "GradingService") as mock_svc_cls, \
             patch.object(grading_module, "SessionLocal", TestingSessionLocal), \
             patch.object(grading_module.settings, "OPENAI_API_KEY", "sk-test"):
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc

            def fake_grade(bg_db, exam, qs):
                exam.status = "graded"
                exam.total_score = 8.0
                exam.max_score = 10.0
                exam.grade_percentage = 80.0
                exam.graded_at = datetime.now(UTC)
                bg_db.commit()
                return exam

            mock_svc.grade_exam.side_effect = fake_grade

            resp = client.post(
                f"/api/v1/projects/{project_id}/grading/grade-all",
                headers=headers,
            )
            assert resp.status_code == 200

        # 9. Gradebook should match enrolled students
        resp = client.get(
            f"/api/v1/classes/{class_id}/gradebook", headers=headers
        )
        assert resp.status_code == 200
        gradebook = resp.json()
        assert len(gradebook["rows"]) == 2
        student_ids_in_gradebook = {r["student_identifier"] for r in gradebook["rows"]}
        assert student_ids_in_gradebook == {"1001", "1002"}


@pytest.mark.e2e
class TestE2EConcurrentGradeAll:
    """Verify grade-all is rejected when another grading task is active."""

    def test_concurrent_grade_all_returns_409(
        self,
        client: TestClient,
        db,
        confirmed_project_with_questions,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        from app.models.task_log import TaskLog

        project, _ = confirmed_project_with_questions

        # Upload one exam so grade-all has something to do
        client.post(
            f"/api/v1/projects/{project.id}/exams/upload",
            headers=auth_headers,
            files={"files": ("e.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        )

        # Seed an active TaskLog (simulates in-flight grading)
        active = TaskLog(
            id=str(uuid4()),
            user_id=list(db.query(__import__("app.models.user", fromlist=["User"]).User).all())[0].id,
            task_type="grading",
            status="processing",
            progress=50.0,
            project_id=project.id,
        )
        db.add(active)
        db.commit()

        with patch.object(
            __import__("app.api.grading", fromlist=["settings"]).settings,
            "OPENAI_API_KEY",
            "sk-test",
        ):
            resp = client.post(
                f"/api/v1/projects/{project.id}/grading/grade-all",
                headers=auth_headers,
            )

        assert resp.status_code == 409
        assert "calificacion en curso" in resp.json()["detail"].lower()


@pytest.mark.e2e
class TestE2EFileSizeValidation:
    """Verify oversized files are rejected with 413."""

    def test_bulk_enroll_rejects_oversized_file(
        self,
        client: TestClient,
        test_class,
        auth_headers: dict,
    ) -> None:
        with patch("app.api.classes.settings") as mock_settings:
            mock_settings.MAX_FILE_SIZE_MB = 1  # 1MB limit
            mock_settings.OPENAI_API_KEY = ""
            mock_settings.RATE_LIMIT_UPLOAD = "100/minute"
            # Create a 2MB payload
            big = b"x" * (2 * 1024 * 1024)
            resp = client.post(
                f"/api/v1/classes/{test_class.id}/enrollments/bulk",
                headers=auth_headers,
                files={"file": ("big.csv", big, "text/csv")},
            )
            assert resp.status_code == 413
            assert "tamaño" in resp.json()["detail"].lower() or "size" in resp.json()["detail"].lower()
