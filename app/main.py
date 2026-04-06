import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.answer_keys import router as answer_keys_router
from app.api.auth import router as auth_router
from app.api.grading import router as grading_router
from app.api.images import router as images_router
from app.api.institutions import router as institutions_router
from app.api.pdf_generator import router as pdf_generator_router
from app.api.projects import router as projects_router
from app.api.questions import router as questions_router
from app.api.student_exams import router as student_exams_router
from app.api.tasks import router as tasks_router
from app.api.analytics import router as analytics_router
from app.config import settings
from app.database import Base, engine
from app.logging_config import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown events."""
    setup_logging()
    logger.info("Starting Calificame API...")

    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")

    os.makedirs(settings.STORAGE_LOCAL_PATH, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    logger.info("Storage directories ready")

    yield

    logger.info("Shutting down Calificame API...")


app = FastAPI(
    title="Calificame API",
    version="2.0.0",
    description="Automated exam grading platform API with roles, institutions, and analytics",
    lifespan=lifespan,
)

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
app.include_router(analytics_router, prefix="/api/v1")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "version": "2.0.0"}
