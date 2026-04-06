from uuid import uuid4

from sqlalchemy import Column, DateTime, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    task_type = Column(String(50), nullable=False)  # grading, ocr_extraction, pdf_generation
    status = Column(String(50), default="pending")  # pending, processing, completed, failed
    progress = Column(Float, default=0.0)
    current_step = Column(String(255))
    result_data = Column(JSON)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime)
    completed_at = Column(DateTime)

    # Optional references
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=True)

    user = relationship("User", back_populates="task_logs")
