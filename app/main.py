import os
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.answer_keys import router as answer_keys_router
from app.api.auth import router as auth_router
from app.api.grading import router as grading_router
from app.api.projects import router as projects_router
from app.api.questions import router as questions_router
from app.api.student_exams import router as student_exams_router
from app.config import settings
from app.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown events."""
    # Create tables (for development / SQLite)
    Base.metadata.create_all(bind=engine)

    # Create upload directory
    os.makedirs(settings.STORAGE_LOCAL_PATH, exist_ok=True)

    yield


app = FastAPI(
    title="Calificame API",
    version="1.0.0",
    description="Automated exam grading platform API",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(answer_keys_router, prefix="/api/v1")
app.include_router(questions_router, prefix="/api/v1")
app.include_router(student_exams_router, prefix="/api/v1")
app.include_router(grading_router, prefix="/api/v1")


@app.get("/health")
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "version": "1.0.0"}
