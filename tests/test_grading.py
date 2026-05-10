import io
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.models.exam_answer import ExamAnswer
from app.models.project import Project
from app.models.question import Question
from app.services.storage import LocalStorageService
from tests.conftest import TestingSessionLocal


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


class TestRecoverStaleWork:
    def test_recover_stale_work_resets_processing_exams(
        self,
        db,
        test_project: Project,
    ) -> None:
        """Startup recovery should reset StudentExams stuck in 'processing'."""
        from app.main import _recover_stale_work
        from app.models.student_exam import StudentExam

        stuck = StudentExam(
            id=str(uuid4()),
            project_id=test_project.id,
            student_name="Stuck Student",
            file_path="/tmp/stuck.pdf",
            status="processing",
        )
        healthy = StudentExam(
            id=str(uuid4()),
            project_id=test_project.id,
            student_name="Healthy Student",
            file_path="/tmp/healthy.pdf",
            status="uploaded",
        )
        db.add(stuck)
        db.add(healthy)
        db.commit()
        stuck_id = stuck.id
        healthy_id = healthy.id

        with patch("app.database.SessionLocal", TestingSessionLocal):
            _recover_stale_work()

        db.expire_all()
        stuck_after = db.query(StudentExam).filter(StudentExam.id == stuck_id).first()
        healthy_after = db.query(StudentExam).filter(StudentExam.id == healthy_id).first()
        assert stuck_after.status == "uploaded"
        assert stuck_after.error_message is not None
        assert "interrumpido" in stuck_after.error_message.lower()
        assert healthy_after.status == "uploaded"
        assert healthy_after.error_message is None


class TestResetStuckEndpoint:
    def test_reset_stuck_endpoint(
        self,
        client: TestClient,
        db,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        """POST /grading/reset-stuck resets 'processing' exams and leaves others alone."""
        from app.models.student_exam import StudentExam

        for _ in range(2):
            db.add(
                StudentExam(
                    id=str(uuid4()),
                    project_id=test_project.id,
                    file_path="/tmp/p.pdf",
                    status="processing",
                )
            )
        db.add(
            StudentExam(
                id=str(uuid4()),
                project_id=test_project.id,
                file_path="/tmp/u.pdf",
                status="uploaded",
            )
        )
        db.commit()

        response = client.post(
            f"/api/v1/projects/{test_project.id}/grading/reset-stuck",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == {"reset": 2}

        remaining_stuck = (
            db.query(StudentExam)
            .filter(StudentExam.project_id == test_project.id, StudentExam.status == "processing")
            .count()
        )
        assert remaining_stuck == 0
        uploaded = (
            db.query(StudentExam)
            .filter(StudentExam.project_id == test_project.id, StudentExam.status == "uploaded")
            .count()
        )
        assert uploaded == 3

    def test_reset_stuck_rejects_other_user(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers_2: dict,
    ) -> None:
        """Another user cannot reset exams on a project they don't own."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/grading/reset-stuck",
            headers=auth_headers_2,
        )
        assert response.status_code in (403, 404)


class TestGradeAllIncludesProcessing:
    def test_grade_all_includes_processing_exams(
        self,
        client: TestClient,
        db,
        confirmed_project_with_questions: tuple[Project, list[Question]],
        auth_headers: dict,
    ) -> None:
        """grade-all should include exams stuck in 'processing' along with 'uploaded'."""
        from app.api import grading as grading_module
        from app.models.student_exam import StudentExam

        project, _ = confirmed_project_with_questions
        uploaded = StudentExam(
            id=str(uuid4()),
            project_id=project.id,
            file_path="/tmp/u.pdf",
            status="uploaded",
        )
        processing = StudentExam(
            id=str(uuid4()),
            project_id=project.id,
            file_path="/tmp/p.pdf",
            status="processing",
        )
        db.add(uploaded)
        db.add(processing)
        db.commit()

        graded_ids: list[str] = []

        def fake_grade(bg_db, exam, qs):
            from datetime import UTC, datetime

            exam.status = "graded"
            exam.total_score = 10.0
            exam.max_score = 10.0
            exam.grade_percentage = 100.0
            exam.graded_at = datetime.now(UTC)
            graded_ids.append(exam.id)
            bg_db.commit()
            return exam

        with (
            patch.object(grading_module, "GradingService") as mock_cls,
            patch.object(grading_module, "SessionLocal", TestingSessionLocal),
            patch.object(grading_module.settings, "OPENAI_API_KEY", "sk-test"),
        ):
            mock_svc = MagicMock()
            mock_svc.grade_exam.side_effect = fake_grade
            mock_cls.return_value = mock_svc

            response = client.post(
                f"/api/v1/projects/{project.id}/grading/grade-all",
                headers=auth_headers,
            )
            assert response.status_code == 200

        assert uploaded.id in graded_ids
        assert processing.id in graded_ids


class TestGradingTransactionSafety:
    def test_grade_exam_rolls_back_on_agent_failure(
        self,
        db,
        confirmed_project_with_questions,
        temp_storage,
    ) -> None:
        """If the grading agent raises, the exam ends in 'error' state, not stuck in 'processing'."""
        from app.models.student_exam import StudentExam
        from app.services.grading import GradingService

        project, questions = confirmed_project_with_questions

        # Seed a student exam
        exam = StudentExam(
            id=str(uuid4()),
            project_id=project.id,
            student_name="Test",
            file_path="/tmp/x.pdf",
            file_type="pdf",
            status="uploaded",
        )
        db.add(exam)
        db.commit()
        db.refresh(exam)

        # Make storage.get_file return something readable but break the agent
        with (
            patch("app.services.grading.GradingService.__init__", return_value=None) as _,
            patch("app.agents.grading_agent.GradingAgent") as mock_agent_cls,
        ):
            svc = GradingService.__new__(GradingService)
            svc.storage = MagicMock()
            svc.storage.get_file = MagicMock(return_value=b"%PDF-1.4 x")

            mock_agent = MagicMock()
            mock_agent.execute.side_effect = RuntimeError("AI service exploded")
            mock_agent_cls.return_value = mock_agent

            # Patch _pdf_to_images so the PDF conversion succeeds
            with patch("app.services.grading._pdf_to_images", return_value=[b"img"]):
                svc.grade_exam(db, exam, questions)

        db.expire_all()
        refreshed = db.query(StudentExam).filter(StudentExam.id == exam.id).first()
        assert refreshed.status == "error"
        assert refreshed.error_message and "exploded" in refreshed.error_message.lower()

        # No partial answers should be persisted
        from app.models.exam_answer import ExamAnswer

        partial = db.query(ExamAnswer).filter(ExamAnswer.student_exam_id == exam.id).count()
        assert partial == 0


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


class TestFriendlyErrorMapping:
    """Translate raw exceptions / RetryError into user-facing Spanish messages."""

    def _retry_error_wrapping(self, inner_exc: BaseException):
        """Build a tenacity.RetryError that wraps `inner_exc` like real retries do."""
        from concurrent.futures import Future

        from tenacity import RetryError

        fut: Future = Future()
        fut.set_exception(inner_exc)
        return RetryError(fut)

    def _make_rate_limit_error(self):
        """Construct a real openai.RateLimitError matching the v1 SDK signature."""
        import httpx
        from openai import RateLimitError

        # The SDK constructor needs message + a response object.
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(429, request=request)
        return RateLimitError("Rate limit reached", response=response, body=None)

    def _make_auth_error(self):
        import httpx
        from openai import AuthenticationError

        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(401, request=request)
        return AuthenticationError("Invalid API key", response=response, body=None)

    def test_retry_error_with_rate_limit_maps_to_friendly_spanish(self) -> None:
        from app.services.grading import _friendly_error

        wrapped = self._retry_error_wrapping(self._make_rate_limit_error())
        msg = _friendly_error(wrapped)
        assert "saturado" in msg.lower()
        assert "rate limit" in msg.lower()
        # Crucially, the ugly tenacity repr must not leak.
        assert "RetryError" not in msg
        assert "Future at" not in msg

    def test_authentication_error_maps_to_friendly_spanish(self) -> None:
        from app.services.grading import _friendly_error

        msg = _friendly_error(self._make_auth_error())
        assert "openai" in msg.lower()
        assert "invalida" in msg.lower() or "configura" in msg.lower()

    def test_retry_error_with_auth_unwraps_correctly(self) -> None:
        from app.services.grading import _friendly_error

        wrapped = self._retry_error_wrapping(self._make_auth_error())
        msg = _friendly_error(wrapped)
        assert "openai" in msg.lower()
        assert "RetryError" not in msg

    def test_unknown_exception_falls_back_to_truncated_str(self) -> None:
        from app.services.grading import _friendly_error

        msg = _friendly_error(RuntimeError("something exploded"))
        assert "something exploded" in msg
        # Must be capped at 500 chars
        assert len(msg) <= 500

    def test_retry_error_with_no_inner_exception_does_not_crash(self) -> None:
        """Defensive: if RetryError.last_attempt is somehow malformed."""
        from tenacity import RetryError

        from app.services.grading import _friendly_error

        # Not realistic but guards the try/except in _friendly_error
        bare = RetryError.__new__(RetryError)
        bare.last_attempt = None  # type: ignore[assignment]
        msg = _friendly_error(bare)
        # Falls back to str(exc)[:500]; no crash.
        assert isinstance(msg, str)
        assert len(msg) <= 500


class TestVisionDownscale:
    """BaseAgent should shrink large images before sending to OpenAI."""

    def _make_jpeg(self, width: int, height: int) -> bytes:
        from PIL import Image

        img = Image.new("RGB", (width, height), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _capture_sent_image(self, client: MagicMock) -> tuple[int, int]:
        """Pull the image embedded in the OpenAI call and return its dimensions."""
        import base64

        from PIL import Image

        args, kwargs = client.chat.completions.create.call_args
        user_content = kwargs["messages"][1]["content"]
        image_part = next(p for p in user_content if p.get("type") == "image_url")
        b64 = image_part["image_url"]["url"].split(",", 1)[1]
        decoded = base64.b64decode(b64)
        sent = Image.open(io.BytesIO(decoded))
        return sent.width, sent.height

    def _stub_client(self, content: str = "ok") -> MagicMock:
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = content
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response
        return client

    def test_large_image_is_downscaled_to_long_edge_1800(self) -> None:
        from app.agents.base import BaseAgent

        class _Agent(BaseAgent):
            def execute(self, **kwargs):
                return None

        client = self._stub_client()
        agent = _Agent(openai_client=client)
        agent._chat_completion_with_images(
            system_prompt="x", user_text="x", images=[self._make_jpeg(4000, 3000)]
        )
        w, h = self._capture_sent_image(client)
        assert max(w, h) <= 1800
        # Aspect ratio preserved (within rounding)
        assert abs((w / h) - (4000 / 3000)) < 0.02

    def test_small_image_is_passed_through_unchanged(self) -> None:
        from app.agents.base import BaseAgent

        class _Agent(BaseAgent):
            def execute(self, **kwargs):
                return None

        client = self._stub_client()
        agent = _Agent(openai_client=client)
        agent._chat_completion_with_images(
            system_prompt="x", user_text="x", images=[self._make_jpeg(800, 600)]
        )
        w, h = self._capture_sent_image(client)
        assert (w, h) == (800, 600)

    def test_corrupt_bytes_pass_through_to_to_png(self) -> None:
        """Defensive: PIL fails on garbage bytes — _downscale_for_vision falls back."""
        from app.agents.base import BaseAgent

        class _Agent(BaseAgent):
            def execute(self, **kwargs):
                return None

        client = self._stub_client()
        agent = _Agent(openai_client=client)
        # Should not raise
        agent._chat_completion_with_images(
            system_prompt="x", user_text="x", images=[b"not-an-image-at-all"]
        )
        # And the request still went out
        assert client.chat.completions.create.called
