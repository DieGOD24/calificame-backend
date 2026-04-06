from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class StudentExam(Base):
    __tablename__ = "student_exams"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    student_name = Column(String(255))
    student_identifier = Column(String(100))
    original_filename = Column(String(255))
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(50))
    status = Column(String(50), default="uploaded")  # uploaded, processing, graded, error
    total_score = Column(Float)
    max_score = Column(Float)
    grade_percentage = Column(Float)
    grading_details = Column(JSON)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now())
    graded_at = Column(DateTime)

    project = relationship("Project", back_populates="student_exams")
    answers = relationship("ExamAnswer", back_populates="student_exam", cascade="all, delete-orphan")
