import io

from fastapi.testclient import TestClient

from app.models.project import Project
from app.services.storage import LocalStorageService


def _upload_exam(
    client: TestClient,
    project_id: str,
    auth_headers: dict,
    name: str = "student1.pdf",
    content: bytes = b"%PDF-1.4 fake student exam",
    content_type: str = "application/pdf",
) -> dict:
    """Upload a single exam and return the first item from the response list."""
    response = client.post(
        f"/api/v1/projects/{project_id}/exams/upload",
        headers=auth_headers,
        files={"files": (name, io.BytesIO(content), content_type)},
    )
    return {
        "response": response,
        "data": response.json()[0] if response.status_code == 201 else response.json(),
    }


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


class TestDuplicateExamPrevention:
    def test_uploading_same_identifier_twice_returns_409(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        """Uploading two exams for the same student in a project must be rejected."""
        url = f"/api/v1/projects/{test_project.id}/exams/upload"

        first = client.post(
            url,
            headers=auth_headers,
            files={"files": ("s1.pdf", io.BytesIO(b"%PDF-1.4 first"), "application/pdf")},
            data={"student_name": "Ana Maria", "student_identifier": "STU-001"},
        )
        assert first.status_code == 201

        second = client.post(
            url,
            headers=auth_headers,
            files={"files": ("s2.pdf", io.BytesIO(b"%PDF-1.4 second"), "application/pdf")},
            data={"student_name": "Ana Maria", "student_identifier": "STU-001"},
        )
        assert second.status_code == 409
        assert "STU-001" in second.json()["detail"]

    def test_anonymous_uploads_can_repeat(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        """Without student_identifier, multiple uploads stay allowed."""
        url = f"/api/v1/projects/{test_project.id}/exams/upload"
        for i in range(3):
            r = client.post(
                url,
                headers=auth_headers,
                files={"files": (f"s{i}.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
            )
            assert r.status_code == 201

    def test_different_projects_can_share_identifier(
        self,
        client: TestClient,
        db,
        test_user,
        auth_headers: dict,
        temp_storage: LocalStorageService,
    ) -> None:
        """The constraint is per-project, not global."""
        from uuid import uuid4

        from app.models.project import Project as ProjectModel
        from app.models.project import ProjectStatus

        proj_a = ProjectModel(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="Proj A",
            status=ProjectStatus.DRAFT.value,
            config={},
        )
        proj_b = ProjectModel(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="Proj B",
            status=ProjectStatus.DRAFT.value,
            config={},
        )
        db.add_all([proj_a, proj_b])
        db.commit()

        for proj in (proj_a, proj_b):
            r = client.post(
                f"/api/v1/projects/{proj.id}/exams/upload",
                headers=auth_headers,
                files={"files": ("s.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
                data={"student_name": "Same Person", "student_identifier": "X-1"},
            )
            assert r.status_code == 201, f"Project {proj.name}: {r.text}"
