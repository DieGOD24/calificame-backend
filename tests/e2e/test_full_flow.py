import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models.exam_answer import ExamAnswer
from app.models.project import ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.services.storage import LocalStorageService


@pytest.mark.e2e
class TestCompleteGradingFlow:
    """End-to-end test of the full exam grading workflow."""

    def test_complete_grading_flow(
        self,
        client: TestClient,
        temp_storage: LocalStorageService,
    ) -> None:
        # ====== Step 1: Register user ======
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "teacher@school.edu",
                "password": "securepassword123",
                "full_name": "Professor Smith",
            },
        )
        assert register_response.status_code == 201
        user_data = register_response.json()
        assert user_data["email"] == "teacher@school.edu"

        # ====== Step 2: Login ======
        login_response = client.post(
            "/api/v1/auth/login",
            json={"email": "teacher@school.edu", "password": "securepassword123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # ====== Step 3: Create project ======
        project_response = client.post(
            "/api/v1/projects/",
            headers=headers,
            json={
                "name": "Algebra Mid-Term",
                "description": "Mid-term exam for Algebra 101",
                "subject": "Mathematics",
                "config": {
                    "exam_type": "mixed",
                    "total_questions": 3,
                    "points_per_question": 10.0,
                    "has_multiple_pages": False,
                },
            },
        )
        assert project_response.status_code == 201
        project = project_response.json()
        project_id = project["id"]
        assert project["status"] == "draft"

        # ====== Step 4: Upload answer key ======
        answer_key_pdf = b"%PDF-1.4 fake answer key with questions and answers"
        upload_response = client.post(
            f"/api/v1/projects/{project_id}/answer-key/upload",
            headers=headers,
            files={"file": ("answer_key.pdf", io.BytesIO(answer_key_pdf), "application/pdf")},
        )
        assert upload_response.status_code == 201
        assert upload_response.json()["is_processed"] is False

        # Verify project status updated
        proj_check = client.get(f"/api/v1/projects/{project_id}", headers=headers)
        assert proj_check.json()["status"] == "answer_key_uploaded"

        # ====== Step 5: Process answer key (mocked OCR) ======
        mock_questions = [
            {"question_number": 1, "question_text": "Solve: 2x + 3 = 7", "correct_answer": "x = 2"},
            {"question_number": 2, "question_text": "Factor: x^2 - 4", "correct_answer": "(x+2)(x-2)"},
            {"question_number": 3, "question_text": "What is sqrt(144)?", "correct_answer": "12"},
        ]

        with patch("app.api.answer_keys.DocumentProcessor") as mock_proc_cls:
            mock_proc = MagicMock()
            mock_proc_cls.return_value = mock_proc

            def fake_process_ak(db, answer_key, proj):
                answer_key.is_processed = True
                answer_key.num_pages = 1
                answer_key.processed_data = {"raw_text": "...", "extracted_questions": mock_questions}

                db.query(Question).filter(Question.project_id == proj.id).delete()

                questions = []
                for qa in mock_questions:
                    q = Question(
                        id=str(uuid4()),
                        project_id=proj.id,
                        question_number=qa["question_number"],
                        question_text=qa["question_text"],
                        correct_answer=qa["correct_answer"],
                        points=10.0,
                        is_confirmed=False,
                    )
                    db.add(q)
                    questions.append(q)

                proj.status = ProjectStatus.ANSWER_KEY_PROCESSED.value
                db.commit()
                for q in questions:
                    db.refresh(q)
                return questions

            mock_proc.process_answer_key.side_effect = fake_process_ak

            process_response = client.post(
                f"/api/v1/projects/{project_id}/answer-key/process",
                headers=headers,
            )
            assert process_response.status_code == 200
            processed = process_response.json()
            assert processed["is_processed"] is True
            assert len(processed["questions"]) == 3

        # ====== Step 6: Confirm questions (human in the loop) ======
        # First, list the questions
        questions_response = client.get(
            f"/api/v1/projects/{project_id}/questions/",
            headers=headers,
        )
        assert questions_response.status_code == 200
        questions = questions_response.json()
        assert len(questions) == 3
        assert all(q["is_confirmed"] is False for q in questions)

        # Confirm all questions at once
        confirm_response = client.post(
            f"/api/v1/projects/{project_id}/questions/confirm-all",
            headers=headers,
            json={"confirm_all": True},
        )
        assert confirm_response.status_code == 200
        confirmed = confirm_response.json()
        assert len(confirmed) == 3
        assert all(q["is_confirmed"] is True for q in confirmed)

        # Verify project status
        proj_check = client.get(f"/api/v1/projects/{project_id}", headers=headers)
        assert proj_check.json()["status"] == "confirmed"

        # ====== Step 7: Upload student exams ======
        student_exam_ids = []
        for student_name in ["alice_exam.pdf", "bob_exam.pdf"]:
            exam_content = f"%PDF-1.4 fake student exam for {student_name}".encode()
            upload_resp = client.post(
                f"/api/v1/projects/{project_id}/exams/upload",
                headers=headers,
                files={"files": (student_name, io.BytesIO(exam_content), "application/pdf")},
            )
            assert upload_resp.status_code == 201
            student_exam_ids.append(upload_resp.json()[0]["id"])

        # Verify exams listed
        exams_list = client.get(f"/api/v1/projects/{project_id}/exams/", headers=headers)
        assert exams_list.json()["total"] == 2

        # ====== Step 8: Grade all exams (mocked AI) ======
        question_ids = [q["id"] for q in questions]

        with patch("app.api.grading.GradingService") as mock_grade_cls:
            mock_grade_svc = MagicMock()
            mock_grade_cls.return_value = mock_grade_svc

            def fake_grade_all(db, proj, regrade=False):
                exams = (
                    db.query(StudentExam)
                    .filter(StudentExam.project_id == proj.id)
                    .all()
                )
                qs = (
                    db.query(Question)
                    .filter(Question.project_id == proj.id, Question.is_confirmed.is_(True))
                    .order_by(Question.question_number)
                    .all()
                )

                # Simulate grading: Alice gets 90%, Bob gets 70%
                scores = {student_exam_ids[0]: [10, 10, 7], student_exam_ids[1]: [10, 7, 4]}

                for exam in exams:
                    exam_scores = scores.get(exam.id, [5, 5, 5])
                    total = 0.0
                    for i, q in enumerate(qs):
                        s = float(exam_scores[i]) if i < len(exam_scores) else 0.0
                        answer = ExamAnswer(
                            id=str(uuid4()),
                            student_exam_id=exam.id,
                            question_id=q.id,
                            extracted_answer=f"student answer {i+1}",
                            is_correct=s == q.points,
                            score=s,
                            max_score=q.points,
                            feedback="Good" if s == q.points else "Partially correct",
                            confidence=0.9,
                        )
                        db.add(answer)
                        total += s

                    exam.total_score = total
                    exam.max_score = 30.0
                    exam.grade_percentage = (total / 30.0) * 100
                    exam.status = "graded"
                    exam.graded_at = datetime.now(timezone.utc)

                proj.status = ProjectStatus.COMPLETED.value
                db.commit()
                for e in exams:
                    db.refresh(e)
                return exams

            mock_grade_svc.grade_all_exams.side_effect = fake_grade_all

            grade_response = client.post(
                f"/api/v1/projects/{project_id}/grading/grade-all",
                headers=headers,
            )
            assert grade_response.status_code == 200
            graded = grade_response.json()
            assert len(graded) == 2
            assert all(e["status"] == "graded" for e in graded)

        # ====== Step 9: Get results ======
        # Summary
        summary_response = client.get(
            f"/api/v1/projects/{project_id}/grading/summary",
            headers=headers,
        )
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["total_exams"] == 2
        assert summary["graded_count"] == 2
        assert summary["pending_count"] == 0

        # Individual exam result
        exam_detail = client.get(
            f"/api/v1/projects/{project_id}/exams/{student_exam_ids[0]}",
            headers=headers,
        )
        assert exam_detail.status_code == 200
        detail = exam_detail.json()
        assert detail["student_exam"]["status"] == "graded"
        assert len(detail["answers"]) == 3

        # ====== Step 10: Verify scores ======
        # Alice: 10 + 10 + 7 = 27/30 = 90%
        alice_exam = next(e for e in graded if e["id"] == student_exam_ids[0])
        assert alice_exam["total_score"] == 27.0
        assert alice_exam["max_score"] == 30.0
        assert abs(alice_exam["grade_percentage"] - 90.0) < 0.1

        # Bob: 10 + 7 + 4 = 21/30 = 70%
        bob_exam = next(e for e in graded if e["id"] == student_exam_ids[1])
        assert bob_exam["total_score"] == 21.0
        assert bob_exam["max_score"] == 30.0
        assert abs(bob_exam["grade_percentage"] - 70.0) < 0.1

        # Export
        export_response = client.get(
            f"/api/v1/projects/{project_id}/grading/export",
            headers=headers,
        )
        assert export_response.status_code == 200
        export_data = export_response.json()
        assert export_data["project"]["name"] == "Algebra Mid-Term"
        assert len(export_data["questions"]) == 3
        assert len(export_data["results"]) == 2
