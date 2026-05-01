from uuid import uuid4

from sqlalchemy import Boolean, Column, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    student_exam_id = Column(String(36), ForeignKey("student_exams.id"), nullable=False, index=True)
    question_id = Column(String(36), ForeignKey("questions.id"), nullable=False, index=True)
    extracted_answer = Column(Text)
    is_correct = Column(Boolean)
    score = Column(Float, default=0.0)
    max_score = Column(Float)
    feedback = Column(Text)
    confidence = Column(Float)

    student_exam = relationship("StudentExam", back_populates="answers")
    question = relationship("Question", back_populates="exam_answers")

    __table_args__ = (
        # Index for analytics queries that group by question_id with is_correct filter
        Index("ix_exam_answers_question_correct", "question_id", "is_correct"),
    )
