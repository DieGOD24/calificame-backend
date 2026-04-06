import io
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.models.exam_answer import ExamAnswer
from app.models.project import Project
from app.models.question import Question
from app.services.storage import LocalStorageService


def _create_student_exam(
    client: TestClient,
    project_id: str,
    auth_headers: dict,
    temp_storage: LocalStorageService,
) -> str:
    """Helper to upload a student exam and return its id."""
    response = client.post(
        f"/api/v1/projects/{project_id}/exams/upload",
        headers=auth_headers,
        files={"files": ("student.pdf", io.BytesIO(b"%PDF-1.4 content"), "application/pdf")},
    )
    return response.json()[0]["id"]


class TestGradeSingleExam:
    def test_grade_single_exam(
        self,
        client: TestClient,
        confirmed_project_with_questions: tuple[Project, list[Question]],
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        project, questions = confirmed_project_with_questions
        exam_id = _create_student_exam(client, project.id, auth_headers, temp_storage)

        # Mock the grading service
        with patch("app.api.grading.GradingService") as mock_svc_cls:
            mock_svc = MagicMock()
            mock_svc_cls.return_value = mock_svc

            def fake_grade(db, exam, qs):
                from datetime import UTC, datetime

                exam.total_score = 8.0
                exam.max_score = 10.0
                exam.grade_percentage = 80.0
                exam.status = "graded"
                exam.graded_at = datetime.now(UTC)

                # Create answers
                for q in qs:
                    is_correct = q.question_number <= 4
                    answer = ExamAnswer(
                        id=str(uuid4()),
                        student_exam_id=exam.id,
                        question_id=q.id,
                        extracted_answer=str(q.question_number * 2) if is_correct else "wrong",
                        is_correct=is_correct,
                        score=q.points if is_correct else 0.0,
                        max_score=q.points,
                        feedback="Correct!" if is_correct else "Incorrect.",
                        confidence=0.95,
                    )
                    db.add(answer)

                db.commit()
                db.refresh(exam)
                return exam

            mock_svc.grade_exam.side_effect = fake_grade

            response = client.post(
                f"/api/v1/projects/{project.id}/grading/grade/{exam_id}",
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["student_exam"]["status"] == "graded"
            assert data["student_exam"]["total_score"] == 8.0
            assert data["student_exam"]["grade_percentage"] == 80.0
            assert len(data["answers"]) == 5

    def test_grade_without_confirmed_questions(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        exam_id = _create_student_exam(client, test_project.id, auth_headers, temp_storage)

        response = client.post(
            f"/api/v1/projects/{test_project.id}/grading/grade/{exam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "confirmed questions" in response.json()["detail"].lower()


class TestGradeAllExams:
    def test_grade_all_exams(
        self,
        client: TestClient,
        confirmed_project_with_questions: tuple[Project, list[Question]],
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        project, questions = confirmed_project_with_questions

        # Upload two exams
        for _ in range(2):
            _create_student_exam(client, project.id, auth_headers, temp_storage)

        response = client.post(
            f"/api/v1/projects/{project.id}/grading/grade-all",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # grade-all now returns a TaskLog for background processing
        assert data["task_type"] == "grading"
        assert data["status"] == "pending"
        assert data["project_id"] == project.id
        assert data["progress"] == 0.0
        assert "id" in data


class TestGradingSummary:
    def test_get_grading_summary(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/projects/{test_project.id}/grading/summary",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == test_project.id
        assert data["total_exams"] == 0
        assert data["graded_count"] == 0
        assert data["average_score"] is None


class TestExportResults:
    def test_export_results(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/projects/{test_project.id}/grading/export",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project"]["id"] == test_project.id
        assert "questions" in data
        assert "results" in data
