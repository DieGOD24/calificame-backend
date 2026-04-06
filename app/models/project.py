import enum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class ProjectStatus(enum.StrEnum):
    DRAFT = "draft"
    CONFIGURING = "configuring"
    ANSWER_KEY_UPLOADED = "answer_key_uploaded"
    ANSWER_KEY_PROCESSED = "answer_key_processed"
    CONFIRMED = "confirmed"
    GRADING = "grading"
    COMPLETED = "completed"


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    subject = Column(String(255))
    status = Column(String(50), default=ProjectStatus.DRAFT.value)
    config = Column(JSON)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    owner = relationship("User", back_populates="projects")
    answer_key = relationship(
        "AnswerKey", back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    questions = relationship(
        "Question", back_populates="project", cascade="all, delete-orphan", order_by="Question.question_number"
    )
    student_exams = relationship("StudentExam", back_populates="project", cascade="all, delete-orphan")
