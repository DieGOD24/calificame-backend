from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class Class(Base):
    __tablename__ = "classes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    professor_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    institution_id = Column(String(36), ForeignKey("institutions.id"), nullable=True)
    name = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    semester = Column(String(50), nullable=False)
    description = Column(Text)
    schedule = Column(String(512))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    professor = relationship("User", back_populates="classes")
    institution = relationship("Institution", back_populates="classes")
    enrollments = relationship(
        "ClassEnrollment",
        back_populates="clase",
        cascade="all, delete-orphan",
    )
    class_projects = relationship(
        "ClassProject",
        back_populates="clase",
        cascade="all, delete-orphan",
        order_by="ClassProject.display_order",
    )


class ClassEnrollment(Base):
    __tablename__ = "class_enrollments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    class_id = Column(String(36), ForeignKey("classes.id"), nullable=False)
    student_name = Column(String(255), nullable=False)
    student_identifier = Column(String(100), nullable=False, index=True)
    student_email = Column(String(255))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    enrolled_at = Column(DateTime, default=func.now())

    clase = relationship("Class", back_populates="enrollments")
    user = relationship("User", back_populates="class_enrollments")


class ClassProject(Base):
    __tablename__ = "class_projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    class_id = Column(String(36), ForeignKey("classes.id"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, unique=True)
    display_order = Column(Integer, default=0)
    added_at = Column(DateTime, default=func.now())

    clase = relationship("Class", back_populates="class_projects")
    project = relationship("Project", back_populates="class_project")
