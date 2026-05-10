"""Regression tests for QA report V3: class professor must access project endpoints.

Bug: a professor who owns a class with a project linked to it (via
ClassProject) was 403'd from /projects/{id}/* endpoints because
get_user_project only matched on Project.owner_id. Result: she could see
the gradebook (class-scoped) but not the project's grading summary,
analytics or exams list — so the project average appeared to "vanish"
when navigating between views.

Fix (app/api/deps.py): can_user_access_project also returns True when
the user is the professor of any class linked to the project via
ClassProject. get_user_project + analytics' inline check use it.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.clase import Class, ClassProject
from app.models.project import Project, ProjectStatus
from app.models.user import User


@pytest.fixture()
def project_owned_by_other_user(db: Session) -> Project:
    """A project whose owner is NOT the test_user (the class professor)."""
    other_owner = User(
        id=str(uuid4()),
        email=f"other-{uuid4()}@example.com",
        full_name="Other Owner",
        hashed_password="$2b$12$hash",
        role="professor",
        is_active=True,
    )
    db.add(other_owner)
    db.flush()
    project = Project(
        id=str(uuid4()),
        owner_id=other_owner.id,
        name="Project owned by someone else",
        status=ProjectStatus.CONFIRMED.value,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture()
def class_with_external_project(
    db: Session,
    test_class: Class,
    project_owned_by_other_user: Project,
) -> tuple[Class, Project]:
    """Link the externally-owned project to the test_user's class."""
    cp = ClassProject(
        id=str(uuid4()),
        class_id=test_class.id,
        project_id=project_owned_by_other_user.id,
        display_order=0,
    )
    db.add(cp)
    db.commit()
    return test_class, project_owned_by_other_user


class TestClassProfessorCanAccessLinkedProject:
    """The class professor must read the project even when she's not its owner."""

    def test_get_project_detail(
        self,
        client: TestClient,
        class_with_external_project: tuple[Class, Project],
        auth_headers: dict,
    ) -> None:
        _, project = class_with_external_project
        response = client.get(f"/api/v1/projects/{project.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["id"] == project.id

    def test_get_grading_summary(
        self,
        client: TestClient,
        class_with_external_project: tuple[Class, Project],
        auth_headers: dict,
    ) -> None:
        _, project = class_with_external_project
        response = client.get(
            f"/api/v1/projects/{project.id}/grading/summary",
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_get_project_exams(
        self,
        client: TestClient,
        class_with_external_project: tuple[Class, Project],
        auth_headers: dict,
    ) -> None:
        _, project = class_with_external_project
        response = client.get(
            f"/api/v1/projects/{project.id}/exams",
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_get_project_analytics(
        self,
        client: TestClient,
        class_with_external_project: tuple[Class, Project],
        auth_headers: dict,
    ) -> None:
        _, project = class_with_external_project
        response = client.get(
            f"/api/v1/analytics/projects/{project.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200


class TestUnrelatedUserStillBlocked:
    """A user who is NOT class professor and NOT owner remains 403."""

    def test_unrelated_professor_cannot_read_external_project(
        self,
        client: TestClient,
        project_owned_by_other_user: Project,
        auth_headers: dict,
    ) -> None:
        # auth_headers belongs to test_user. There's no ClassProject linking
        # this project to any class of test_user — so 403 is expected.
        response = client.get(
            f"/api/v1/projects/{project_owned_by_other_user.id}",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_unrelated_professor_cannot_read_grading_summary(
        self,
        client: TestClient,
        project_owned_by_other_user: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/projects/{project_owned_by_other_user.id}/grading/summary",
            headers=auth_headers,
        )
        assert response.status_code == 403

    def test_unrelated_professor_cannot_read_analytics(
        self,
        client: TestClient,
        project_owned_by_other_user: Project,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/analytics/projects/{project_owned_by_other_user.id}",
            headers=auth_headers,
        )
        assert response.status_code == 403
