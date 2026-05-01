"""Comprehensive security and validation tests for Calificame.com backend."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.clase import Class
from app.models.project import Project
from app.models.user import User
from app.services.auth import create_access_token
from app.services.validators import validate_file_upload

# ---------------------------------------------------------------------------
# TestPasswordPolicy
# ---------------------------------------------------------------------------


class TestPasswordPolicy:
    """Tests for password strength validation via the register and change-password endpoints."""

    def test_register_weak_password_no_uppercase(self, client: TestClient) -> None:
        """A password without uppercase letters must be rejected with 400."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "test_noup@example.com",
                "password": "password123",
                "full_name": "Test User",
            },
        )
        assert response.status_code == 400

    def test_register_weak_password_no_digit(self, client: TestClient) -> None:
        """A password without digits must be rejected with 400."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "test_nodigit@example.com",
                "password": "Passwordonly",
                "full_name": "Test User",
            },
        )
        assert response.status_code == 400

    def test_register_weak_password_too_short(self, client: TestClient) -> None:
        """A password shorter than min_length is caught by Pydantic (422)."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "test_short@example.com",
                "password": "Ab1",
                "full_name": "Test User",
            },
        )
        assert response.status_code == 422

    def test_register_strong_password(self, client: TestClient) -> None:
        """A valid strong password should yield 201."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "test_strong@example.com",
                "password": "MyStrong1Pass",
                "full_name": "Test User",
            },
        )
        assert response.status_code == 201

    def test_change_password_weak_rejected(
        self, client: TestClient, test_user: User, auth_headers: dict[str, str]
    ) -> None:
        """Changing to a weak password should be rejected with 400."""
        response = client.post(
            "/api/v1/auth/me/change-password",
            json={
                "current_password": "testpassword123",
                "new_password": "weakpass",
            },
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_change_password_strong_accepted(
        self, client: TestClient, test_user: User, auth_headers: dict[str, str]
    ) -> None:
        """Changing to a strong password should succeed with 200."""
        response = client.post(
            "/api/v1/auth/me/change-password",
            json={
                "current_password": "testpassword123",
                "new_password": "NewStrong1",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# TestFileUploadValidation
# ---------------------------------------------------------------------------


class TestFileUploadValidation:
    """Tests for the validate_file_upload helper (magic-bytes + extension checks)."""

    def test_validate_pdf_magic_bytes(self) -> None:
        """Valid PDF magic bytes with .pdf extension should pass."""
        content = b"%PDF-1.4 some pdf content"
        valid, msg = validate_file_upload("document.pdf", content)
        assert valid is True
        assert msg == ""

    def test_validate_png_magic_bytes(self) -> None:
        """Valid PNG magic bytes with .png extension should pass."""
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        valid, msg = validate_file_upload("image.png", content)
        assert valid is True
        assert msg == ""

    def test_validate_invalid_extension(self) -> None:
        """An .exe file must be rejected regardless of content."""
        content = b"MZ" + b"\x00" * 100  # PE header
        valid, msg = validate_file_upload("malware.exe", content)
        assert valid is False
        assert "no permitido" in msg

    def test_validate_mismatched_content(self) -> None:
        """PNG magic bytes with a .pdf extension should be rejected."""
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        valid, msg = validate_file_upload("fake.pdf", content)
        assert valid is False
        assert "no coincide" in msg


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Smoke tests for rate-limiting infrastructure."""

    def test_rate_limit_module_loads(self) -> None:
        """The rate limiter module should be importable and non-None."""
        from app.rate_limit import limiter

        assert limiter is not None


# ---------------------------------------------------------------------------
# TestAuthorizationSecurity
# ---------------------------------------------------------------------------


class TestAuthorizationSecurity:
    """Tests ensuring cross-user access is blocked and token validation works."""

    def test_access_other_users_project(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers_2: dict[str, str],
    ) -> None:
        """User 2 must not be able to access User 1's project (403)."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}",
            headers=auth_headers_2,
        )
        assert response.status_code == 403

    def test_access_other_users_class(
        self,
        client: TestClient,
        test_class: Class,
        auth_headers_2: dict[str, str],
    ) -> None:
        """User 2 must not be able to access User 1's class (403)."""
        response = client.get(
            f"/api/v1/classes/{test_class.id}",
            headers=auth_headers_2,
        )
        assert response.status_code == 403

    def test_student_cannot_create_class(
        self,
        client: TestClient,
        auth_headers_student: dict[str, str],
    ) -> None:
        """A student must not be allowed to create a class (403)."""
        response = client.post(
            "/api/v1/classes/",
            json={
                "name": "Hacked Class",
                "subject": "None",
                "semester": "2026-1",
            },
            headers=auth_headers_student,
        )
        assert response.status_code == 403

    def test_student_cannot_delete_institution(
        self,
        client: TestClient,
        db: Session,
        auth_headers_student: dict[str, str],
    ) -> None:
        """A student must not be able to delete an institution (403)."""
        # Create an institution directly in the DB so we have one to target
        from app.models.institution import Institution

        inst = Institution(
            id=str(uuid4()),
            name="Test Inst",
            slug="test-inst",
        )
        db.add(inst)
        db.commit()

        response = client.delete(
            f"/api/v1/institutions/{inst.id}",
            headers=auth_headers_student,
        )
        assert response.status_code == 403

    def test_expired_token_rejected(self, client: TestClient, test_user: User) -> None:
        """A token created with a negative expiry should yield 401."""
        expired_token = create_access_token(
            data={"sub": test_user.id},
            expires_delta=timedelta(minutes=-5),
        )
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401

    def test_malformed_token_rejected(self, client: TestClient) -> None:
        """A completely invalid token string should yield 401."""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-token-garbage"},
        )
        assert response.status_code == 401

    def test_missing_token_rejected(self, client: TestClient) -> None:
        """No Authorization header at all should yield 401."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# TestSQLInjectionPrevention
# ---------------------------------------------------------------------------


class TestSQLInjectionPrevention:
    """Tests that common SQL injection payloads are safely handled."""

    def test_login_sql_injection(self, client: TestClient) -> None:
        """An SQL injection attempt in the email field should be rejected (422)
        because Pydantic EmailStr validation rejects it."""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "' OR 1=1 --",
                "password": "irrelevant",
            },
        )
        assert response.status_code == 422

    def test_project_id_sql_injection(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        test_user: User,
    ) -> None:
        """SQL injection in a path parameter should result in 404 (not found)
        rather than a server error."""
        response = client.get(
            "/api/v1/projects/' OR 1=1 --",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_search_sql_injection_in_query_param(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        test_user: User,
    ) -> None:
        """SQL injection in a query parameter should be handled safely."""
        payloads = [
            "'; DROP TABLE users; --",
            "1 UNION SELECT * FROM users",
            "' OR '1'='1",
        ]
        for payload in payloads:
            response = client.get(
                "/api/v1/classes/",
                params={"semester": payload},
                headers=auth_headers,
            )
            # Should return normally (empty list), not a 500
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# TestInputValidation
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Tests for edge-case input handling on the API."""

    def test_oversized_project_name(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        test_user: User,
    ) -> None:
        """A project name exceeding max_length should be rejected with 422."""
        response = client.post(
            "/api/v1/projects/",
            json={
                "name": "A" * 1000,
                "description": "Too long name",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_xss_in_project_name(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        test_user: User,
    ) -> None:
        """XSS payloads in project name should be stored and returned literally
        (backend does not render HTML)."""
        xss_name = "<script>alert(1)</script>"
        response = client.post(
            "/api/v1/projects/",
            json={
                "name": xss_name,
                "description": "XSS test project",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == xss_name

    def test_unicode_in_names(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        test_user: User,
    ) -> None:
        """Unicode and emoji characters in names should work correctly."""
        unicode_name = "Examen Final \u00e1\u00e9\u00ed\u00f3\u00fa \U0001f4da"
        response = client.post(
            "/api/v1/projects/",
            json={
                "name": unicode_name,
                "description": "Unicode test project",
            },
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == unicode_name


# ---------------------------------------------------------------------------
# TestHealthCheck
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Tests for the /health endpoint."""

    def test_health_check_returns_status(self, client: TestClient) -> None:
        """GET /health should return a JSON body containing a 'status' key."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_health_check_includes_db(self, client: TestClient) -> None:
        """GET /health should include a 'database' key in the response."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "database" in data
