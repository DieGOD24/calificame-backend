from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class InstitutionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255, pattern="^[a-z0-9-]+$")
    logo_url: str | None = None
    primary_color: str = "#4f46e5"


class InstitutionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    logo_url: str | None = None
    primary_color: str | None = None
    settings: dict | None = None


class InstitutionResponse(BaseModel):
    id: str
    name: str
    slug: str
    logo_url: str | None
    primary_color: str
    plan: str
    max_professors: int
    max_students: int
    member_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class InstitutionMemberResponse(BaseModel):
    id: str
    user_id: str
    institution_id: str
    role: str
    joined_at: datetime
    user_email: str = ""
    user_name: str = ""

    model_config = {"from_attributes": True}


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="professor", pattern="^(admin|professor|student)$")


class InstitutionInvitationResponse(BaseModel):
    id: str
    email: str
    role: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
