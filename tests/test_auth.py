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

        # Default page=1, per_page=50 → returns up to 31 users (admin + 30)
        r = client.get("/api/v1/auth/users", headers=auth_headers_admin)
        assert r.status_code == 200
        assert len(r.json()) <= 50

        # per_page=10 → returns max 10
        r = client.get("/api/v1/auth/users?page=1&per_page=10", headers=auth_headers_admin)
        assert r.status_code == 200
        assert len(r.json()) == 10

        # Invalid per_page (>200) rejected by query validation
        r = client.get("/api/v1/auth/users?per_page=999", headers=auth_headers_admin)
        assert r.status_code == 422
