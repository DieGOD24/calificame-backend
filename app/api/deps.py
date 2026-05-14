from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.clase import Class, ClassProject
from app.models.institution import InstitutionMember
from app.models.project import Project
from app.models.user import User, UserRole
from app.services.auth import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_db() -> Generator[Session, None, None]:
    """Provide a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Validate JWT token and return the current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token_data = decode_access_token(token)
    if token_data is None:
        raise credentials_exception

    user = db.query(User).filter(User.id == token_data.user_id).first()
    if user is None:
        raise credentials_exception

    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is active."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return current_user


def require_role(*roles: UserRole):
    """Dependency factory that checks if the user has one of the required roles."""

    def _check_role(current_user: User = Depends(get_current_active_user)) -> User:
        if current_user.role == UserRole.DEVELOPER.value:
            return current_user
        if current_user.role not in [r.value for r in roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' does not have permission for this action",
            )
        return current_user

    return _check_role


def can_user_access_project(db: Session, project: Project, user: User) -> bool:
    """Return True when `user` can read this project.

    Authorized callers:
    - Developer / admin (anything)
    - The project owner
    - The professor of any class to which this project is linked via
      ClassProject. This is the case the previous policy was missing — a
      class professor could see her gradebook but was 403'd on every
      `/projects/{id}/...` endpoint, including grading summary, exam list
      and analytics. (See QA report V3 — "media perdida" — for repro.)
    - An institution-role user who is owner/admin of an institution whose
      class links to this project. Without this, an institution admin
      could see classes via /classes but every project they touched
      returned 403 (see institution QA report — Bug #8).
    """
    if user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        return True
    if project.owner_id == user.id:
        return True
    # Is the user the professor of any class that links to this project?
    linked_class_professor_id = (
        db.query(Class.professor_id)
        .join(ClassProject, ClassProject.class_id == Class.id)
        .filter(ClassProject.project_id == project.id)
        .filter(Class.professor_id == user.id)
        .first()
    )
    if linked_class_professor_id is not None:
        return True
    # Is the user an institution admin of an institution whose class links to this project?
    if user.role == UserRole.INSTITUTION.value:
        admin_inst_ids_subq = (
            db.query(InstitutionMember.institution_id)
            .filter(
                InstitutionMember.user_id == user.id,
                InstitutionMember.role.in_(["owner", "admin"]),
            )
            .scalar_subquery()
        )
        institution_match = (
            db.query(Class.institution_id)
            .join(ClassProject, ClassProject.class_id == Class.id)
            .filter(ClassProject.project_id == project.id)
            .filter(Class.institution_id.in_(admin_inst_ids_subq))
            .first()
        )
        if institution_match is not None:
            return True
    return False


def get_user_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Project:
    """Get a project the current user can access (owner, class professor, admin)."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not can_user_access_project(db, project, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return project
