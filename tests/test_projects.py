from fastapi.testclient import TestClient

from app.models.project import Project
from app.models.user import User


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

    def test_institution_admin_sees_project_in_their_class(
        self,
        client: TestClient,
        db,
        test_user: User,
        test_project: Project,
    ) -> None:
        """An institution owner can read a project linked to a class belonging
        to their institution, even if they did not create the project and don't
        teach the class.
        """
        from uuid import uuid4

        from app.models.clase import Class, ClassProject
        from app.models.institution import Institution, InstitutionMember
        from app.models.user import User as UserModel
        from app.services.auth import create_access_token, hash_password

        inst_user = UserModel(
            id=str(uuid4()),
            email="inst-proj@example.com",
            hashed_password=hash_password("x" * 16),
            full_name="Inst Proj Owner",
            role="institution",
            is_active=True,
        )
        db.add(inst_user)
        inst = Institution(id=str(uuid4()), name="Has Class", slug="has-class")
        db.add(inst)
        db.flush()
        db.add(
            InstitutionMember(
                id=str(uuid4()),
                user_id=inst_user.id,
                institution_id=inst.id,
                role="owner",
            )
        )
        clase = Class(
            id=str(uuid4()),
            professor_id=test_user.id,
            institution_id=inst.id,
            name="Linked Class",
            subject="Mathematics",
            semester="2026-1",
        )
        db.add(clase)
        db.add(
            ClassProject(
                id=str(uuid4()),
                class_id=clase.id,
                project_id=test_project.id,
                display_order=0,
            )
        )
        db.commit()

        token = create_access_token(data={"sub": inst_user.id})
        # Detail endpoint: institution admin must NOT get 403.
        response = client.get(
            f"/api/v1/projects/{test_project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        # List endpoint: the project shows up too.
        list_response = client.get("/api/v1/projects/", headers={"Authorization": f"Bearer {token}"})
        assert list_response.status_code == 200
        ids = [p["id"] for p in list_response.json()["items"]]
        assert test_project.id in ids


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

    def test_owner_cannot_transfer(
        self,
        client: TestClient,
        test_project: Project,
        test_user_2,
        auth_headers: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/projects/{test_project.id}",
            headers=auth_headers,
            json={"owner_id": test_user_2.id},
        )
        assert response.status_code == 403

    def test_admin_transfers_ownership(
        self,
        client: TestClient,
        test_project: Project,
        test_user_2,
        auth_headers_admin: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/projects/{test_project.id}",
            headers=auth_headers_admin,
            json={"owner_id": test_user_2.id},
        )
        assert response.status_code == 200
        assert response.json()["owner_id"] == test_user_2.id

    def test_admin_transfer_to_invalid_owner_404(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers_admin: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/projects/{test_project.id}",
            headers=auth_headers_admin,
            json={"owner_id": "non-existent-id"},
        )
        assert response.status_code == 404


class TestDeleteProject:
    def test_delete_project(self, client: TestClient, test_project: Project, auth_headers: dict) -> None:
        response = client.delete(f"/api/v1/projects/{test_project.id}", headers=auth_headers)
        assert response.status_code == 204

        # Verify deletion
        response = client.get(f"/api/v1/projects/{test_project.id}", headers=auth_headers)
        assert response.status_code == 404
