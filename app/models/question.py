from uuid import uuid4

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Question(Base):
    __tablename__ = "questions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False)
    question_number = Column(Integer, nullable=False)
    question_text = Column(Text)
    correct_answer = Column(Text, nullable=False)
    points = Column(Float, default=1.0)
    is_confirmed = Column(Boolean, default=False)

    project = relationship("Project", back_populates="questions")
    exam_answers = relationship("ExamAnswer", back_populates="question", cascade="all, delete-orphan")
