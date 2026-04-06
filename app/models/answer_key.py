from uuid import uuid4

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.database import Base


class AnswerKey(Base):
    __tablename__ = "answer_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, unique=True)
    original_filename = Column(String(255))
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(50))  # "pdf" or "images"
    num_pages = Column(Integer)
    processed_data = Column(JSON)  # raw OCR extraction
    is_processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    project = relationship("Project", back_populates="answer_key")
