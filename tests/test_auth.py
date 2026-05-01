from fastapi.testclient import TestClient

from app.models.user import User


class TestRegister:
    def test_register_success(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "new@example.com",
                "password": "Secure123pass",
                "full_name": "New User",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "new@example.com"
        assert data["full_name"] == "New User"
        assert data["is_active"] is True
        assert "id" in data
        assert "hashed_password" not in data

    def test_register_duplicate_email(self, client: TestClient, test_user: User) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "password": "anotherpassword",
                "full_name": "Duplicate User",
            },
        )
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"]

    def test_register_invalid_email(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "password": "Secure123pass",
                "full_name": "Bad Email User",
            },
        )
        assert response.status_code == 422

    def test_register_short_password(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "short@example.com",
                "password": "short",
                "full_name": "Short Password",
            },
        )
        assert response.status_code == 422


class TestLogin:
    def test_login_success(self, client: TestClient, test_user: User) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user.email, "password": "testpassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client: TestClient, test_user: User) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": test_user.email, "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert "Incorrect email or password" in response.json()["detail"]

    def test_login_nonexistent_user(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "password123"},
        )
        assert response.status_code == 401


class TestGetMe:
    def test_get_me_authenticated(self, client: TestClient, test_user: User, auth_headers: dict) -> None:
        response = client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_user.id
        assert data["email"] == test_user.email
        assert data["full_name"] == test_user.full_name

    def test_get_me_unauthenticated(self, client: TestClient) -> None:
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401


class TestNormalization:
    """Email + name should be trimmed/lowercased before storage."""

    def test_register_normalizes_whitespace_email(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/auth/register",
            json={
                "email": "  HasWhitespace@Example.COM  ",
                "password": "Secure123pass",
                "full_name": "  Whitespace  User  ",
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert data["email"] == "haswhitespace@example.com"
        assert data["full_name"] == "Whitespace User"

    def test_login_with_uppercase_email_works_after_lowercase_register(self, client: TestClient) -> None:
        # Register with normal casing
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "case@example.com",
                "password": "Secure123pass",
                "full_name": "Case Test",
            },
        )
        # Login with uppercase + spaces — should still work due to normalization
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "  CASE@EXAMPLE.COM  ", "password": "Secure123pass"},
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_register_rejects_blank_full_name(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/auth/register",
            json={
                "email": "blank@example.com",
                "password": "Secure123pass",
                "full_name": "   ",
            },
        )
        assert r.status_code == 422


class TestListUsersPagination:
    def test_list_users_paginated(self, client: TestClient, db, auth_headers_admin: dict) -> None:
        from uuid import uuid4

        from app.models.user import User as UserModel
        from app.services.auth import hash_password

        # Seed 30 users in addition to admin (already exists)
        for i in range(30):
            db.add(
                UserModel(
                    id=str(uuid4()),
                    email=f"bulk{i}@x.com",
                    hashed_password=hash_password("xxxxxxxx"),
                    full_name=f"Bulk {i}",
                    role="professor",
                    is_active=True,
                )
            )
        db.commit()

        r = client.get("/api/v1/auth/users", headers=auth_headers_admin)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 31
        assert len(body["items"]) <= 50

        r = client.get("/api/v1/auth/users?page=1&per_page=10", headers=auth_headers_admin)
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 10
        assert body["page"] == 1
        assert body["per_page"] == 10

        r = client.get("/api/v1/auth/users?per_page=999", headers=auth_headers_admin)
        assert r.status_code == 422

    def test_list_users_filters(self, client: TestClient, db, auth_headers_admin: dict) -> None:
        from uuid import uuid4

        from app.models.user import User as UserModel
        from app.services.auth import hash_password

        for i in range(3):
            db.add(
                UserModel(
                    id=str(uuid4()),
                    email=f"prof{i}@x.com",
                    hashed_password=hash_password("xxxxxxxx"),
                    full_name=f"Prof {i}",
                    role="professor",
                    is_active=True,
                )
            )
        db.add(
            UserModel(
                id=str(uuid4()),
                email="inactivo@x.com",
                hashed_password=hash_password("xxxxxxxx"),
                full_name="Inactivo",
                role="student",
                is_active=False,
            )
        )
        db.commit()

        r = client.get("/api/v1/auth/users?role=professor", headers=auth_headers_admin)
        assert r.status_code == 200
        items = r.json()["items"]
        assert all(u["role"] == "professor" for u in items)
        assert len(items) >= 3

        r = client.get("/api/v1/auth/users?is_active=false", headers=auth_headers_admin)
        items = r.json()["items"]
        assert all(u["is_active"] is False for u in items)
        assert any(u["email"] == "inactivo@x.com" for u in items)

        r = client.get("/api/v1/auth/users?search=Prof", headers=auth_headers_admin)
        items = r.json()["items"]
        assert all("prof" in u["full_name"].lower() or "prof" in u["email"].lower() for u in items)


class TestAdminCreateUser:
    def _url(self) -> str:
        return "/api/v1/auth/users"

    def test_admin_creates_user(self, client: TestClient, auth_headers_admin: dict) -> None:
        r = client.post(
            self._url(),
            headers=auth_headers_admin,
            json={
                "email": "newprof@x.com",
                "password": "Secure123pass",
                "full_name": "New Prof",
                "role": "professor",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["email"] == "newprof@x.com"
        assert body["role"] == "professor"
        assert body["is_active"] is True

    def test_only_developer_can_create_developer(
        self,
        client: TestClient,
        auth_headers_admin: dict,
        auth_headers_developer: dict,
    ) -> None:
        payload = {
            "email": "newdev@x.com",
            "password": "Secure123pass",
            "full_name": "New Dev",
            "role": "developer",
        }
        r = client.post(self._url(), headers=auth_headers_admin, json=payload)
        assert r.status_code == 403

        r = client.post(self._url(), headers=auth_headers_developer, json=payload)
        assert r.status_code == 201

    def test_non_admin_cannot_create(self, client: TestClient, auth_headers: dict) -> None:
        r = client.post(
            self._url(),
            headers=auth_headers,
            json={
                "email": "x@x.com",
                "password": "Secure123pass",
                "full_name": "X",
                "role": "professor",
            },
        )
        assert r.status_code == 403

    def test_duplicate_email_409(self, client: TestClient, test_user: User, auth_headers_admin: dict) -> None:
        r = client.post(
            self._url(),
            headers=auth_headers_admin,
            json={
                "email": test_user.email,
                "password": "Secure123pass",
                "full_name": "Dup",
                "role": "professor",
            },
        )
        assert r.status_code == 409


class TestAdminUpdateUser:
    def test_admin_updates_user(
        self,
        client: TestClient,
        test_user: User,
        auth_headers_admin: dict,
    ) -> None:
        r = client.patch(
            f"/api/v1/auth/users/{test_user.id}",
            headers=auth_headers_admin,
            json={"full_name": "Renamed", "role": "admin"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["full_name"] == "Renamed"
        assert r.json()["role"] == "admin"

    def test_admin_can_deactivate_user(
        self,
        client: TestClient,
        test_user: User,
        auth_headers_admin: dict,
    ) -> None:
        r = client.patch(
            f"/api/v1/auth/users/{test_user.id}",
            headers=auth_headers_admin,
            json={"is_active": False},
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    def test_cannot_deactivate_self(
        self,
        client: TestClient,
        test_admin_user: User,
        auth_headers_admin: dict,
    ) -> None:
        r = client.patch(
            f"/api/v1/auth/users/{test_admin_user.id}",
            headers=auth_headers_admin,
            json={"is_active": False},
        )
        assert r.status_code == 400

    def test_only_developer_assigns_developer_role(
        self,
        client: TestClient,
        test_user: User,
        auth_headers_admin: dict,
        auth_headers_developer: dict,
    ) -> None:
        r = client.patch(
            f"/api/v1/auth/users/{test_user.id}",
            headers=auth_headers_admin,
            json={"role": "developer"},
        )
        assert r.status_code == 403

        r = client.patch(
            f"/api/v1/auth/users/{test_user.id}",
            headers=auth_headers_developer,
            json={"role": "developer"},
        )
        assert r.status_code == 200

    def test_cannot_demote_last_developer(
        self,
        client: TestClient,
        test_developer_user: User,
        auth_headers_developer: dict,
    ) -> None:
        r = client.patch(
            f"/api/v1/auth/users/{test_developer_user.id}",
            headers=auth_headers_developer,
            json={"role": "admin"},
        )
        assert r.status_code == 400


class TestAdminDeleteUser:
    def test_admin_deletes_user(
        self,
        client: TestClient,
        test_user: User,
        auth_headers_admin: dict,
    ) -> None:
        r = client.delete(
            f"/api/v1/auth/users/{test_user.id}",
            headers=auth_headers_admin,
        )
        assert r.status_code == 204

        r = client.get("/api/v1/auth/users", headers=auth_headers_admin)
        assert all(u["id"] != test_user.id for u in r.json()["items"])

    def test_cannot_delete_self(
        self,
        client: TestClient,
        test_admin_user: User,
        auth_headers_admin: dict,
    ) -> None:
        r = client.delete(
            f"/api/v1/auth/users/{test_admin_user.id}",
            headers=auth_headers_admin,
        )
        assert r.status_code == 400

    def test_cannot_delete_last_developer(
        self,
        client: TestClient,
        test_developer_user: User,
        auth_headers_developer: dict,
    ) -> None:
        # The only developer is test_developer_user (auth_headers_developer)
        r = client.delete(
            f"/api/v1/auth/users/{test_developer_user.id}",
            headers=auth_headers_developer,
        )
        assert r.status_code == 400


class TestAdminResetPassword:
    def test_admin_resets_password_and_user_can_login(
        self,
        client: TestClient,
        test_user: User,
        auth_headers_admin: dict,
    ) -> None:
        r = client.post(
            f"/api/v1/auth/users/{test_user.id}/reset-password",
            headers=auth_headers_admin,
            json={"new_password": "BrandNewPass99"},
        )
        assert r.status_code == 204

        r = client.post(
            "/api/v1/auth/login",
            json={"email": test_user.email, "password": "BrandNewPass99"},
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_non_admin_cannot_reset(
        self,
        client: TestClient,
        test_user_2: User,
        auth_headers: dict,
    ) -> None:
        r = client.post(
            f"/api/v1/auth/users/{test_user_2.id}/reset-password",
            headers=auth_headers,
            json={"new_password": "BrandNewPass99"},
        )
        assert r.status_code == 403
