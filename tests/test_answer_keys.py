import io
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.models.project import Project
from app.services.storage import LocalStorageService


class TestUploadAnswerKey:
    def test_upload_answer_key_pdf(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        pdf_content = b"%PDF-1.4 fake pdf content for testing"
        response = client.post(
            f"/api/v1/projects/{test_project.id}/answer-key/upload",
            headers=auth_headers,
            files={"file": ("exam_key.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project_id"] == test_project.id
        assert data["original_filename"] == "exam_key.pdf"
        assert data["file_type"] == "pdf"
        assert data["is_processed"] is False

    def test_upload_answer_key_image(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        # Create a minimal valid PNG (1x1 pixel)
        import struct
        import zlib

        def _make_png() -> bytes:
            sig = b"\x89PNG\r\n\x1a\n"
            # IHDR
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            # IDAT
            raw = zlib.compress(b"\x00\x00\x00\x00")
            idat_crc = zlib.crc32(b"IDAT" + raw) & 0xFFFFFFFF
            idat = struct.pack(">I", len(raw)) + b"IDAT" + raw + struct.pack(">I", idat_crc)
            # IEND
            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            return sig + ihdr + idat + iend

        png_content = _make_png()
        response = client.post(
            f"/api/v1/projects/{test_project.id}/answer-key/upload",
            headers=auth_headers,
            files={"file": ("exam_key.png", io.BytesIO(png_content), "image/png")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["file_type"] == "images"

    def test_upload_invalid_file_type(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            f"/api/v1/projects/{test_project.id}/answer-key/upload",
            headers=auth_headers,
            files={"file": ("data.csv", io.BytesIO(b"a,b,c"), "text/csv")},
        )
        assert response.status_code == 400

    def test_upload_without_project(
        self,
        client: TestClient,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            "/api/v1/projects/nonexistent-id/answer-key/upload",
            headers=auth_headers,
            files={"file": ("key.pdf", io.BytesIO(b"content"), "application/pdf")},
        )
        assert response.status_code == 404


class TestGetAnswerKey:
    def test_get_answer_key(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        # Upload first
        pdf_content = b"%PDF-1.4 fake pdf content"
        client.post(
            f"/api/v1/projects/{test_project.id}/answer-key/upload",
            headers=auth_headers,
            files={"file": ("key.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )

        response = client.get(
            f"/api/v1/projects/{test_project.id}/answer-key/",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == test_project.id

    def test_get_answer_key_not_found(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/projects/{test_project.id}/answer-key/",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestProcessAnswerKey:
    def test_process_answer_key(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        # Upload first
        pdf_content = b"%PDF-1.4 fake pdf content"
        client.post(
            f"/api/v1/projects/{test_project.id}/answer-key/upload",
            headers=auth_headers,
            files={"file": ("key.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )

        # Mock the DocumentProcessor
        mock_questions_data = [
            {"question_number": 1, "question_text": "What is 1+1?", "correct_answer": "2"},
            {"question_number": 2, "question_text": "What is 2+2?", "correct_answer": "4"},
        ]

        with patch("app.api.answer_keys.DocumentProcessor") as mock_processor_cls:
            mock_processor = MagicMock()
            mock_processor_cls.return_value = mock_processor

            # Simulate what process_answer_key does
            from uuid import uuid4

            from app.models.question import Question

            def fake_process(db, answer_key, project):
                answer_key.is_processed = True
                answer_key.processed_data = {
                    "raw_text": "test",
                    "extracted_questions": mock_questions_data,
                }
                answer_key.num_pages = 1

                questions = []
                for qa in mock_questions_data:
                    q = Question(
                        id=str(uuid4()),
                        project_id=project.id,
                        question_number=qa["question_number"],
                        question_text=qa["question_text"],
                        correct_answer=qa["correct_answer"],
                        points=2.0,
                        is_confirmed=False,
                    )
                    db.add(q)
                    questions.append(q)

                project.status = "answer_key_processed"
                db.commit()
                for q in questions:
                    db.refresh(q)
                return questions

            mock_processor.process_answer_key.side_effect = fake_process

            response = client.post(
                f"/api/v1/projects/{test_project.id}/answer-key/process",
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["is_processed"] is True
            assert len(data["questions"]) == 2
            assert data["questions"][0]["question_text"] == "What is 1+1?"
