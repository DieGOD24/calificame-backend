from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_role
from app.models.user import User, UserRole
from app.rate_limit import limiter
from app.schemas.user import Token, UserCreate, UserLogin, UserResponse, UserRoleUpdate
from app.services.auth import authenticate_user, create_access_token, hash_password, verify_password
from app.services.validators import validate_password

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register(request: Request, user_data: UserCreate, db: Session = Depends(get_db)) -> User:
    """Register a new user."""
    existing = db.query(User).filter(User.email == user_data.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    valid, msg = validate_password(user_data.password)
    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)

    user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        role=user_data.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("New user registered: {} (role: {})", user.email, user.role)
    return user


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
def login(request: Request, login_data: UserLogin, db: Session = Depends(get_db)) -> dict:
    """Login and return a JWT token."""
    user = authenticate_user(db, login_data.email, login_data.password)
    if user is None:
        logger.warning("Failed login attempt for: {}", login_data.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user.id})
    logger.info("User logged in: {}", user.email)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_active_user)) -> User:
    """Get the current user's profile."""
    return current_user


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


@router.patch("/me", response_model=UserResponse)
def update_profile(
    data: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> User:
    """Update current user's profile."""
    if data.email and data.email != current_user.email:
        existing = db.query(User).filter(User.email == data.email).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )
        current_user.email = data.email

    if data.full_name:
        current_user.full_name = data.full_name

    db.commit()
    db.refresh(current_user)
    logger.info("User profile updated: {}", current_user.email)
    return current_user


@router.post("/me/change-password")
def change_password(
    data: PasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Change current user's password."""
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contrasena actual incorrecta",
        )
    valid, msg = validate_password(data.new_password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=msg,
        )

    current_user.hashed_password = hash_password(data.new_password)
    db.commit()
    logger.info("Password changed for user: {}", current_user.email)
    return {"message": "Contrasena actualizada exitosamente"}


# Admin endpoints
@router.get("/users", response_model=list[UserResponse])
def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.DEVELOPER, UserRole.ADMIN)),
) -> list[User]:
    """List users with pagination (Developer/Admin only)."""
    offset = (page - 1) * per_page
    return db.query(User).order_by(User.created_at.desc()).offset(offset).limit(per_page).all()


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(
    user_id: str,
    data: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.DEVELOPER, UserRole.ADMIN)),
) -> User:
    """Update a user's role (Developer/Admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if data.role == UserRole.DEVELOPER.value and current_user.role != UserRole.DEVELOPER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only developers can assign the developer role",
        )

    user.role = data.role
    db.commit()
    db.refresh(user)
    logger.info("Role updated for {}: {} (by {})", user.email, data.role, current_user.email)
    return user
