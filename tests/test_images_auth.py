"""Authorization regression tests for the image endpoints.

These endpoints serve answer-key and student-exam page images for the
project detail UI. Authentication is via query-param token (so plain
``<img>`` tags work), so the standard ``get_user_project`` dependency
can't be used. The local helper must still delegate the permission
check to ``can_user_access_project`` — otherwise institution admins
(and class professors) cannot load images for projects linked to their
classes, even though they can read every other endpoint of that project.
"""

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.clase import Class, ClassProject
from app.models.institution import Institution, InstitutionMember
from app.models.project import Project
from app.models.user import User
from app.services.auth import create_access_token, hash_password


def _make_institution_owner(db: Session, slug: str) -> tuple[User, Institution]:
    user = User(
        id=str(uuid4()),
        email=f"{slug}@example.com",
        hashed_password=hash_password("x" * 16),
        full_name=f"Inst {slug}",
        role="institution",
        is_active=True,
    )
    db.add(user)
    inst = Institution(id=str(uuid4()), name=f"Inst {slug}", slug=slug)
    db.add(inst)
    db.flush()
    db.add(
        InstitutionMember(
            id=str(uuid4()),
            user_id=user.id,
            institution_id=inst.id,
            role="owner",
        )
    )
    db.commit()
    return user, inst


class TestImagesAuthorization:
    def test_institution_admin_passes_image_auth(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_project: Project,
    ) -> None:
        """An institution owner with a class linked to the project must reach
        the image handler. There's no real answer-key file so the endpoint
        returns 404 — that's fine; what matters is it's NOT 403.
        """
        inst_user, inst = _make_institution_owner(db, slug="img-auth-pass")
        clase = Class(
            id=str(uuid4()),
            professor_id=test_user.id,
            institution_id=inst.id,
            name="Linked",
            subject="X",
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
        response = client.get(
            f"/api/v1/projects/{test_project.id}/answer-key/image",
            params={"page": 0, "token": token},
        )
        # Pass = NOT 403. 404 (no answer key uploaded) is the expected result.
        assert response.status_code != 403, response.text
        assert response.status_code == 404

    def test_unrelated_user_still_gets_403(
        self,
        client: TestClient,
        db: Session,
        test_project: Project,
    ) -> None:
        """A user who is not owner, not a class professor, and not an
        institution admin of any linked class must still be rejected.
        """
        unrelated, _ = _make_institution_owner(db, slug="img-auth-no")
        token = create_access_token(data={"sub": unrelated.id})
        response = client.get(
            f"/api/v1/projects/{test_project.id}/answer-key/image",
            params={"page": 0, "token": token},
        )
        assert response.status_code == 403
