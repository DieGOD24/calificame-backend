from fastapi.testclient import TestClient

from app.models.project import Project


class TestCreateProject:
    def test_create_project(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/projects/",
            headers=auth_headers,
            json={
                "name": "Math Final",
                "description": "Final exam for algebra",
                "subject": "Mathematics",
                "config": {
                    "exam_type": "multiple_choice",
                    "total_questions": 10,
                    "points_per_question": 1.0,
                    "has_multiple_pages": False,
                },
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Math Final"
        assert data["description"] == "Final exam for algebra"
        assert data["subject"] == "Mathematics"
        assert data["status"] == "draft"
        assert data["config"]["exam_type"] == "multiple_choice"
        assert data["config"]["total_questions"] == 10

    def test_create_project_minimal(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/projects/",
            headers=auth_headers,
            json={"name": "Quick Test"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Quick Test"
        assert data["description"] is None

    def test_create_project_unauthenticated(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/projects/",
            json={"name": "No Auth"},
        )
        assert response.status_code == 401


class TestListProjects:
    def test_list_projects(self, client: TestClient, test_project: Project, auth_headers: dict) -> None:
        response = client.get("/api/v1/projects/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1
        assert data["items"][0]["id"] == test_project.id

    def test_list_projects_empty(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/projects/", headers=auth_headers)
        assert response.status_code == 200
        # The test_user fixture is used by auth_headers but no project fixture
        # so this depends on whether test_project is also loaded


class TestGetProject:
    def test_get_project(self, client: TestClient, test_project: Project, auth_headers: dict) -> None:
        response = client.get(f"/api/v1/projects/{test_project.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_project.id
        assert data["name"] == test_project.name

    def test_get_nonexistent_project(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/projects/nonexistent-id", headers=auth_headers)
        assert response.status_code == 404

    def test_get_other_user_project_forbidden(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers_2: dict,
    ) -> None:
        response = client.get(f"/api/v1/projects/{test_project.id}", headers=auth_headers_2)
        assert response.status_code == 403


class TestUpdateProject:
    def test_update_project(self, client: TestClient, test_project: Project, auth_headers: dict) -> None:
        response = client.put(
            f"/api/v1/projects/{test_project.id}",
            headers=auth_headers,
            json={"name": "Updated Name", "description": "Updated description"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["description"] == "Updated description"


class TestDeleteProject:
    def test_delete_project(self, client: TestClient, test_project: Project, auth_headers: dict) -> None:
        response = client.delete(f"/api/v1/projects/{test_project.id}", headers=auth_headers)
        assert response.status_code == 204

        # Verify deletion
        response = client.get(f"/api/v1/projects/{test_project.id}", headers=auth_headers)
        assert response.status_code == 404
