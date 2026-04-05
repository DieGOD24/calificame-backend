import io

from fastapi.testclient import TestClient

from app.models.project import Project
from app.models.user import User
from app.services.storage import LocalStorageService


class TestUploadStudentExam:
    def test_upload_student_exam(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        pdf_content = b"%PDF-1.4 fake student exam"
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"file": ("student1.pdf", io.BytesIO(pdf_content), "application/pdf")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project_id"] == test_project.id
        assert data["original_filename"] == "student1.pdf"
        assert data["status"] == "uploaded"
        assert data["file_type"] == "pdf"

    def test_upload_student_exam_image(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        img_content = b"\x89PNG\r\n\x1a\nfake image data"
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"file": ("student1.png", io.BytesIO(img_content), "image/png")},
        )
        assert response.status_code == 201
        assert response.json()["file_type"] == "images"

    def test_upload_invalid_type(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"file": ("data.txt", io.BytesIO(b"text"), "text/plain")},
        )
        assert response.status_code == 400


class TestListStudentExams:
    def test_list_student_exams(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        # Upload two exams
        for name in ["student1.pdf", "student2.pdf"]:
            client.post(
                f"/api/v1/projects/{test_project.id}/exams/upload",
                headers=auth_headers,
                files={"file": (name, io.BytesIO(b"%PDF-1.4 content"), "application/pdf")},
            )

        response = client.get(
            f"/api/v1/projects/{test_project.id}/exams/",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["graded_count"] == 0
        assert data["average_score"] is None

    def test_list_student_exams_empty(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/projects/{test_project.id}/exams/",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestGetStudentExam:
    def test_get_student_exam(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        # Upload
        upload_response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"file": ("s1.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        exam_id = upload_response.json()["id"]

        response = client.get(
            f"/api/v1/projects/{test_project.id}/exams/{exam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["student_exam"]["id"] == exam_id
        assert data["answers"] == []

    def test_get_nonexistent_exam(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/projects/{test_project.id}/exams/nonexistent",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestDeleteStudentExam:
    def test_delete_student_exam(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        # Upload
        upload_response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"file": ("s1.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        exam_id = upload_response.json()["id"]

        # Delete
        response = client.delete(
            f"/api/v1/projects/{test_project.id}/exams/{exam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify gone
        response = client.get(
            f"/api/v1/projects/{test_project.id}/exams/{exam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404
