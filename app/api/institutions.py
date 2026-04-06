from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_role
from app.models.institution import Institution, InstitutionInvitation, InstitutionMember
from app.models.user import User, UserRole
from app.schemas.institution import (
    InstitutionCreate,
    InstitutionInvitationResponse,
    InstitutionMemberResponse,
    InstitutionResponse,
    InstitutionUpdate,
    InviteMemberRequest,
)

router = APIRouter(prefix="/institutions", tags=["Institutions"])


def _institution_to_response(institution: Institution) -> InstitutionResponse:
    """Convert an Institution model to a response schema."""
    return InstitutionResponse(
        id=institution.id,
        name=institution.name,
        slug=institution.slug,
        logo_url=institution.logo_url,
        primary_color=institution.primary_color,
        plan=institution.plan,
        max_professors=institution.max_professors,
        max_students=institution.max_students,
        member_count=len(institution.members) if institution.members else 0,
        created_at=institution.created_at,
    )


def _is_institution_admin(db: Session, institution_id: str, user: User) -> bool:
    """Check if the user is an owner or admin member of the institution."""
    if user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        return True
    member = (
        db.query(InstitutionMember)
        .filter(
            InstitutionMember.institution_id == institution_id,
            InstitutionMember.user_id == user.id,
            InstitutionMember.role.in_(["owner", "admin"]),
        )
        .first()
    )
    return member is not None


@router.post("/", response_model=InstitutionResponse, status_code=status.HTTP_201_CREATED)
def create_institution(
    data: InstitutionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.DEVELOPER, UserRole.ADMIN, UserRole.INSTITUTION)),
) -> InstitutionResponse:
    """Create a new institution and set the creator as owner member."""
    # Check slug uniqueness
    existing = db.query(Institution).filter(Institution.slug == data.slug).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An institution with this slug already exists",
        )

    institution = Institution(
        name=data.name,
        slug=data.slug,
        logo_url=data.logo_url,
        primary_color=data.primary_color,
    )
    db.add(institution)
    db.flush()

    # Add creator as owner member
    member = InstitutionMember(
        user_id=current_user.id,
        institution_id=institution.id,
        role="owner",
    )
    db.add(member)
    db.commit()
    db.refresh(institution)

    logger.info(f"User {current_user.id} created institution {institution.id} ({institution.name})")
    return _institution_to_response(institution)


@router.get("/", response_model=list[InstitutionResponse])
def list_institutions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[InstitutionResponse]:
    """List institutions based on user role."""
    if current_user.role in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        query = db.query(Institution)
    elif current_user.role == UserRole.INSTITUTION.value:
        # Institution role sees institutions they own/are member of
        member_institution_ids = (
            db.query(InstitutionMember.institution_id).filter(InstitutionMember.user_id == current_user.id).subquery()
        )
        query = db.query(Institution).filter(Institution.id.in_(member_institution_ids))
    else:
        # Professor/Student see institutions they are members of
        member_institution_ids = (
            db.query(InstitutionMember.institution_id).filter(InstitutionMember.user_id == current_user.id).subquery()
        )
        query = db.query(Institution).filter(Institution.id.in_(member_institution_ids))

    institutions = query.order_by(Institution.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return [_institution_to_response(inst) for inst in institutions]


@router.get("/{institution_id}", response_model=InstitutionResponse)
def get_institution(
    institution_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> InstitutionResponse:
    """Get institution detail with member count."""
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if institution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

    return _institution_to_response(institution)


@router.put("/{institution_id}", response_model=InstitutionResponse)
def update_institution(
    institution_id: str,
    data: InstitutionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> InstitutionResponse:
    """Update an institution. Only owner/admin members or Developer/Admin roles."""
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if institution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

    if not _is_institution_admin(db, institution_id, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(institution, field, value)

    db.commit()
    db.refresh(institution)

    logger.info(f"User {current_user.id} updated institution {institution_id}")
    return _institution_to_response(institution)


@router.delete("/{institution_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_institution(
    institution_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.DEVELOPER, UserRole.ADMIN)),
) -> None:
    """Delete an institution. Only Developer or Admin role."""
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if institution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

    db.delete(institution)
    db.commit()

    logger.info(f"User {current_user.id} deleted institution {institution_id}")


@router.get("/{institution_id}/members", response_model=list[InstitutionMemberResponse])
def list_members(
    institution_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[InstitutionMemberResponse]:
    """List institution members with user email and name."""
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if institution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

    members = db.query(InstitutionMember).filter(InstitutionMember.institution_id == institution_id).all()

    result = []
    for member in members:
        result.append(
            InstitutionMemberResponse(
                id=member.id,
                user_id=member.user_id,
                institution_id=member.institution_id,
                role=member.role,
                joined_at=member.joined_at,
                user_email=member.user.email if member.user else "",
                user_name=member.user.full_name if member.user else "",
            )
        )

    return result


@router.post(
    "/{institution_id}/members/invite",
    response_model=InstitutionInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def invite_member(
    institution_id: str,
    data: InviteMemberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> InstitutionInvitationResponse:
    """Invite a member by email to the institution."""
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if institution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

    if not _is_institution_admin(db, institution_id, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to invite members")

    # Check for existing pending invitation
    existing_invite = (
        db.query(InstitutionInvitation)
        .filter(
            InstitutionInvitation.institution_id == institution_id,
            InstitutionInvitation.email == data.email,
            InstitutionInvitation.status == "pending",
        )
        .first()
    )
    if existing_invite:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending invitation already exists for this email",
        )

    invitation = InstitutionInvitation(
        institution_id=institution_id,
        email=data.email,
        role=data.role,
        token=str(uuid4()),
        status="pending",
        invited_by=current_user.id,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)

    logger.info(f"User {current_user.id} invited {data.email} to institution {institution_id}")
    return InstitutionInvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        created_at=invitation.created_at,
    )


@router.post("/invitations/{token}/accept", response_model=InstitutionMemberResponse)
def accept_invitation(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> InstitutionMemberResponse:
    """Accept an institution invitation."""
    invitation = (
        db.query(InstitutionInvitation)
        .filter(
            InstitutionInvitation.token == token,
            InstitutionInvitation.status == "pending",
        )
        .first()
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found or already used")

    # Check if invitation is expired
    if invitation.expires_at and invitation.expires_at < datetime.now(UTC):
        invitation.status = "expired"
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invitation has expired")

    # Check that the current user's email matches the invitation
    user = db.query(User).filter(User.email == invitation.email).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No account found for this email. Please register first.",
        )

    if user.id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invitation was sent to a different email address",
        )

    # Check if already a member
    existing_member = (
        db.query(InstitutionMember)
        .filter(
            InstitutionMember.institution_id == invitation.institution_id,
            InstitutionMember.user_id == current_user.id,
        )
        .first()
    )
    if existing_member:
        invitation.status = "accepted"
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member of this institution")

    # Create member
    member = InstitutionMember(
        user_id=current_user.id,
        institution_id=invitation.institution_id,
        role=invitation.role,
    )
    db.add(member)

    invitation.status = "accepted"
    db.commit()
    db.refresh(member)

    logger.info(f"User {current_user.id} accepted invitation to institution {invitation.institution_id}")
    return InstitutionMemberResponse(
        id=member.id,
        user_id=member.user_id,
        institution_id=member.institution_id,
        role=member.role,
        joined_at=member.joined_at,
        user_email=current_user.email,
        user_name=current_user.full_name or "",
    )


@router.delete("/{institution_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    institution_id: str,
    member_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    """Remove a member from an institution."""
    member = (
        db.query(InstitutionMember)
        .filter(
            InstitutionMember.id == member_id,
            InstitutionMember.institution_id == institution_id,
        )
        .first()
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if not _is_institution_admin(db, institution_id, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to remove members")

    if member.role == "owner":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove the institution owner")

    db.delete(member)
    db.commit()

    logger.info(f"User {current_user.id} removed member {member_id} from institution {institution_id}")
