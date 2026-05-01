"""Tests for POST /projects/{id}/exams/generate-and-assign.

Covers happy paths (with and without auto-grade), 409 conflict + replace,
input validation, and ownership checks. The grading service is stubbed so
the FastAPI background task that TestClient runs synchronously does not
hit OpenAI.
"""

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

import app.api.student_exams as student_exams_mod
import app.services.grading as grading_mod
from app.config import settings
from app.models.project import Project
from app.services.storage import LocalStorageService
from tests.conftest import TestingSessionLocal


def _png(width: int = 400, height: int = 600, color: str = "white") -> bytes:
    """Build a real PNG so generate_pdf_from_images() can decode it."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def stub_grading_service(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Replace GradingService.grade_exam so the background auto-grade is deterministic.

    Also redirects the background task's `SessionLocal()` to the test engine so
    the table created by `_setup_db` is visible to the worker thread.
    """
    calls: list[str] = []

    def _fake_grade(self, db, exam, questions):  # type: ignore[no-untyped-def]
        calls.append(exam.id)
        exam.status = "graded"
        exam.total_score = 8.0
        exam.max_score = 10.0
        exam.grade_percentage = 80.0
        db.commit()
        return exam

    monkeypatch.setattr(grading_mod.GradingService, "grade_exam", _fake_grade)
    monkeypatch.setattr(student_exams_mod, "SessionLocal", TestingSessionLocal)
    yield calls


@pytest.fixture
def with_openai_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test-anything")
    yield


class TestHappyPath:
    def test_creates_exam_without_questions_skips_auto_grade(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
        stub_grading_service: list[str],
        with_openai_key: None,
    ) -> None:
        """No confirmed questions -> no background grade, status stays 'uploaded'."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/generate-and-assign",
            headers=auth_headers,
            files=[
                ("files", ("p1.png", io.BytesIO(_png()), "image/png")),
                ("files", ("p2.png", io.BytesIO(_png()), "image/png")),
            ],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["student_name"] == "Ana"
        assert body["student_identifier"] == "A1"
        assert body["file_type"] == "pdf"
        assert body["status"] == "uploaded"
        assert body["original_filename"] == "A1.pdf"
        assert stub_grading_service == []

    def test_creates_exam_and_runs_auto_grade(
        self,
        client: TestClient,
        confirmed_project_with_questions: tuple,
        auth_headers: dict,
        temp_storage: LocalStorageService,
        stub_grading_service: list[str],
        with_openai_key: None,
    ) -> None:
        """With confirmed questions + key, the background task runs and grades."""
        project, _ = confirmed_project_with_questions
        response = client.post(
            f"/api/v1/projects/{project.id}/exams/generate-and-assign",
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert response.status_code == 201, response.text
        exam_id = response.json()["id"]
        assert stub_grading_service == [exam_id]

        listed = client.get(
            f"/api/v1/projects/{project.id}/exams/", headers=auth_headers
        ).json()
        assert listed["graded_count"] == 1
        assert listed["items"][0]["status"] == "graded"

    def test_auto_grade_disabled_keeps_uploaded(
        self,
        client: TestClient,
        confirmed_project_with_questions: tuple,
        auth_headers: dict,
        temp_storage: LocalStorageService,
        stub_grading_service: list[str],
        with_openai_key: None,
    ) -> None:
        project, _ = confirmed_project_with_questions
        response = client.post(
            f"/api/v1/projects/{project.id}/exams/generate-and-assign",
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={
                "student_name": "Ana",
                "student_identifier": "A1",
                "auto_grade": "false",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "uploaded"
        assert stub_grading_service == []

    def test_missing_openai_key_skips_auto_grade(
        self,
        client: TestClient,
        confirmed_project_with_questions: tuple,
        auth_headers: dict,
        temp_storage: LocalStorageService,
        stub_grading_service: list[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
        project, _ = confirmed_project_with_questions
        response = client.post(
            f"/api/v1/projects/{project.id}/exams/generate-and-assign",
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "uploaded"
        assert stub_grading_service == []


class TestValidation:
    def test_non_image_rejected(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/generate-and-assign",
            headers=auth_headers,
            files=[("files", ("foo.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert response.status_code == 400
        assert "imagen procesada" in response.json()["detail"]

    def test_student_name_required(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/generate-and-assign",
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={"student_identifier": "A1"},
        )
        assert response.status_code == 422


class TestConflict:
    def test_existing_exam_returns_409_with_structured_detail(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        url = f"/api/v1/projects/{test_project.id}/exams/generate-and-assign"
        first = client.post(
            url,
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert first.status_code == 201
        existing_id = first.json()["id"]

        second = client.post(
            url,
            headers=auth_headers,
            files=[("files", ("p2.png", io.BytesIO(_png()), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert second.status_code == 409
        detail = second.json()["detail"]
        assert detail["code"] == "exam_exists"
        assert detail["existing_exam_id"] == existing_id

    def test_replace_existing_swaps_file_and_keeps_one_row(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
        db: Session,
    ) -> None:
        from app.models.student_exam import StudentExam

        url = f"/api/v1/projects/{test_project.id}/exams/generate-and-assign"
        first = client.post(
            url,
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png(color="red")), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert first.status_code == 201
        old_id = first.json()["id"]
        old_path = (
            db.query(StudentExam.file_path).filter(StudentExam.id == old_id).scalar()
        )
        assert old_path

        second = client.post(
            url,
            headers=auth_headers,
            files=[("files", ("p2.png", io.BytesIO(_png(color="blue")), "image/png"))],
            data={
                "student_name": "Ana Maria",
                "student_identifier": "A1",
                "replace_existing": "true",
            },
        )
        assert second.status_code == 201, second.text
        new_body = second.json()
        assert new_body["student_name"] == "Ana Maria"
        assert new_body["id"] != old_id

        rows = (
            db.query(StudentExam)
            .filter(
                StudentExam.project_id == test_project.id,
                StudentExam.student_identifier == "A1",
            )
            .all()
        )
        assert len(rows) == 1, "replace must keep exactly one row"

        old_resolved = temp_storage._safe_path(old_path)
        assert not old_resolved.exists(), "old file should be deleted from storage"

    def test_processing_exam_blocks_replace(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
        db: Session,
    ) -> None:
        from app.models.student_exam import StudentExam

        url = f"/api/v1/projects/{test_project.id}/exams/generate-and-assign"
        first = client.post(
            url,
            headers=auth_headers,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert first.status_code == 201
        # Force the exam into 'processing' to simulate an in-flight grade
        existing = (
            db.query(StudentExam)
            .filter(StudentExam.id == first.json()["id"])
            .one()
        )
        existing.status = "processing"
        db.commit()

        second = client.post(
            url,
            headers=auth_headers,
            files=[("files", ("p2.png", io.BytesIO(_png()), "image/png"))],
            data={
                "student_name": "Ana",
                "student_identifier": "A1",
                "replace_existing": "true",
            },
        )
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "exam_processing"


class TestOwnership:
    def test_other_user_cannot_assign(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers_2: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/generate-and-assign",
            headers=auth_headers_2,
            files=[("files", ("p1.png", io.BytesIO(_png()), "image/png"))],
            data={"student_name": "Ana", "student_identifier": "A1"},
        )
        assert response.status_code == 403
