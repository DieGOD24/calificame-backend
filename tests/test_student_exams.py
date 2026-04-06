import io

from fastapi.testclient import TestClient

from app.models.project import Project
from app.models.user import User
from app.services.storage import LocalStorageService


def _upload_exam(client: TestClient, project_id: str, auth_headers: dict, name: str = "student1.pdf", content: bytes = b"%PDF-1.4 fake student exam", content_type: str = "application/pdf") -> dict:
    """Upload a single exam and return the first item from the response list."""
    response = client.post(
        f"/api/v1/projects/{project_id}/exams/upload",
        headers=auth_headers,
        files={"files": (name, io.BytesIO(content), content_type)},
    )
    return {"response": response, "data": response.json()[0] if response.status_code == 201 else response.json()}


class TestUploadStudentExam:
    def test_upload_student_exam(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        result = _upload_exam(client, test_project.id, auth_headers)
        assert result["response"].status_code == 201
        data = result["data"]
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
        result = _upload_exam(client, test_project.id, auth_headers, "student1.png", img_content, "image/png")
        assert result["response"].status_code == 201
        assert result["data"]["file_type"] == "images"

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
            files={"files": ("data.txt", io.BytesIO(b"text"), "text/plain")},
        )
        assert response.status_code == 400

    def test_upload_multiple_exams(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files=[
                ("files", ("s1.pdf", io.BytesIO(b"%PDF-1.4 content1"), "application/pdf")),
                ("files", ("s2.pdf", io.BytesIO(b"%PDF-1.4 content2"), "application/pdf")),
            ],
        )
        assert response.status_code == 201
        data = response.json()
        assert len(data) == 2
        assert data[0]["original_filename"] == "s1.pdf"
        assert data[1]["original_filename"] == "s2.pdf"


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
                files={"files": (name, io.BytesIO(b"%PDF-1.4 content"), "application/pdf")},
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
        result = _upload_exam(client, test_project.id, auth_headers, "s1.pdf")
        exam_id = result["data"]["id"]

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
        result = _upload_exam(client, test_project.id, auth_headers, "s1.pdf")
        exam_id = result["data"]["id"]

        response = client.delete(
            f"/api/v1/projects/{test_project.id}/exams/{exam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        response = client.get(
            f"/api/v1/projects/{test_project.id}/exams/{exam_id}",
            headers=auth_headers,
        )
        assert response.status_code == 404
