from uuid import uuid4

from sqlalchemy import Boolean, Column, Float, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    student_exam_id = Column(String(36), ForeignKey("student_exams.id"), nullable=False)
    question_id = Column(String(36), ForeignKey("questions.id"), nullable=False)
    extracted_answer = Column(Text)
    is_correct = Column(Boolean)
    score = Column(Float, default=0.0)
    max_score = Column(Float)
    feedback = Column(Text)
    confidence = Column(Float)

    student_exam = relationship("StudentExam", back_populates="answers")
    question = relationship("Question", back_populates="exam_answers")
