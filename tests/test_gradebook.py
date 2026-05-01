import csv
import io
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.clase import Class, ClassEnrollment, ClassProject
from app.models.project import Project, ProjectStatus
from app.models.student_exam import StudentExam
from app.models.user import User


def _setup_graded_class(
    db: Session, test_user: User, test_class: Class
) -> tuple[ClassEnrollment, ClassEnrollment, Project, Project]:
    """Helper: create 2 enrollments, 2 projects linked to the class, and graded exams."""
    # Enrollments
    e1 = ClassEnrollment(
        id=str(uuid4()),
        class_id=test_class.id,
        student_name="Alice",
        student_identifier="STU-A",
    )
    e2 = ClassEnrollment(
        id=str(uuid4()),
        class_id=test_class.id,
        student_name="Bob",
        student_identifier="STU-B",
    )
    db.add_all([e1, e2])

    # Projects
    p1 = Project(
        id=str(uuid4()),
        owner_id=test_user.id,
        name="Exam 1",
        status=ProjectStatus.CONFIRMED.value,
    )
    p2 = Project(
        id=str(uuid4()),
        owner_id=test_user.id,
        name="Exam 2",
        status=ProjectStatus.CONFIRMED.value,
    )
    db.add_all([p1, p2])
    db.flush()

    # Link projects to class
    cp1 = ClassProject(
        id=str(uuid4()),
        class_id=test_class.id,
        project_id=p1.id,
        display_order=0,
    )
    cp2 = ClassProject(
        id=str(uuid4()),
        class_id=test_class.id,
        project_id=p2.id,
        display_order=1,
    )
    db.add_all([cp1, cp2])

    # Graded exams
    # Alice: graded on both projects
    db.add(StudentExam(
        id=str(uuid4()),
        project_id=p1.id,
        student_name="Alice",
        student_identifier="STU-A",
        file_path="/fake/alice_e1.pdf",
        status="graded",
        total_score=8.0,
        max_score=10.0,
        grade_percentage=80.0,
    ))
    db.add(StudentExam(
        id=str(uuid4()),
        project_id=p2.id,
        student_name="Alice",
        student_identifier="STU-A",
        file_path="/fake/alice_e2.pdf",
        status="graded",
        total_score=9.0,
        max_score=10.0,
        grade_percentage=90.0,
    ))
    # Bob: graded on project 1 only
    db.add(StudentExam(
        id=str(uuid4()),
        project_id=p1.id,
        student_name="Bob",
        student_identifier="STU-B",
        file_path="/fake/bob_e1.pdf",
        status="graded",
        total_score=5.0,
        max_score=10.0,
        grade_percentage=50.0,
    ))

    db.commit()
    db.refresh(e1)
    db.refresh(e2)
    return e1, e2, p1, p2


class TestGetGradebook:
    def test_gradebook_with_graded_exams(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        e1, e2, p1, p2 = _setup_graded_class(db, test_user, test_class)

        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["class_id"] == test_class.id
        assert data["class_name"] == test_class.name
        assert len(data["columns"]) == 2
        assert len(data["rows"]) == 2

        # Alice row
        alice_row = next(r for r in data["rows"] if r["student_identifier"] == "STU-A")
        assert alice_row["average"] == 85.0
        assert alice_row["pass_status"] == "passing"
        assert len(alice_row["projects"]) == 2

        # Bob row
        bob_row = next(r for r in data["rows"] if r["student_identifier"] == "STU-B")
        assert bob_row["average"] == 50.0
        assert bob_row["pass_status"] == "failing"

    def test_empty_gradebook(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["rows"] == []
        assert data["columns"] == []

    def test_partial_grading_shows_pending(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        _setup_graded_class(db, test_user, test_class)

        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()

        # Bob has only 1 of 2 graded -> his project 2 cell should have no score
        bob_row = next(r for r in data["rows"] if r["student_identifier"] == "STU-B")
        exam2_cell = bob_row["projects"][1]
        assert exam2_cell["score"] is None
        assert exam2_cell["percentage"] is None

    def test_gradebook_non_owner_forbidden(
        self, client: TestClient, test_class: Class, auth_headers_2: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook", headers=auth_headers_2
        )
        assert response.status_code == 403

    def test_gradebook_admin_can_view(
        self,
        client: TestClient,
        test_class: Class,
        auth_headers_admin: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook", headers=auth_headers_admin
        )
        assert response.status_code == 200


class TestExportGradebook:
    def test_export_csv(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        _setup_graded_class(db, test_user, test_class)

        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook/export",
            headers=auth_headers,
            params={"format": "csv"},
        )
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]

        # Parse CSV content
        content = response.content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # Header + 2 data rows
        assert len(rows) == 3
        header = rows[0]
        assert header[0] == "Estudiante"
        assert header[1] == "Codigo"
        assert "Exam 1" in header
        assert "Exam 2" in header

    def test_export_csv_empty(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook/export",
            headers=auth_headers,
            params={"format": "csv"},
        )
        assert response.status_code == 200
        content = response.content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # Only header row
        assert len(rows) == 1

    def test_export_xlsx(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        _setup_graded_class(db, test_user, test_class)

        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook/export",
            headers=auth_headers,
            params={"format": "xlsx"},
        )
        assert response.status_code == 200
        assert "spreadsheetml" in response.headers["content-type"]
        # XLSX files start with PK (zip signature)
        assert response.content[:2] == b"PK"

    def test_export_non_owner_forbidden(
        self, client: TestClient, test_class: Class, auth_headers_2: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/gradebook/export",
            headers=auth_headers_2,
            params={"format": "csv"},
        )
        assert response.status_code == 403


class TestStudentProgress:
    def test_student_progress(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_class: Class,
        auth_headers: dict,
    ) -> None:
        e1, e2, p1, p2 = _setup_graded_class(db, test_user, test_class)

        response = client.get(
            f"/api/v1/classes/{test_class.id}/students/{e1.id}/progress",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["student_name"] == "Alice"
        assert data["student_identifier"] == "STU-A"
        assert data["class_name"] == test_class.name
        assert len(data["projects"]) == 2
        assert data["average"] == 85.0

    def test_student_progress_no_grades(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/students/{test_enrollment.id}/progress",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["average"] is None
        assert data["projects"] == []

    def test_student_progress_nonexistent_enrollment(
        self, client: TestClient, test_class: Class, auth_headers: dict
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/students/nonexistent-id/progress",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_student_progress_non_owner_forbidden(
        self,
        client: TestClient,
        test_class: Class,
        test_enrollment: ClassEnrollment,
        auth_headers_2: dict,
    ) -> None:
        response = client.get(
            f"/api/v1/classes/{test_class.id}/students/{test_enrollment.id}/progress",
            headers=auth_headers_2,
        )
        assert response.status_code == 403
