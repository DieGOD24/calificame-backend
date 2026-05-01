"""Tests for rate limiting on critical endpoints.

The limiter is disabled by default in test env (TESTING=1) — these tests
re-enable it for the duration of the test using monkeypatch + reset.
"""
import io

import pytest
from fastapi.testclient import TestClient


def _enable_limiter(monkeypatch):
    """Re-enable the slowapi limiter for this test only."""
    from app import rate_limit as rl
    monkeypatch.setattr(rl.limiter, "enabled", True)
    # Reset internal storage so each test starts with a fresh counter
    try:
        rl.limiter._storage.reset()  # type: ignore[attr-defined]
    except Exception:
        pass


class TestRateLimitsLogin:
    """Login endpoint already had rate limit; verify it still works as a baseline."""

    def test_login_rate_limit_kicks_in(
        self,
        client: TestClient,
        monkeypatch,
    ) -> None:
        _enable_limiter(monkeypatch)
        # Limit is 5/minute on login
        for i in range(5):
            r = client.post(
                "/api/v1/auth/login",
                json={"email": f"nope{i}@x.com", "password": "Wrong123pass"},
            )
            # 401 unauthorized is expected since user doesn't exist
            assert r.status_code in (401, 429), f"Iteration {i}: {r.status_code}"

        # 6th call should be rate-limited
        r6 = client.post(
            "/api/v1/auth/login",
            json={"email": "nope6@x.com", "password": "Wrong123pass"},
        )
        assert r6.status_code == 429


class TestRateLimitsUpload:
    """Verify RATE_LIMIT_UPLOAD applies to file-upload endpoints."""

    def test_pdf_analyze_rate_limited(
        self,
        client: TestClient,
        auth_headers: dict,
        monkeypatch,
    ) -> None:
        _enable_limiter(monkeypatch)
        # Settings.RATE_LIMIT_UPLOAD = "10/minute"
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal PNG-ish bytes

        # First 10 calls should pass (some may 400 due to invalid PNG, but not 429)
        last_status = None
        for i in range(10):
            r = client.post(
                "/api/v1/pdf-generator/analyze",
                headers=auth_headers,
                files=[("files", (f"img{i}.png", io.BytesIO(png), "image/png"))],
            )
            last_status = r.status_code
            assert r.status_code != 429, f"Iteration {i} hit rate limit too early"

        # 11th call must be 429
        r11 = client.post(
            "/api/v1/pdf-generator/analyze",
            headers=auth_headers,
            files=[("files", ("img11.png", io.BytesIO(png), "image/png"))],
        )
        assert r11.status_code == 429, (
            f"Expected 429 on 11th call, got {r11.status_code} (last={last_status})"
        )


class TestRateLimitsBulkEnroll:
    """Verify RATE_LIMIT_UPLOAD applies to bulk_enroll."""

    def test_bulk_enroll_rate_limited(
        self,
        client: TestClient,
        test_class,
        auth_headers: dict,
        monkeypatch,
    ) -> None:
        _enable_limiter(monkeypatch)
        # RATE_LIMIT_UPLOAD = 10/minute
        csv = b"codigo,nombre,email\nA1,Ana,a@x.com\n"

        # First 10 calls — small CSV, should all return 200 (or 400 for already-enrolled)
        for i in range(10):
            r = client.post(
                f"/api/v1/classes/{test_class.id}/enrollments/bulk",
                headers=auth_headers,
                files={"file": (f"r{i}.csv", csv, "text/csv")},
            )
            assert r.status_code != 429, f"Iteration {i}: {r.status_code}"

        # 11th call must be 429
        r11 = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files={"file": ("r11.csv", csv, "text/csv")},
        )
        assert r11.status_code == 429
