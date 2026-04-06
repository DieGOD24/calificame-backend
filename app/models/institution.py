from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.database import Base


class Institution(Base):
    __tablename__ = "institutions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    logo_url = Column(String(512))
    primary_color = Column(String(7), default="#4f46e5")
    plan = Column(String(50), default="free")
    max_professors = Column(Integer, default=10)
    max_students = Column(Integer, default=100)
    settings = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    members = relationship("InstitutionMember", back_populates="institution", cascade="all, delete-orphan")
    invitations = relationship("InstitutionInvitation", back_populates="institution", cascade="all, delete-orphan")


class InstitutionMember(Base):
    __tablename__ = "institution_members"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    institution_id = Column(String(36), ForeignKey("institutions.id"), nullable=False)
    role = Column(String(50), default="professor")  # owner, admin, professor, student
    joined_at = Column(DateTime, default=func.now())

    user = relationship("User", back_populates="institution_memberships")
    institution = relationship("Institution", back_populates="members")


class InstitutionInvitation(Base):
    __tablename__ = "institution_invitations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    institution_id = Column(String(36), ForeignKey("institutions.id"), nullable=False)
    email = Column(String(255), nullable=False)
    role = Column(String(50), default="professor")
    token = Column(String(255), unique=True, nullable=False)
    status = Column(String(50), default="pending")  # pending, accepted, expired
    invited_by = Column(String(36), ForeignKey("users.id"))
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=False)

    institution = relationship("Institution", back_populates="invitations")
