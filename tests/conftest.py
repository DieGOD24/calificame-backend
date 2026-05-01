import os

os.environ["TESTING"] = "1"

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db
from app.database import Base
from app.main import app
from app.models.clase import Class, ClassEnrollment
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.user import User
from app.services.auth import create_access_token, hash_password
from app.services.storage import LocalStorageService, set_storage_service

# In-memory SQLite for tests
TEST_DATABASE_URL = "sqlite://"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def _setup_db() -> Generator[None, None, None]:
    """Create and drop all tables for each test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _stub_ai_enrollment_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the OpenAI-backed EnrollmentExtractionAgent with a no-op stub.

    Tests that need real AI behavior should override this by re-patching the
    agent inside the test. Default behavior: ``execute()`` returns ``[]`` so
    the bulk-enroll endpoint falls through to the "no records" 400 error
    path instead of hitting the OpenAI API with a fake key (and 502'ing).
    """

    class _StubAgent:
        def execute(self, *_args: Any, **_kwargs: Any) -> list[Any]:
            return []

    # Force import so monkeypatch.setattr finds the attribute.
    import app.agents.enrollment_extraction_agent as _agent_mod  # noqa: I001
    monkeypatch.setattr(_agent_mod, "EnrollmentExtractionAgent", _StubAgent)


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    """Provide a test database session."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def _override_deps(db: Session) -> Generator[None, None, None]:
    """Override FastAPI dependencies for testing."""

    def _get_test_db() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _get_test_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client(_override_deps: None) -> TestClient:
    """Provide a test HTTP client."""
    return TestClient(app)


@pytest.fixture()
def test_user(db: Session) -> User:
    """Create a test user."""
    user = User(
        id=str(uuid4()),
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        full_name="Test User",
        role="professor",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def test_user_2(db: Session) -> User:
    """Create a second test user."""
    user = User(
        id=str(uuid4()),
        email="other@example.com",
        hashed_password=hash_password("otherpassword123"),
        full_name="Other User",
        role="professor",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def auth_token(test_user: User) -> str:
    """Create an auth token for the test user."""
    return create_access_token(data={"sub": test_user.id})


@pytest.fixture()
def auth_headers(auth_token: str) -> dict[str, str]:
    """Auth headers for the test user."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture()
def auth_token_2(test_user_2: User) -> str:
    """Create an auth token for the second test user."""
    return create_access_token(data={"sub": test_user_2.id})


@pytest.fixture()
def auth_headers_2(auth_token_2: str) -> dict[str, str]:
    """Auth headers for the second test user."""
    return {"Authorization": f"Bearer {auth_token_2}"}


@pytest.fixture()
def test_admin_user(db: Session) -> User:
    """Create a test admin user."""
    user = User(
        id=str(uuid4()),
        email="admin@example.com",
        hashed_password=hash_password("adminpassword123"),
        full_name="Admin User",
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def test_student_user(db: Session) -> User:
    """Create a test student user."""
    user = User(
        id=str(uuid4()),
        email="student@example.com",
        hashed_password=hash_password("studentpassword123"),
        full_name="Student User",
        role="student",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def test_developer_user(db: Session) -> User:
    """Create a test developer user."""
    user = User(
        id=str(uuid4()),
        email="developer@example.com",
        hashed_password=hash_password("developerpassword123"),
        full_name="Developer User",
        role="developer",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def auth_token_admin(test_admin_user: User) -> str:
    """Create an auth token for the admin user."""
    return create_access_token(data={"sub": test_admin_user.id})


@pytest.fixture()
def auth_headers_admin(auth_token_admin: str) -> dict[str, str]:
    """Auth headers for the admin user."""
    return {"Authorization": f"Bearer {auth_token_admin}"}


@pytest.fixture()
def auth_token_student(test_student_user: User) -> str:
    """Create an auth token for the student user."""
    return create_access_token(data={"sub": test_student_user.id})


@pytest.fixture()
def auth_headers_student(auth_token_student: str) -> dict[str, str]:
    """Auth headers for the student user."""
    return {"Authorization": f"Bearer {auth_token_student}"}


@pytest.fixture()
def auth_token_developer(test_developer_user: User) -> str:
    """Create an auth token for the developer user."""
    return create_access_token(data={"sub": test_developer_user.id})


@pytest.fixture()
def auth_headers_developer(auth_token_developer: str) -> dict[str, str]:
    """Auth headers for the developer user."""
    return {"Authorization": f"Bearer {auth_token_developer}"}


@pytest.fixture()
def test_class(db: Session, test_user: User) -> Class:
    """Create a test class owned by test_user."""
    clase = Class(
        id=str(uuid4()),
        professor_id=test_user.id,
        name="Algebra Lineal",
        subject="Mathematics",
        semester="2026-1",
        description="Linear algebra course",
        is_active=True,
    )
    db.add(clase)
    db.commit()
    db.refresh(clase)
    return clase


@pytest.fixture()
def test_enrollment(db: Session, test_class: Class, test_student_user: User) -> ClassEnrollment:
    """Create a test enrollment linking student to class."""
    enrollment = ClassEnrollment(
        id=str(uuid4()),
        class_id=test_class.id,
        student_name=test_student_user.full_name,
        student_identifier="STU-001",
        student_email=test_student_user.email,
        user_id=test_student_user.id,
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


@pytest.fixture()
def test_project(db: Session, test_user: User) -> Project:
    """Create a test project."""
    project = Project(
        id=str(uuid4()),
        owner_id=test_user.id,
        name="Test Exam",
        description="A test exam project",
        subject="Mathematics",
        status=ProjectStatus.DRAFT.value,
        config={
            "exam_type": "multiple_choice",
            "total_questions": 5,
            "points_per_question": 2.0,
            "has_multiple_pages": False,
        },
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture()
def confirmed_project_with_questions(db: Session, test_project: Project) -> tuple[Project, list[Question]]:
    """Create a project with confirmed questions."""
    questions: list[Question] = []
    for i in range(1, 6):
        q = Question(
            id=str(uuid4()),
            project_id=test_project.id,
            question_number=i,
            question_text=f"What is {i} + {i}?",
            correct_answer=str(i + i),
            points=2.0,
            is_confirmed=True,
        )
        db.add(q)
        questions.append(q)

    test_project.status = ProjectStatus.CONFIRMED.value
    db.commit()
    for q in questions:
        db.refresh(q)
    db.refresh(test_project)
    return test_project, questions


@pytest.fixture()
def temp_storage(tmp_path: Any) -> Generator[LocalStorageService, None, None]:
    """Set up temporary local storage for tests."""
    storage = LocalStorageService(str(tmp_path / "uploads"))
    set_storage_service(storage)
    yield storage
    from app.services.storage import reset_storage_service

    reset_storage_service()


@pytest.fixture()
def mock_openai() -> MagicMock:
    """Create a mock OpenAI client."""
    mock = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "mocked response"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock.chat.completions.create.return_value = mock_response
    return mock
