from fastapi.testclient import TestClient

from app.models.clase import Class, ClassEnrollment


class TestCreateClass:
    def test_create_class(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/classes/",
            headers=auth_headers,
            json={
                "name": "Calculus I",
                "subject": "Mathematics",
                "semester": "2026-1",
                "description": "Intro to calculus",
                "schedule": "MWF 10:00-11:00",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Calculus I"
        assert data["subject"] == "Mathematics"
        assert data["semester"] == "2026-1"
        assert data["description"] == "Intro to calculus"
        assert data["schedule"] == "MWF 10:00-11:00"
        assert data["is_active"] is True
        assert data["enrollment_count"] == 0
        assert data["project_count"] == 0

    def test_create_class_minimal(self, client: TestClient, auth_headers: dict) -> None:
        response = client.post(
            "/api/v1/classes/",
            headers=auth_headers,
            json={
                "name": "Physics",
                "subject": "Science",
                "semester": "2026-1",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Physics"
        assert data["description"] is None
        assert data["schedule"] is None

    def test_create_class_student_forbidden(self, client: TestClient, auth_headers_student: dict) -> None:
        response = client.post(
            "/api/v1/classes/",
            headers=auth_headers_student,
            json={
                "name": "Forbidden Class",
                "subject": "Test",
                "semester": "2026-1",
            },
        )
        assert response.status_code == 403

    def test_create_class_unauthenticated(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/classes/",
            json={
                "name": "No Auth",
                "subject": "Test",
                "semester": "2026-1",
            },
        )
        assert response.status_code == 401

    def test_admin_can_assign_professor_id(
        self,
        client: TestClient,
        test_admin_user,
        test_user,
        auth_headers_admin: dict,
    ) -> None:
        """Admin/developer/institution may pick an existing teaching-role user as the class professor."""
        response = client.post(
            "/api/v1/classes/",
            headers=auth_headers_admin,
            json={
                "name": "Assigned Class",
                "subject": "X",
                "semester": "2026-1",
                "professor_id": test_user.id,  # test_user has role=professor
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["professor_id"] == test_user.id

    def test_cannot_assign_student_as_professor(
        self,
        client: TestClient,
        auth_headers_admin: dict,
        db,
    ) -> None:
        """Non-teaching roles (student / institution) must be rejected as professor_id."""
        from uuid import uuid4
        from app.models.user import User
        from app.services.auth import hash_password

        student = User(
            id=str(uuid4()),
            email="rejected-student@example.com",
            hashed_password=hash_password("x" * 16),
            full_name="Student User",
            role="student",
            is_active=True,
        )
        db.add(student)
        db.commit()

        response = client.post(
            "/api/v1/classes/",
            headers=auth_headers_admin,
            json={
                "name": "Bad Assignment",
                "subject": "X",
                "semester": "2026-1",
                "professor_id": student.id,
            },
        )
        assert response.status_code == 400
        assert "cannot be a professor" in response.json()["detail"]


class TestListClasses:
    def test_professor_sees_own_classes(self, client: TestClient, test_class: Class, auth_headers: dict) -> None:
        response = client.get("/api/v1/classes/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert any(c["id"] == test_class.id for c in data["items"])

    def test_student_sees_enrolled_classes(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers_student: dict,
    ) -> None:
        response = client.get("/api/v1/classes/", headers=auth_headers_student)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == test_class.id

    def test_admin_sees_all_classes(self, client: TestClient, test_class: Class, auth_headers_admin: dict) -> None:
        response = client.get("/api/v1/classes/", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1

    def test_list_classes_empty(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/classes/", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_filter_by_semester(self, client: TestClient, test_class: Class, auth_headers: dict) -> None:
        response = client.get("/api/v1/classes/", headers=auth_headers, params={"semester": "2026-1"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert all(c["semester"] == "2026-1" for c in data["items"])

    def test_filter_by_semester_no_match(self, client: TestClient, test_class: Class, auth_headers: dict) -> None:
        response = client.get("/api/v1/classes/", headers=auth_headers, params={"semester": "9999-9"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestGetClass:
    def test_owner_can_view(self, client: TestClient, test_class: Class, auth_headers: dict) -> None:
        response = client.get(f"/api/v1/classes/{test_class.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_class.id
        assert data["name"] == test_class.name

    def test_enrolled_student_can_view(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers_student: dict,
    ) -> None:
        response = client.get(f"/api/v1/classes/{test_class.id}", headers=auth_headers_student)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_class.id

    def test_other_user_forbidden(self, client: TestClient, test_class: Class, auth_headers_2: dict) -> None:
        response = client.get(f"/api/v1/classes/{test_class.id}", headers=auth_headers_2)
        assert response.status_code == 403

    def test_nonexistent_class_404(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/classes/nonexistent-id", headers=auth_headers)
        assert response.status_code == 404


class TestUpdateClass:
    def test_update_fields(self, client: TestClient, test_class: Class, auth_headers: dict) -> None:
        response = client.put(
            f"/api/v1/classes/{test_class.id}",
            headers=auth_headers,
            json={"name": "Updated Name", "description": "Updated desc"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["description"] == "Updated desc"

    def test_update_non_owner_forbidden(self, client: TestClient, test_class: Class, auth_headers_2: dict) -> None:
        response = client.put(
            f"/api/v1/classes/{test_class.id}",
            headers=auth_headers_2,
            json={"name": "Hacked"},
        )
        assert response.status_code == 403

    def test_owner_cannot_change_professor_id(
        self,
        client: TestClient,
        test_class: Class,
        test_user_2,
        auth_headers: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/classes/{test_class.id}",
            headers=auth_headers,
            json={"professor_id": test_user_2.id},
        )
        assert response.status_code == 403

    def test_admin_reassigns_professor(
        self,
        client: TestClient,
        test_class: Class,
        test_user_2,
        auth_headers_admin: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/classes/{test_class.id}",
            headers=auth_headers_admin,
            json={"professor_id": test_user_2.id},
        )
        assert response.status_code == 200
        assert response.json()["professor_id"] == test_user_2.id

    def test_admin_reassigns_to_invalid_professor_404(
        self,
        client: TestClient,
        test_class: Class,
        auth_headers_admin: dict,
    ) -> None:
        response = client.put(
            f"/api/v1/classes/{test_class.id}",
            headers=auth_headers_admin,
            json={"professor_id": "non-existent-id"},
        )
        assert response.status_code == 404


class TestDeleteClass:
    def test_owner_can_delete(self, client: TestClient, test_class: Class, auth_headers: dict) -> None:
        response = client.delete(f"/api/v1/classes/{test_class.id}", headers=auth_headers)
        assert response.status_code == 204

        # Verify deletion
        response = client.get(f"/api/v1/classes/{test_class.id}", headers=auth_headers)
        assert response.status_code == 404

    def test_non_owner_forbidden(self, client: TestClient, test_class: Class, auth_headers_2: dict) -> None:
        response = client.delete(f"/api/v1/classes/{test_class.id}", headers=auth_headers_2)
        assert response.status_code == 403

    def test_delete_cascades_enrollments(
        self,
        client: TestClient,
        db,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers: dict,
    ) -> None:
        enrollment_id = test_enrollment.id
        response = client.delete(f"/api/v1/classes/{test_class.id}", headers=auth_headers)
        assert response.status_code == 204

        # Verify enrollment was cascade-deleted
        assert db.query(ClassEnrollment).filter(ClassEnrollment.id == enrollment_id).first() is None
