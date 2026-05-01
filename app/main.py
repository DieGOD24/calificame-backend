import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.analytics import router as analytics_router
from app.api.answer_keys import router as answer_keys_router
from app.api.auth import router as auth_router
from app.api.classes import router as classes_router
from app.api.grading import router as grading_router
from app.api.images import router as images_router
from app.api.institutions import router as institutions_router
from app.api.pdf_generator import router as pdf_generator_router
from app.api.projects import router as projects_router
from app.api.questions import router as questions_router
from app.api.student_exams import router as student_exams_router
from app.api.tasks import router as tasks_router
from app.config import settings
from app.database import Base, engine
from app.logging_config import setup_logging
from app.rate_limit import limiter


def _recover_stale_work() -> None:
    """Mark orphaned tasks as failed and reset stuck student exams on startup.

    After a server restart, TaskLog rows stuck in pending/processing and
    StudentExam rows stuck in 'processing' will never transition on their own.
    Reset them so the user can retry.
    """
    from app.database import SessionLocal
    from app.models.student_exam import StudentExam
    from app.models.task_log import TaskLog

    db = SessionLocal()
    try:
        stale_tasks = db.query(TaskLog).filter(TaskLog.status.in_(["pending", "processing"])).all()
        for task in stale_tasks:
            task.status = "failed"
            task.error_message = "Task interrupted by server restart. Please retry."
        if stale_tasks:
            db.commit()
            logger.info("Recovered {} stale tasks", len(stale_tasks))

        stuck_exams = db.query(StudentExam).filter(StudentExam.status == "processing").all()
        for exam in stuck_exams:
            exam.status = "uploaded"
            exam.error_message = "Procesamiento interrumpido por reinicio del servidor. Reintenta la calificacion."
        if stuck_exams:
            db.commit()
            logger.info("Recovered {} stuck student exams", len(stuck_exams))
    except Exception as exc:
        logger.warning("Could not recover stale work: {}", exc)
    finally:
        db.close()


def _seed_demo_user() -> None:
    """Create a demo developer user if no users exist."""
    from app.database import SessionLocal
    from app.models.user import User, UserRole
    from app.services.auth import hash_password

    db = SessionLocal()
    try:
        if db.query(User).first() is None:
            demo = User(
                email="demo@calificame.com",
                hashed_password=hash_password("demo1234"),
                full_name="Profesor Demo",
                role=UserRole.DEVELOPER.value,
            )
            db.add(demo)
            db.commit()
            logger.info("Demo user created: demo@calificame.com (developer)")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown events."""
    setup_logging()
    logger.info("Starting Calificame API...")

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created/verified")
    except (IntegrityError, OperationalError) as exc:
        # With multiple workers, another process may have created tables already.
        # PostgreSQL raises IntegrityError on pg_type_typname_nsp_index conflicts.
        logger.warning("create_all race condition (tables likely exist): {}", exc)
        engine.dispose()

    os.makedirs(settings.STORAGE_LOCAL_PATH, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    logger.info("Storage directories ready")

    _seed_demo_user()
    _recover_stale_work()

    yield

    logger.info("Shutting down Calificame API...")


app = FastAPI(
    title="Calificame API",
    version="3.0.0",
    description="Automated exam grading platform API with roles, institutions, and analytics",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
)

# Auth & core
app.include_router(auth_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(answer_keys_router, prefix="/api/v1")
app.include_router(questions_router, prefix="/api/v1")
app.include_router(student_exams_router, prefix="/api/v1")
app.include_router(grading_router, prefix="/api/v1")
app.include_router(images_router, prefix="/api/v1")

# New features
app.include_router(tasks_router, prefix="/api/v1")
app.include_router(institutions_router, prefix="/api/v1")
app.include_router(pdf_generator_router, prefix="/api/v1")
app.include_router(classes_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint with database connectivity check."""
    checks = {"api": "healthy", "version": "3.0.0"}
    try:
        from app.database import SessionLocal

        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = "healthy"
    except Exception:
        checks["database"] = "unhealthy"
    checks["status"] = "healthy" if checks["database"] == "healthy" else "degraded"
    return checks
