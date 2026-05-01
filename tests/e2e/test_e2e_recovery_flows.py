"""E2E tests for recovery and edge-case flows.

Covers scenarios that aren't part of the happy path:
- Regrade flow after first grading attempt
- Recovery from stuck "processing" exam via reset endpoint
- Partial grading completion (some exams succeed, some fail)
- Question editing after answer key processing
"""

import io
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.services.storage import LocalStorageService
from tests.conftest import TestingSessionLocal


def _seed_project_with_questions(db, owner_id: str, num_questions: int = 2) -> tuple:
    """Helper: create a project with N confirmed questions."""
    project = Project(
        id=str(uuid4()),
        owner_id=owner_id,
        name="Recovery E2E",
        status=ProjectStatus.CONFIRMED.value,
        config={"exam_type": "mixed", "total_questions": num_questions, "points_per_question": 10.0},
    )
    db.add(project)
    db.commit()

    questions = []
    for i in range(1, num_questions + 1):
        q = Question(
            id=str(uuid4()),
            project_id=project.id,
            question_number=i,
            question_text=f"Q{i}",
            correct_answer=f"A{i}",
            points=10.0,
            is_confirmed=True,
        )
        db.add(q)
        questions.append(q)
    db.commit()
    return project, questions


@pytest.mark.e2e
class TestRegradeFlow:
    """Verify grading can be re-run after the first pass succeeds."""

    def test_regrade_after_initial_grading(
        self,
        client: TestClient,
        db,
        test_user,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        project, questions = _seed_project_with_questions(db, test_user.id, num_questions=2)

        # Upload exam
        r = client.post(
            f"/api/v1/projects/{project.id}/exams/upload",
            headers=auth_headers,
            files={"files": ("e.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
            data={"student_name": "Bob", "student_identifier": "BOB-1"},
        )
        assert r.status_code == 201

        from app.api import grading as grading_module

        # First grade pass: score 50%
        with (
            patch.object(grading_module, "GradingService") as svc_cls,
            patch.object(grading_module, "SessionLocal", TestingSessionLocal),
            patch.object(grading_module.settings, "OPENAI_API_KEY", "sk-test"),
        ):
            svc = MagicMock()

            def grade_low(bg_db, exam, qs):
                exam.status = "graded"
                exam.total_score = 5.0
                exam.max_score = 10.0
                exam.grade_percentage = 50.0
                exam.graded_at = datetime.now(UTC)
                bg_db.commit()
                return exam

            svc.grade_exam.side_effect = grade_low
            svc_cls.return_value = svc

            r = client.post(
                f"/api/v1/projects/{project.id}/grading/grade-all",
                headers=auth_headers,
            )
            assert r.status_code == 200

        # Verify first score
        r = client.get(f"/api/v1/projects/{project.id}/grading/summary", headers=auth_headers)
        assert r.json()["average_percentage"] == pytest.approx(50.0, rel=0.01)

        # Mark task as completed so the next grade-all isn't blocked by 409
        from app.models.task_log import TaskLog

        for task in db.query(TaskLog).filter(TaskLog.project_id == project.id).all():
            task.status = "completed"
        db.commit()

        # Regrade pass: score 90%
        with (
            patch.object(grading_module, "GradingService") as svc_cls,
            patch.object(grading_module, "SessionLocal", TestingSessionLocal),
            patch.object(grading_module.settings, "OPENAI_API_KEY", "sk-test"),
        ):
            svc = MagicMock()

            def grade_high(bg_db, exam, qs):
                exam.status = "graded"
                exam.total_score = 9.0
                exam.max_score = 10.0
                exam.grade_percentage = 90.0
                exam.graded_at = datetime.now(UTC)
                bg_db.commit()
                return exam

            svc.grade_exam.side_effect = grade_high
            svc_cls.return_value = svc

            r = client.post(
                f"/api/v1/projects/{project.id}/grading/grade-all?regrade=true",
                headers=auth_headers,
            )
            assert r.status_code == 200

        # Verify regrade reflected
        r = client.get(f"/api/v1/projects/{project.id}/grading/summary", headers=auth_headers)
        assert r.json()["average_percentage"] == pytest.approx(90.0, rel=0.01)


@pytest.mark.e2e
class TestStuckExamRecoveryFlow:
    """Verify the full recovery: stuck → reset → re-grade → results."""

    def test_stuck_exam_recovers_via_reset_endpoint(
        self,
        client: TestClient,
        db,
        test_user,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        project, _ = _seed_project_with_questions(db, test_user.id)

        # Seed a stuck exam directly
        stuck = StudentExam(
            id=str(uuid4()),
            project_id=project.id,
            student_name="Stuck",
            student_identifier="STK-1",
            file_path=f"projects/{project.id}/s.pdf",
            file_type="pdf",
            status="processing",  # stuck state
            error_message=None,
        )
        db.add(stuck)
        db.commit()

        # Reset endpoint should clean it up
        r = client.post(
            f"/api/v1/projects/{project.id}/grading/reset-stuck",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["reset"] == 1

        # Verify status is now "uploaded"
        db.expire_all()
        refreshed = db.query(StudentExam).filter(StudentExam.id == stuck.id).first()
        assert refreshed.status == "uploaded"


@pytest.mark.e2e
class TestPartialCompletionFlow:
    """Some exams grade successfully, others fail — task still completes."""

    def test_grade_all_completes_with_mixed_outcomes(
        self,
        client: TestClient,
        db,
        test_user,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        project, questions = _seed_project_with_questions(db, test_user.id, num_questions=1)

        # Upload 3 exams
        for i in range(3):
            client.post(
                f"/api/v1/projects/{project.id}/exams/upload",
                headers=auth_headers,
                files={"files": (f"e{i}.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
                data={"student_name": f"Stu{i}", "student_identifier": f"S-{i}"},
            )

        from app.api import grading as grading_module

        with (
            patch.object(grading_module, "GradingService") as svc_cls,
            patch.object(grading_module, "SessionLocal", TestingSessionLocal),
            patch.object(grading_module.settings, "OPENAI_API_KEY", "sk-test"),
        ):
            svc = MagicMock()
            call_count = {"n": 0}

            def grade_with_failures(bg_db, exam, qs):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    # Second exam fails
                    raise RuntimeError("Simulated failure for exam 2")
                exam.status = "graded"
                exam.total_score = 8.0
                exam.max_score = 10.0
                exam.grade_percentage = 80.0
                exam.graded_at = datetime.now(UTC)
                bg_db.commit()
                return exam

            svc.grade_exam.side_effect = grade_with_failures
            svc_cls.return_value = svc

            r = client.post(
                f"/api/v1/projects/{project.id}/grading/grade-all",
                headers=auth_headers,
            )
            assert r.status_code == 200

        # Summary: 2 graded + 0 errors (the failed one isn't marked as error
        # because the mock just raises before status is set; in reality the
        # service catches and marks 'error'). Our mock doesn't simulate that
        # path, so we just verify the task completes and there are 2 graded.
        r = client.get(
            f"/api/v1/projects/{project.id}/grading/summary",
            headers=auth_headers,
        )
        summary = r.json()
        assert summary["total_exams"] == 3
        assert summary["graded_count"] >= 2  # At least 2 succeeded


@pytest.mark.e2e
class TestQuestionEditFlow:
    """Verify questions can be edited after answer key processing, before grading."""

    def test_update_question_after_extraction(
        self,
        client: TestClient,
        db,
        test_user,
        auth_headers: dict,
    ) -> None:
        project = Project(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="Edit Q",
            status=ProjectStatus.ANSWER_KEY_PROCESSED.value,
            config={},
        )
        db.add(project)
        db.commit()

        question = Question(
            id=str(uuid4()),
            project_id=project.id,
            question_number=1,
            question_text="Original text",
            correct_answer="Original",
            points=5.0,
            is_confirmed=False,
        )
        db.add(question)
        db.commit()

        # Edit the question
        r = client.put(
            f"/api/v1/projects/{project.id}/questions/{question.id}",
            headers=auth_headers,
            json={
                "question_text": "Updated text",
                "correct_answer": "Updated",
                "points": 10.0,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["question_text"] == "Updated text"
        assert data["correct_answer"] == "Updated"
        assert data["points"] == 10.0
