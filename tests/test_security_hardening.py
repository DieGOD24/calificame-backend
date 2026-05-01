"""Security hardening tests: path traversal, oversized form fields, secret key validation."""
import io
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.models.project import Project
from app.services.storage import LocalStorageService


class TestPathTraversal:
    def test_save_rejects_traversal_path(self, tmp_path) -> None:
        storage = LocalStorageService(str(tmp_path / "uploads"))
        with pytest.raises(ValueError, match="traversal"):
            storage.save_file(b"x", "../../etc/passwd")

    def test_get_rejects_traversal_path(self, tmp_path) -> None:
        storage = LocalStorageService(str(tmp_path / "uploads"))
        with pytest.raises(ValueError, match="traversal"):
            storage.get_file("../../../boot.ini")

    def test_delete_rejects_traversal_path(self, tmp_path) -> None:
        storage = LocalStorageService(str(tmp_path / "uploads"))
        with pytest.raises(ValueError, match="traversal"):
            # Use forward slashes — backslashes are legal filename chars on Linux
            # so they don't get interpreted as path separators by Path.resolve().
            storage.delete_file("../../windows/system32/config")

    def test_normal_paths_still_work(self, tmp_path) -> None:
        """Defensive check: legitimate nested paths continue to work."""
        storage = LocalStorageService(str(tmp_path / "uploads"))
        storage.save_file(b"hello", "projects/abc/file.pdf")
        assert storage.get_file("projects/abc/file.pdf") == b"hello"
        storage.delete_file("projects/abc/file.pdf")

    def test_empty_path_rejected(self, tmp_path) -> None:
        storage = LocalStorageService(str(tmp_path / "uploads"))
        with pytest.raises(ValueError):
            storage.save_file(b"x", "")


class TestStudentExamFieldLimits:
    def test_student_name_too_long_returns_422(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage,
    ) -> None:
        huge_name = "x" * 10_000
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"files": ("e.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
            data={"student_name": huge_name, "student_identifier": "STU-1"},
        )
        assert response.status_code == 422

    def test_student_identifier_too_long_returns_422(
        self,
        client: TestClient,
        test_project: Project,
        auth_headers: dict,
        temp_storage,
    ) -> None:
        huge_id = "x" * 5_000
        response = client.post(
            f"/api/v1/projects/{test_project.id}/exams/upload",
            headers=auth_headers,
            files={"files": ("e.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
            data={"student_name": "Ana", "student_identifier": huge_id},
        )
        assert response.status_code == 422


class TestSecretKeyValidation:
    def test_dev_secret_rejected_in_production(self, monkeypatch) -> None:
        """When ENV=production and SECRET_KEY is the dev default, Settings must fail."""
        from app.config import _DEV_SECRET_KEY, Settings

        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("SECRET_KEY", _DEV_SECRET_KEY)
        with pytest.raises(Exception):
            Settings()

    def test_short_secret_always_rejected(self, monkeypatch) -> None:
        from app.config import Settings

        monkeypatch.setenv("SECRET_KEY", "tooshort")
        with pytest.raises(Exception):
            Settings()

    def test_dev_secret_allowed_in_dev(self, monkeypatch) -> None:
        """In dev/test environments the dev key still works (with a warning)."""
        from app.config import _DEV_SECRET_KEY, Settings

        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.setenv("SECRET_KEY", _DEV_SECRET_KEY)
        # Should not raise
        s = Settings()
        assert s.SECRET_KEY == _DEV_SECRET_KEY

    def test_strong_custom_secret_accepted(self, monkeypatch) -> None:
        from app.config import Settings

        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("SECRET_KEY", "a-very-long-and-strong-random-secret-32chars")
        s = Settings()
        assert len(s.SECRET_KEY) >= 16
