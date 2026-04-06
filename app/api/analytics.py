from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.exam_answer import ExamAnswer
from app.models.institution import Institution, InstitutionMember
from app.models.project import Project
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.models.user import User, UserRole
from app.schemas.analytics import (
    InstitutionAnalytics,
    ProjectAnalytics,
    QuestionDifficulty,
    ScoreDistribution,
    StudentProgress,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/projects/{project_id}", response_model=ProjectAnalytics)
def get_project_analytics(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ProjectAnalytics:
    """Get analytics for a project. Accessible by owner, Developer, or Admin."""
    # Use get_user_project logic inline for auth check
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if current_user.role not in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        if project.owner_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Get graded exams
    exams = (
        db.query(StudentExam)
        .filter(
            StudentExam.project_id == project_id,
            StudentExam.status == "graded",
        )
        .all()
    )

    total_exams = db.query(StudentExam).filter(StudentExam.project_id == project_id).count()
    graded_count = len(exams)

    # Calculate score statistics
    percentages = [e.grade_percentage for e in exams if e.grade_percentage is not None]

    average_percentage = None
    median_score = None
    highest_score = None
    lowest_score = None
    pass_rate = None
    average_score = None

    if percentages:
        average_percentage = sum(percentages) / len(percentages)

        scores = [e.total_score for e in exams if e.total_score is not None]
        if scores:
            average_score = sum(scores) / len(scores)
            highest_score = max(scores)
            lowest_score = min(scores)

        # Median calculation
        sorted_scores = sorted(percentages)
        mid = len(sorted_scores) // 2
        median_score = (
            sorted_scores[mid] if len(sorted_scores) % 2 else (sorted_scores[mid - 1] + sorted_scores[mid]) / 2
        )

        # Pass rate (>= 60%)
        passing = sum(1 for p in percentages if p >= 60.0)
        pass_rate = (passing / len(percentages)) * 100

    # Score distribution in ranges
    distribution_ranges = [
        ("0-10", 0, 10),
        ("10-20", 10, 20),
        ("20-30", 20, 30),
        ("30-40", 30, 40),
        ("40-50", 40, 50),
        ("50-60", 50, 60),
        ("60-70", 60, 70),
        ("70-80", 70, 80),
        ("80-90", 80, 90),
        ("90-100", 90, 100),
    ]
    score_distribution = []
    for label, low, high in distribution_ranges:
        if high == 100:
            count = sum(1 for p in percentages if low <= p <= high)
        else:
            count = sum(1 for p in percentages if low <= p < high)
        score_distribution.append(ScoreDistribution(range_label=label, count=count))

    # Question difficulty
    questions = db.query(Question).filter(Question.project_id == project_id).order_by(Question.question_number).all()

    question_difficulty = []
    for q in questions:
        answers = db.query(ExamAnswer).filter(ExamAnswer.question_id == q.id).all()
        total_count = len(answers)
        correct_count = sum(1 for a in answers if a.is_correct)
        success_rate = (correct_count / total_count * 100) if total_count > 0 else 0.0
        question_difficulty.append(
            QuestionDifficulty(
                question_number=q.question_number,
                question_text=q.question_text,
                correct_count=correct_count,
                total_count=total_count,
                success_rate=round(success_rate, 2),
            )
        )

    logger.info(f"Generated analytics for project {project_id} ({graded_count} graded exams)")

    return ProjectAnalytics(
        project_id=project.id,
        project_name=project.name,
        total_exams=total_exams,
        graded_count=graded_count,
        average_score=round(average_score, 2) if average_score is not None else None,
        median_score=round(median_score, 2) if median_score is not None else None,
        highest_score=round(highest_score, 2) if highest_score is not None else None,
        lowest_score=round(lowest_score, 2) if lowest_score is not None else None,
        average_percentage=round(average_percentage, 2) if average_percentage is not None else None,
        pass_rate=round(pass_rate, 2) if pass_rate is not None else None,
        score_distribution=score_distribution,
        question_difficulty=question_difficulty,
    )


@router.get("/students/{student_identifier}", response_model=list[StudentProgress])
def get_student_progress(
    student_identifier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[StudentProgress]:
    """Get student progress across all projects they appear in."""
    query = (
        db.query(StudentExam)
        .join(Project, StudentExam.project_id == Project.id)
        .filter(StudentExam.student_identifier == student_identifier)
    )

    # Filter by current user's projects unless Developer/Admin
    if current_user.role not in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        query = query.filter(Project.owner_id == current_user.id)

    exams = query.all()

    if not exams:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No records found for this student")

    result = []
    for exam in exams:
        result.append(
            StudentProgress(
                student_identifier=exam.student_identifier or "",
                student_name=exam.student_name,
                project_name=exam.project.name if exam.project else "",
                score=exam.total_score,
                max_score=exam.max_score,
                percentage=exam.grade_percentage,
                graded_at=exam.graded_at.isoformat() if exam.graded_at else None,
            )
        )

    logger.info(f"Retrieved progress for student {student_identifier} ({len(result)} records)")
    return result


@router.get("/institutions/{institution_id}", response_model=InstitutionAnalytics)
def get_institution_analytics(
    institution_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> InstitutionAnalytics:
    """Get institution analytics. Only institution members, Developer, or Admin."""
    institution = db.query(Institution).filter(Institution.id == institution_id).first()
    if institution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Institution not found")

    # Authorization: must be a member, Developer, or Admin
    if current_user.role not in (UserRole.DEVELOPER.value, UserRole.ADMIN.value):
        is_member = (
            db.query(InstitutionMember)
            .filter(
                InstitutionMember.institution_id == institution_id,
                InstitutionMember.user_id == current_user.id,
            )
            .first()
        )
        if not is_member:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Count members by role
    members = db.query(InstitutionMember).filter(InstitutionMember.institution_id == institution_id).all()
    total_professors = sum(1 for m in members if m.role in ("professor", "owner", "admin"))
    total_students = sum(1 for m in members if m.role == "student")

    # Get all member user IDs to find their projects
    member_user_ids = [m.user_id for m in members]

    # Count projects owned by members
    total_projects = (db.query(Project).filter(Project.owner_id.in_(member_user_ids)).count()) if member_user_ids else 0

    # Count graded exams across member projects
    graded_exams_query = (
        db.query(StudentExam)
        .join(Project, StudentExam.project_id == Project.id)
        .filter(
            Project.owner_id.in_(member_user_ids),
            StudentExam.status == "graded",
        )
    )
    graded_exams = graded_exams_query.all() if member_user_ids else []
    total_exams_graded = len(graded_exams)

    # Average score percentage
    percentages = [e.grade_percentage for e in graded_exams if e.grade_percentage is not None]
    average_score_percentage = round(sum(percentages) / len(percentages), 2) if percentages else None

    logger.info(f"Generated analytics for institution {institution_id}")

    return InstitutionAnalytics(
        institution_id=institution.id,
        institution_name=institution.name,
        total_professors=total_professors,
        total_students=total_students,
        total_projects=total_projects,
        total_exams_graded=total_exams_graded,
        average_score_percentage=average_score_percentage,
    )
