import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_role
from app.config import settings
from app.models.clase import Class, ClassEnrollment, ClassProject
from app.models.institution import InstitutionMember
from app.models.project import Project
from app.models.user import User, UserRole
from app.rate_limit import limiter
from app.schemas.clase import (
    BulkEnrollResponse,
    ClassCreate,
    ClassEnrollmentCreate,
    ClassEnrollmentResponse,
    ClassListResponse,
    ClassProjectAdd,
    ClassProjectReorder,
    ClassProjectResponse,
    ClassResponse,
    ClassUpdate,
    GradebookResponse,
    StudentProgressResponse,
)
from app.services.enrollment import auto_link_users, flatten_to_text, parse_student_file
from app.services.gradebook import (
    build_gradebook,
    export_gradebook_csv,
    export_gradebook_xlsx,
    get_student_progress,
)

router = APIRouter(prefix="/classes", tags=["Classes"])


def _class_to_response(clase: Class) -> ClassResponse:
    """Convert a Class model to response schema."""
    return ClassResponse(
        id=clase.id,
        professor_id=clase.professor_id,
        institution_id=clase.institution_id,
        name=clase.name,
        subject=clase.subject,
        semester=clase.semester,
        description=clase.description,
        schedule=clase.schedule,
        is_active=clase.is_active,
        created_at=clase.created_at,
        updated_at=clase.updated_at,
        professor_name=clase.professor.full_name if clase.professor else "",
        enrollment_count=len(clase.enrollments) if clase.enrollments else 0,
        project_count=len(clase.class_projects) if clase.class_projects else 0,
    )


def _get_class_or_404(db: Session, class_id: str) -> Class:
    """Get a class or raise 404."""
    clase = db.query(Class).filter(Class.id == class_id).first()
    if clase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class not found")
    return clase


def _check_class_owner(clase: Class, user: User, db: Session | None = None) -> None:
    """Check if the user can manage this class.

    Allowed: global developer/admin, the class's professor, or — when ``db`` is
    provided — an institution-role user who is owner/admin of the institution
    that owns the class.
    """
    if user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        return
    if clase.professor_id == user.id:
        return
    if (
        db is not None
        and user.role == UserRole.INSTITUTION.value
        and clase.institution_id
        and clase.institution_id in _institution_admin_ids(db, user)
    ):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")


def _institution_admin_ids(db: Session, user: User) -> list[str]:
    """Return institution IDs where ``user`` is an owner/admin member.

    Used so users with the global ``institution`` role can manage the classes
    that belong to the institutions they administer, even when they aren't the
    teaching professor of the class.
    """
    rows = (
        db.query(InstitutionMember.institution_id)
        .filter(
            InstitutionMember.user_id == user.id,
            InstitutionMember.role.in_(["owner", "admin"]),
        )
        .all()
    )
    return [r[0] for r in rows]


def _can_view_class(db: Session, clase: Class, user: User) -> bool:
    """Check if user can view this class (owner, enrolled student, admin)."""
    if user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        return True
    if clase.professor_id == user.id:
        return True
    # Institution-role users administer their institution's classes.
    if (
        user.role == UserRole.INSTITUTION.value
        and clase.institution_id
        and clase.institution_id in _institution_admin_ids(db, user)
    ):
        return True
    # Check enrollment
    enrollment = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == clase.id,
            ClassEnrollment.user_id == user.id,
        )
        .first()
    )
    return enrollment is not None


# ─── CRUD ───────────────────────────────────────────────────


@router.post("", response_model=ClassResponse, status_code=status.HTTP_201_CREATED)
def create_class(
    data: ClassCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_role(UserRole.DEVELOPER, UserRole.ADMIN, UserRole.INSTITUTION, UserRole.PROFESSOR)
    ),
) -> ClassResponse:
    """Create a new class.

    The creator becomes the professor by default. Developer/admin/institution
    roles may pass ``professor_id`` to assign someone else — the target must
    exist and have a teaching role (developer, admin, or professor). Plain
    professors cannot reassign and ``professor_id`` is ignored for them.
    """
    professor_id = current_user.id

    if data.professor_id and data.professor_id != current_user.id:
        # Only privileged roles may set a different professor.
        if current_user.role not in (
            UserRole.DEVELOPER.value,
            UserRole.ADMIN.value,
            UserRole.INSTITUTION.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins and institutions can assign a different professor",
            )
        target = db.query(User).filter(User.id == data.professor_id).first()
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="professor_id does not point to a known user",
            )
        if target.role not in (
            UserRole.DEVELOPER.value,
            UserRole.ADMIN.value,
            UserRole.PROFESSOR.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User {target.email} has role '{target.role}' and cannot be a professor of a class",
            )
        professor_id = target.id

    clase = Class(
        professor_id=professor_id,
        institution_id=data.institution_id,
        name=data.name,
        subject=data.subject,
        semester=data.semester,
        description=data.description,
        schedule=data.schedule,
    )
    db.add(clase)
    db.commit()
    db.refresh(clase)

    logger.info(f"User {current_user.id} created class {clase.id} ({clase.name}); professor_id={professor_id}")
    return _class_to_response(clase)


@router.get("", response_model=ClassListResponse)
def list_classes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    semester: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ClassListResponse:
    """List classes. Professors see own, students see enrolled, admins see all.

    Institution-role users see every class in the institutions they administer
    (owner/admin membership), so they can manage them from the UI even though
    they aren't the teaching professor of any class.
    """
    if current_user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        query = db.query(Class)
    elif current_user.role == UserRole.STUDENT.value:
        enrolled_class_ids = (
            db.query(ClassEnrollment.class_id).filter(ClassEnrollment.user_id == current_user.id).scalar_subquery()
        )
        query = db.query(Class).filter(Class.id.in_(enrolled_class_ids))
    elif current_user.role == UserRole.INSTITUTION.value:
        admin_inst_ids = _institution_admin_ids(db, current_user)
        if admin_inst_ids:
            query = db.query(Class).filter(Class.institution_id.in_(admin_inst_ids))
        else:
            # Institution role without any admin/owner membership — show nothing.
            query = db.query(Class).filter(Class.id == "__never__")
    else:
        query = db.query(Class).filter(Class.professor_id == current_user.id)

    if semester:
        query = query.filter(Class.semester == semester)

    total = query.count()
    classes = query.order_by(Class.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return ClassListResponse(
        items=[_class_to_response(c) for c in classes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{class_id}", response_model=ClassResponse)
def get_class(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ClassResponse:
    """Get class detail."""
    clase = _get_class_or_404(db, class_id)
    if not _can_view_class(db, clase, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return _class_to_response(clase)


@router.put("/{class_id}", response_model=ClassResponse)
def update_class(
    class_id: str,
    data: ClassUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ClassResponse:
    """Update a class.

    `professor_id` and `institution_id` can only be changed by Developer/Admin
    (acts as a transfer/reassignment). Other fields follow the usual ownership
    rules.
    """
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    update_data = data.model_dump(exclude_unset=True)

    is_admin = current_user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value)
    admin_only = {"professor_id", "institution_id"}
    if not is_admin and any(k in update_data for k in admin_only):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only developers/admins can change professor_id or institution_id",
        )

    if "professor_id" in update_data and update_data["professor_id"] != clase.professor_id:
        new_prof = db.query(User).filter(User.id == update_data["professor_id"]).first()
        if new_prof is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target professor not found")
        if new_prof.role not in (
            UserRole.PROFESSOR.value,
            UserRole.ADMIN.value,
            UserRole.DEVELOPER.value,
            UserRole.INSTITUTION.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target user must have a teaching role (professor/admin/developer/institution)",
            )

    if "institution_id" in update_data and update_data["institution_id"]:
        from app.models.institution import Institution

        if not db.query(Institution).filter(Institution.id == update_data["institution_id"]).first():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target institution not found")

    for field, value in update_data.items():
        setattr(clase, field, value)

    db.commit()
    db.refresh(clase)

    logger.info(f"User {current_user.id} updated class {class_id} (fields: {list(update_data.keys())})")
    return _class_to_response(clase)


@router.delete("/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    """Delete a class and cascade to enrollments and class projects."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    db.delete(clase)
    db.commit()
    logger.info(f"User {current_user.id} deleted class {class_id}")


# ─── ENROLLMENTS ─────────��──────────────────────────────────


@router.post(
    "/{class_id}/enrollments",
    response_model=ClassEnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_enrollment(
    class_id: str,
    data: ClassEnrollmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ClassEnrollmentResponse:
    """Add a single student to the class."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    # Check duplicate
    existing = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_identifier == data.student_identifier,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Student with identifier '{data.student_identifier}' already enrolled",
        )

    enrollment = ClassEnrollment(
        class_id=class_id,
        student_name=data.student_name,
        student_identifier=data.student_identifier,
        student_email=data.student_email,
    )

    # Auto-link user
    if data.student_email:
        user = db.query(User).filter(User.email == data.student_email).first()
        if user:
            enrollment.user_id = user.id

    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)

    logger.info(f"Enrolled student {data.student_identifier} in class {class_id}")
    return ClassEnrollmentResponse.model_validate(enrollment)


@router.post("/{class_id}/enrollments/bulk", response_model=BulkEnrollResponse)
@limiter.limit(settings.RATE_LIMIT_UPLOAD)
async def bulk_enroll(
    request: Request,
    class_id: str,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> BulkEnrollResponse:
    """Upload CSV/Excel to enroll students in bulk.

    Tries a heuristic parser first (handles UTP-style rosters with metadata rows,
    Spanish column names, etc). If nothing is extracted, falls back to GPT-4o.
    """
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    # Read file once so we can try the AI fallback if the heuristic fails.
    content = await file.read()
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo excede el tamaño maximo permitido ({settings.MAX_FILE_SIZE_MB} MB)",
        )

    class _BufferedUpload:
        """Lightweight UploadFile stand-in that replays buffered bytes."""

        def __init__(self, filename: str, data: bytes):
            self.filename = filename
            self._data = data

        async def read(self) -> bytes:
            return self._data

    buffered = _BufferedUpload(file.filename or "", content)

    try:
        records = await parse_student_file(buffered)  # type: ignore[arg-type]
    except ValueError as e:
        # Heuristic parser raised a clear error (bad encoding, corrupt file, etc.)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    used_ai = False
    if not records:
        # Heuristic could not match columns — try GPT-4o with the flattened table.
        if not settings.OPENAI_API_KEY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No se reconocieron columnas de la lista y la extraccion por IA "
                    "no esta disponible (falta OPENAI_API_KEY). Asegurate de que el "
                    "archivo tenga columnas con nombres como Documento, Nombres y EMAIL."
                ),
            )
        table_text = flatten_to_text(content, file.filename or "")
        if not table_text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El archivo esta vacio o no se pudo leer.",
            )
        try:
            from app.agents.enrollment_extraction_agent import EnrollmentExtractionAgent

            agent = EnrollmentExtractionAgent()
            ai_rows = agent.execute(table_text=table_text)
        except Exception as exc:
            logger.error("AI enrollment extraction failed: {}", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"La extraccion por IA fallo: {exc}",
            )

        records = [
            {
                "student_name": str(r.get("student_name") or "").strip(),
                "student_identifier": str(r.get("student_identifier") or "").strip(),
                "student_email": (str(r.get("student_email")).strip().lower() if r.get("student_email") else None),
            }
            for r in ai_rows
            if r.get("student_name") and r.get("student_identifier")
        ]
        used_ai = True
        logger.info("AI-extracted {} enrollment records for class {}", len(records), class_id)

    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No se encontraron estudiantes en el archivo. Revisa que tenga "
                "columnas con nombre, documento/codigo y email."
            ),
        )

    records = auto_link_users(db, records)

    # Get existing identifiers for this class
    existing_ids = set(
        row[0]
        for row in db.query(ClassEnrollment.student_identifier).filter(ClassEnrollment.class_id == class_id).all()
    )

    added = 0
    skipped = 0
    errors: list[str] = []

    for record in records:
        identifier = record["student_identifier"]
        if identifier in existing_ids:
            skipped += 1
            continue

        enrollment = ClassEnrollment(
            class_id=class_id,
            student_name=record["student_name"],
            student_identifier=identifier,
            student_email=record.get("student_email"),
            user_id=record.get("user_id"),
        )
        db.add(enrollment)
        existing_ids.add(identifier)
        added += 1

    db.commit()
    logger.info(f"Bulk enrolled {added} students in class {class_id} (skipped={skipped})")

    return BulkEnrollResponse(added=added, skipped=skipped, errors=errors, used_ai=used_ai)


@router.get("/{class_id}/enrollments", response_model=list[ClassEnrollmentResponse])
def list_enrollments(
    class_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[ClassEnrollmentResponse]:
    """List enrolled students in a class (paginated)."""
    clase = _get_class_or_404(db, class_id)
    if not _can_view_class(db, clase, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    offset = (page - 1) * per_page
    enrollments = (
        db.query(ClassEnrollment)
        .filter(ClassEnrollment.class_id == class_id)
        .order_by(ClassEnrollment.student_name)
        .offset(offset)
        .limit(per_page)
        .all()
    )
    return [ClassEnrollmentResponse.model_validate(e) for e in enrollments]


@router.delete("/{class_id}/enrollments/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_enrollment(
    class_id: str,
    enrollment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    """Remove a student from the class."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    enrollment = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.id == enrollment_id,
            ClassEnrollment.class_id == class_id,
        )
        .first()
    )
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment not found")

    db.delete(enrollment)
    db.commit()
    logger.info(f"Removed enrollment {enrollment_id} from class {class_id}")


# ��── CLASS PROJECTS ─────────────────────────────────────────


@router.post(
    "/{class_id}/projects",
    response_model=ClassProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_class_project(
    class_id: str,
    data: ClassProjectAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ClassProjectResponse:
    """Link a project to a class."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    # Verify project exists and belongs to the professor
    project = db.query(Project).filter(Project.id == data.project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if current_user.role not in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        if project.owner_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your project")

    # Check if already linked
    existing = db.query(ClassProject).filter(ClassProject.project_id == data.project_id).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This project is already linked to a class",
        )

    # Get max order
    max_order = (
        db.query(ClassProject.display_order)
        .filter(ClassProject.class_id == class_id)
        .order_by(ClassProject.display_order.desc())
        .first()
    )
    next_order = (max_order[0] + 1) if max_order else 0

    cp = ClassProject(
        class_id=class_id,
        project_id=data.project_id,
        display_order=next_order,
    )
    db.add(cp)
    db.commit()
    db.refresh(cp)

    logger.info(f"Linked project {data.project_id} to class {class_id}")
    return ClassProjectResponse(
        id=cp.id,
        project_id=cp.project_id,
        project_name=project.name,
        project_status=project.status,
        display_order=cp.display_order,
    )


@router.get("/{class_id}/projects", response_model=list[ClassProjectResponse])
def list_class_projects(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[ClassProjectResponse]:
    """List projects in a class."""
    clase = _get_class_or_404(db, class_id)
    if not _can_view_class(db, clase, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    class_projects = (
        db.query(ClassProject).filter(ClassProject.class_id == class_id).order_by(ClassProject.display_order).all()
    )

    return [
        ClassProjectResponse(
            id=cp.id,
            project_id=cp.project_id,
            project_name=cp.project.name if cp.project else "",
            project_status=cp.project.status if cp.project else "",
            display_order=cp.display_order,
        )
        for cp in class_projects
    ]


@router.delete("/{class_id}/projects/{class_project_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_class_project(
    class_id: str,
    class_project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    """Unlink a project from a class."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    cp = (
        db.query(ClassProject)
        .filter(
            ClassProject.id == class_project_id,
            ClassProject.class_id == class_id,
        )
        .first()
    )
    if cp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Class project not found")

    db.delete(cp)
    db.commit()
    logger.info(f"Unlinked project {cp.project_id} from class {class_id}")


@router.put("/{class_id}/projects/reorder", response_model=list[ClassProjectResponse])
def reorder_class_projects(
    class_id: str,
    data: ClassProjectReorder,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[ClassProjectResponse]:
    """Reorder projects in a class by providing ordered list of class_project IDs."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    class_projects = db.query(ClassProject).filter(ClassProject.class_id == class_id).all()
    cp_map = {cp.id: cp for cp in class_projects}

    for idx, cp_id in enumerate(data.order):
        if cp_id not in cp_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Class project ID '{cp_id}' not found in this class",
            )
        cp_map[cp_id].display_order = idx

    db.commit()
    logger.info(f"Reordered projects in class {class_id}")

    return list_class_projects(class_id, db=db, current_user=current_user)


# ─── GRADEBOOK ──────────���───────────────────────────────────


@router.get("/{class_id}/gradebook", response_model=GradebookResponse)
def get_gradebook(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> GradebookResponse:
    """Get the full gradebook for a class."""
    clase = _get_class_or_404(db, class_id)
    # Only professor/admin can see the full gradebook
    _check_class_owner(clase, current_user, db)
    return build_gradebook(db, clase)


@router.get("/{class_id}/gradebook/export")
def export_gradebook(
    class_id: str,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    """Export the gradebook as CSV or XLSX."""
    clase = _get_class_or_404(db, class_id)
    _check_class_owner(clase, current_user, db)

    gradebook = build_gradebook(db, clase)

    if format == "xlsx":
        content = export_gradebook_xlsx(gradebook)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"gradebook_{clase.semester}_{clase.name}.xlsx"
    else:
        content = export_gradebook_csv(gradebook)
        media_type = "text/csv"
        filename = f"gradebook_{clase.semester}_{clase.name}.csv"

    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get(
    "/{class_id}/students/{enrollment_id}/progress",
    response_model=StudentProgressResponse,
)
def get_enrollment_progress(
    class_id: str,
    enrollment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> StudentProgressResponse:
    """Get a single student's progress in a class."""
    clase = _get_class_or_404(db, class_id)

    enrollment = (
        db.query(ClassEnrollment)
        .filter(
            ClassEnrollment.id == enrollment_id,
            ClassEnrollment.class_id == class_id,
        )
        .first()
    )
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enrollment not found")

    # Access: owner, the student themselves, or admin
    if current_user.role not in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        if clase.professor_id != current_user.id and enrollment.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    return get_student_progress(db, clase, enrollment)
