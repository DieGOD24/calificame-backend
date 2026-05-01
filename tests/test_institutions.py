from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.institution import Institution, InstitutionMember
from app.models.user import User


def _make_institution(db: Session, owner: User, slug: str = "test-inst") -> Institution:
    """Helper to create an institution with an owner member."""
    inst = Institution(
        id=str(uuid4()),
        name="Test Institution",
        slug=slug,
    )
    db.add(inst)
    db.flush()
    member = InstitutionMember(
        id=str(uuid4()),
        user_id=owner.id,
        institution_id=inst.id,
        role="owner",
    )
    db.add(member)
    db.commit()
    db.refresh(inst)
    return inst


class TestCreateInstitution:
    def test_create_valid(self, client: TestClient, test_admin_user: User, auth_headers_admin: dict) -> None:
        response = client.post(
            "/api/v1/institutions/",
            headers=auth_headers_admin,
            json={"name": "New Inst", "slug": "new-inst"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Inst"
        assert data["slug"] == "new-inst"
        assert data["member_count"] == 1

    def test_slug_conflict(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        _make_institution(db, test_admin_user, slug="taken-slug")
        response = client.post(
            "/api/v1/institutions/",
            headers=auth_headers_admin,
            json={"name": "Another Inst", "slug": "taken-slug"},
        )
        assert response.status_code == 409

    def test_student_cannot_create(
        self, client: TestClient, test_student_user: User, auth_headers_student: dict
    ) -> None:
        response = client.post(
            "/api/v1/institutions/",
            headers=auth_headers_student,
            json={"name": "Nope", "slug": "nope"},
        )
        assert response.status_code == 403


class TestListInstitutions:
    def test_admin_sees_all(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        _make_institution(db, test_admin_user, slug="inst-a")
        _make_institution(db, test_admin_user, slug="inst-b")
        response = client.get("/api/v1/institutions/", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2

    def test_admin_empty_list(self, client: TestClient, test_admin_user: User, auth_headers_admin: dict) -> None:
        response = client.get("/api/v1/institutions/", headers=auth_headers_admin)
        assert response.status_code == 200
        assert response.json() == []

    def test_list_with_pagination(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        _make_institution(db, test_admin_user, slug="page-a")
        _make_institution(db, test_admin_user, slug="page-b")
        response = client.get("/api/v1/institutions/", headers=auth_headers_admin, params={"page_size": 1})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1


class TestGetInstitution:
    def test_get_valid(self, client: TestClient, db: Session, test_user: User, auth_headers: dict) -> None:
        inst = _make_institution(db, test_user, slug="get-me")
        response = client.get(f"/api/v1/institutions/{inst.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["slug"] == "get-me"

    def test_not_found(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/institutions/nonexistent-id", headers=auth_headers)
        assert response.status_code == 404


class TestUpdateInstitution:
    def test_owner_updates(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="upd-inst")
        response = client.put(
            f"/api/v1/institutions/{inst.id}",
            headers=auth_headers_admin,
            json={"name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    def test_non_owner_forbidden(
        self,
        client: TestClient,
        db: Session,
        test_admin_user: User,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="no-touch")
        response = client.put(
            f"/api/v1/institutions/{inst.id}",
            headers=auth_headers,
            json={"name": "Nope"},
        )
        assert response.status_code == 403


class TestDeleteInstitution:
    def test_admin_deletes(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="del-inst")
        response = client.delete(f"/api/v1/institutions/{inst.id}", headers=auth_headers_admin)
        assert response.status_code == 204

    def test_professor_forbidden(
        self,
        client: TestClient,
        db: Session,
        test_admin_user: User,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="nodelete")
        response = client.delete(f"/api/v1/institutions/{inst.id}", headers=auth_headers)
        assert response.status_code == 403


class TestMembers:
    def test_list_members(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="mem-inst")
        response = client.get(f"/api/v1/institutions/{inst.id}/members", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["role"] == "owner"

    def test_invite_member(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="inv-inst")
        response = client.post(
            f"/api/v1/institutions/{inst.id}/members/invite",
            headers=auth_headers_admin,
            json={"email": "invitee@example.com", "role": "professor"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "invitee@example.com"
        assert data["status"] == "pending"

    def test_duplicate_invite(
        self, client: TestClient, db: Session, test_admin_user: User, auth_headers_admin: dict
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="dup-inv")
        invite_payload = {"email": "dup@example.com", "role": "professor"}
        client.post(
            f"/api/v1/institutions/{inst.id}/members/invite",
            headers=auth_headers_admin,
            json=invite_payload,
        )
        response = client.post(
            f"/api/v1/institutions/{inst.id}/members/invite",
            headers=auth_headers_admin,
            json=invite_payload,
        )
        assert response.status_code == 409

    def test_remove_member(
        self,
        client: TestClient,
        db: Session,
        test_admin_user: User,
        test_user: User,
        auth_headers_admin: dict,
    ) -> None:
        inst = _make_institution(db, test_admin_user, slug="rm-inst")
        # Add test_user as a professor member
        extra_member = InstitutionMember(
            id=str(uuid4()),
            user_id=test_user.id,
            institution_id=inst.id,
            role="professor",
        )
        db.add(extra_member)
        db.commit()
        db.refresh(extra_member)

        response = client.delete(
            f"/api/v1/institutions/{inst.id}/members/{extra_member.id}",
            headers=auth_headers_admin,
        )
        assert response.status_code == 204
