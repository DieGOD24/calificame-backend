from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


def _normalize_email(value: str) -> str:
    """Trim whitespace and lowercase the email so comparisons are consistent."""
    return value.strip().lower()


def _normalize_name(value: str) -> str:
    """Collapse internal whitespace runs and trim ends."""
    return " ".join(value.split())


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)
    role: str = "professor"

    @field_validator("email", mode="before")
    @classmethod
    def _strip_email(cls, v):
        return _normalize_email(v) if isinstance(v, str) else v

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v):
        v = _normalize_name(v)
        if not v:
            raise ValueError("full_name cannot be empty after trimming")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _strip_email(cls, v):
        return _normalize_email(v) if isinstance(v, str) else v


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _strip_email(cls, v):
        return _normalize_email(v) if isinstance(v, str) else v

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v):
        if v is None:
            return v
        v = _normalize_name(v)
        if not v:
            raise ValueError("full_name cannot be empty after trimming")
        return v


class UserRoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(developer|admin|institution|professor|student)$")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: str
