from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.clase import Class
from app.models.institution import Institution, InstitutionMember
from app.models.project import Project, ProjectStatus
from app.models.student_exam import StudentExam
from app.models.user import User


def _make_project(db: Session, owner: User, name: str = "Exam") -> Project:
    project = Project(
        id=str(uuid4()),
        owner_id=owner.id,
        name=name,
        status=ProjectStatus.CONFIRMED.value,
        config={"exam_type": "multiple_choice", "total_questions": 2},
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _make_graded_exam(
    db: Session,
    project: Project,
    identifier: str = "STU-001",
    percentage: float = 80.0,
    score: float = 8.0,
    max_score: float = 10.0,
) -> StudentExam:
    exam = StudentExam(
        id=str(uuid4()),
        project_id=project.id,
        student_name="Student A",
        student_identifier=identifier,
        file_path="/fake/path.png",
        status="graded",
        total_score=score,
        max_score=max_score,
        grade_percentage=percentage,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam


class TestProjectAnalytics:
    def test_with_graded_exams(self, client: TestClient, db: Session, test_user: User, auth_headers: dict) -> None:
        project = _make_project(db, test_user)
        _make_graded_exam(db, project, percentage=80.0, score=8.0)
        _make_graded_exam(db, project, identifier="STU-002", percentage=60.0, score=6.0)

        response = client.get(f"/api/v1/analytics/projects/{project.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == project.id
        assert data["graded_count"] == 2
        assert data["total_exams"] == 2
        assert data["average_percentage"] == 70.0
        assert data["pass_rate"] is not None

    def test_without_exams(self, client: TestClient, db: Session, test_user: User, auth_headers: dict) -> None:
        project = _make_project(db, test_user)
        response = client.get(f"/api/v1/analytics/projects/{project.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["graded_count"] == 0
        assert data["average_percentage"] is None

    def test_not_found(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/analytics/projects/nonexistent", headers=auth_headers)
        assert response.status_code == 404

    def test_not_authorized(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        test_user_2: User,
        auth_headers_2: dict,
    ) -> None:
        project = _make_project(db, test_user)
        response = client.get(f"/api/v1/analytics/projects/{project.id}", headers=auth_headers_2)
        assert response.status_code == 403


class TestStudentProgress:
    def test_valid_student(self, client: TestClient, db: Session, test_user: User, auth_headers: dict) -> None:
        project = _make_project(db, test_user)
        _make_graded_exam(db, project, identifier="STU-100", percentage=90.0)

        response = client.get("/api/v1/analytics/students/STU-100", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["student_identifier"] == "STU-100"

    def test_no_records(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/analytics/students/NONEXISTENT", headers=auth_headers)
        assert response.status_code == 404


class TestInstitutionAnalytics:
    def test_valid(
        self,
        client: TestClient,
        db: Session,
        test_admin_user: User,
        auth_headers_admin: dict,
    ) -> None:
        inst = Institution(id=str(uuid4()), name="Analytics Inst", slug="analytics-inst")
        db.add(inst)
        db.flush()
        member = InstitutionMember(
            id=str(uuid4()),
            user_id=test_admin_user.id,
            institution_id=inst.id,
            role="owner",
        )
        db.add(member)
        db.commit()

        response = client.get(f"/api/v1/analytics/institutions/{inst.id}", headers=auth_headers_admin)
        assert response.status_code == 200
        data = response.json()
        assert data["institution_id"] == inst.id
        assert data["institution_name"] == "Analytics Inst"

    def test_not_found(self, client: TestClient, auth_headers_admin: dict, test_admin_user: User) -> None:
        response = client.get("/api/v1/analytics/institutions/nonexistent", headers=auth_headers_admin)
        assert response.status_code == 404

    def test_not_authorized(
        self,
        client: TestClient,
        db: Session,
        test_admin_user: User,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        inst = Institution(id=str(uuid4()), name="Private Inst", slug="private-inst")
        db.add(inst)
        db.flush()
        # Only add admin as member, not test_user
        member = InstitutionMember(
            id=str(uuid4()),
            user_id=test_admin_user.id,
            institution_id=inst.id,
            role="owner",
        )
        db.add(member)
        db.commit()

        response = client.get(f"/api/v1/analytics/institutions/{inst.id}", headers=auth_headers)
        assert response.status_code == 403


class TestClassAnalytics:
    def test_valid(
        self,
        client: TestClient,
        db: Session,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        clase = Class(
            id=str(uuid4()),
            professor_id=test_user.id,
            name="Math 101",
            subject="Math",
            semester="2026-1",
            is_active=True,
        )
        db.add(clase)
        db.commit()

        response = client.get(f"/api/v1/analytics/classes/{clase.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["class_id"] == clase.id
        assert data["class_name"] == "Math 101"

    def test_not_found(self, client: TestClient, auth_headers: dict) -> None:
        response = client.get("/api/v1/analytics/classes/nonexistent", headers=auth_headers)
        assert response.status_code == 404
