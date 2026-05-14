import csv
import io

from fastapi.testclient import TestClient

from app.models.clase import Class, ClassEnrollment


class TestAddEnrollment:
    def test_add_single_student(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments",
            headers=auth_headers,
            json={
                "student_name": "Jane Doe",
                "student_identifier": "STU-100",
                "student_email": "jane@example.com",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["student_name"] == "Jane Doe"
        assert data["student_identifier"] == "STU-100"
        assert data["student_email"] == "jane@example.com"
        assert data["class_id"] == test_class.id

    def test_add_student_without_email(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments",
            headers=auth_headers,
            json={
                "student_name": "John Doe",
                "student_identifier": "STU-200",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["student_email"] is None

    def test_duplicate_rejected(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers: dict,
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments",
            headers=auth_headers,
            json={
                "student_name": "Duplicate Student",
                "student_identifier": test_enrollment.student_identifier,
            },
        )
        assert response.status_code == 409

    def test_invalid_data_422(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments",
            headers=auth_headers,
            json={
                "student_name": "",
                "student_identifier": "",
            },
        )
        assert response.status_code == 422

    def test_non_owner_forbidden(
        self, client: TestClient, test_class: Class, auth_headers_2: dict
    ) -> None:
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments",
            headers=auth_headers_2,
            json={
                "student_name": "Hacker",
                "student_identifier": "HACK-001",
            },
        )
        assert response.status_code == 403


class TestBulkEnroll:
    def _make_csv_upload(self, rows: list[list[str]]) -> tuple[str, bytes, str]:
        """Create a CSV file tuple for upload."""
        output = io.StringIO()
        writer = csv.writer(output)
        for row in rows:
            writer.writerow(row)
        content = output.getvalue().encode("utf-8")
        return ("file", ("students.csv", content, "text/csv"))

    def test_bulk_upload_csv(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        file_tuple = self._make_csv_upload([
            ["nombre", "codigo", "email"],
            ["Alice Smith", "STU-301", "alice@example.com"],
            ["Bob Jones", "STU-302", "bob@example.com"],
            ["Carol White", "STU-303", ""],
        ])
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files=[file_tuple],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["added"] == 3
        assert data["skipped"] == 0

    def test_bulk_upload_skips_duplicates(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers: dict,
    ) -> None:
        file_tuple = self._make_csv_upload([
            ["nombre", "codigo"],
            [test_enrollment.student_name, test_enrollment.student_identifier],
            ["New Student", "STU-NEW"],
        ])
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files=[file_tuple],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["added"] == 1
        assert data["skipped"] == 1

    def test_bulk_upload_missing_columns(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        file_tuple = self._make_csv_upload([
            ["random_col", "another_col"],
            ["foo", "bar"],
        ])
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files=[file_tuple],
        )
        assert response.status_code == 400

    def test_bulk_upload_empty_file(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        file_tuple = self._make_csv_upload([
            ["nombre", "codigo"],
        ])
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers,
            files=[file_tuple],
        )
        assert response.status_code == 400

    def test_bulk_upload_non_owner_forbidden(
        self, client: TestClient, test_class: Class, auth_headers_2: dict
    ) -> None:
        file_tuple = self._make_csv_upload([
            ["nombre", "codigo"],
            ["Alice Smith", "STU-301"],
        ])
        response = client.post(
            f"/api/v1/classes/{test_class.id}/enrollments/bulk",
            headers=auth_headers_2,
            files=[file_tuple],
        )
        assert response.status_code == 403


class TestListEnrollments:
    def test_list_students(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/enrollments", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["student_identifier"] == test_enrollment.student_identifier

    def test_list_empty(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/enrollments", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_non_owner_non_enrolled_forbidden(
        self, client: TestClient, test_class: Class, auth_headers_2: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/enrollments", headers=auth_headers_2
        )
        assert response.status_code == 403


class TestRemoveEnrollment:
    def test_remove_student(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers: dict,
    ) -> None:
        response = client.delete(
            f"/api/v1/classes/{test_class.id}/enrollments/{test_enrollment.id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify removal
        response = client.get(
            f"/api/v1/classes/{test_class.id}/enrollments", headers=auth_headers
        )
        assert response.json() == []

    def test_remove_nonexistent_404(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.delete(
            f"/api/v1/classes/{test_class.id}/enrollments/nonexistent-id",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_remove_non_owner_forbidden(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers_2: dict,
    ) -> None:
        response = client.delete(
            f"/api/v1/classes/{test_class.id}/enrollments/{test_enrollment.id}",
            headers=auth_headers_2,
        )
        assert response.status_code == 403
