from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.clase import Class, ClassProject
from app.models.project import Project, ProjectStatus
from app.models.user import User


class TestAddClassProject:
    def test_link_project(
        self,
        client: TestClient,
        test_class: Class,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/projects",
            headers=auth_headers,
            json={"project_id": test_project.id},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project_id"] == test_project.id
        assert data["project_name"] == test_project.name
        assert data["display_order"] == 0

    def test_duplicate_rejected(
        self,
        client: TestClient,
        db: Session,
        test_class: Class,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        # Link once
        cp = ClassProject(
            id=str(uuid4()),
            class_id=test_class.id,
            project_id=test_project.id,
            display_order=0,
        )
        db.add(cp)
        db.commit()

        # Attempt duplicate
        response = client.post(
            f"/api/v1/classes/{test_class.id}/projects",
            headers=auth_headers,
            json={"project_id": test_project.id},
        )
        assert response.status_code == 409

    def test_other_users_project_forbidden(
        self,
        client: TestClient,
        db: Session,
        test_class: Class,
        test_user_2: User,
        auth_headers: dict,
    ) -> None:
        # Create a project owned by test_user_2
        other_project = Project(
            id=str(uuid4()),
            owner_id=test_user_2.id,
            name="Other Project",
            status=ProjectStatus.DRAFT.value,
        )
        db.add(other_project)
        db.commit()

        response = client.post(
            f"/api/v1/classes/{test_class.id}/projects",
            headers=auth_headers,
            json={"project_id": other_project.id},
        )
        assert response.status_code == 403

    def test_nonexistent_project_404(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/projects",
            headers=auth_headers,
            json={"project_id": "nonexistent-id"},
        )
        assert response.status_code == 404

    def test_non_owner_forbidden(
        self,
        client: TestClient,
        test_class: Class,
        test_project: Project,
        auth_headers_2: dict,
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/projects",
            headers=auth_headers_2,
            json={"project_id": test_project.id},
        )
        assert response.status_code == 403


class TestListClassProjects:
    def test_list_projects(
        self,
        client: TestClient,
        db: Session,
        test_class: Class,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        cp = ClassProject(
            id=str(uuid4()),
            class_id=test_class.id,
            project_id=test_project.id,
            display_order=0,
        )
        db.add(cp)
        db.commit()

        response = client.get(
            f"/api/v1/classes/{test_class.id}/projects", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["project_id"] == test_project.id

    def test_list_empty(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/projects", headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json() == []


class TestRemoveClassProject:
    def test_unlink_project(
        self,
        client: TestClient,
        db: Session,
        test_class: Class,
        test_project: Project,
        auth_headers: dict,
    ) -> None:
        cp = ClassProject(
            id=str(uuid4()),
            class_id=test_class.id,
            project_id=test_project.id,
            display_order=0,
        )
        db.add(cp)
        db.commit()

        response = client.delete(
            f"/api/v1/classes/{test_class.id}/projects/{cp.id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify removed
        response = client.get(
            f"/api/v1/classes/{test_class.id}/projects", headers=auth_headers
        )
        assert response.json() == []

    def test_nonexistent_404(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.delete(
            f"/api/v1/classes/{test_class.id}/projects/nonexistent-id",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestReorderClassProjects:
    def test_valid_reorder(
        self,
        client: TestClient,
        db: Session,
        test_class: Class,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        # Create two projects and link them
        proj1 = Project(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="Project A",
            status=ProjectStatus.DRAFT.value,
        )
        proj2 = Project(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="Project B",
            status=ProjectStatus.DRAFT.value,
        )
        db.add_all([proj1, proj2])
        db.flush()

        cp1 = ClassProject(
            id=str(uuid4()),
            class_id=test_class.id,
            project_id=proj1.id,
            display_order=0,
        )
        cp2 = ClassProject(
            id=str(uuid4()),
            class_id=test_class.id,
            project_id=proj2.id,
            display_order=1,
        )
        db.add_all([cp1, cp2])
        db.commit()

        # Reorder: swap
        response = client.put(
            f"/api/v1/classes/{test_class.id}/projects/reorder",
            headers=auth_headers,
            json={"order": [cp2.id, cp1.id]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data[0]["id"] == cp2.id
        assert data[0]["display_order"] == 0
        assert data[1]["id"] == cp1.id
        assert data[1]["display_order"] == 1

    def test_invalid_ids(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.put(
            f"/api/v1/classes/{test_class.id}/projects/reorder",
            headers=auth_headers,
            json={"order": ["fake-id-1", "fake-id-2"]},
        )
        assert response.status_code == 400
