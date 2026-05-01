"""Cascade delete integrity tests.

When a project, class, or user is deleted, no orphaned child rows should
remain. These tests guard against future schema changes that drop or
forget cascade rules.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.models.answer_key import AnswerKey
from app.models.clase import Class, ClassEnrollment, ClassProject
from app.models.exam_answer import ExamAnswer
from app.models.project import Project, ProjectStatus
from app.models.question import Question
from app.models.student_exam import StudentExam
from app.models.user import User


class TestProjectCascade:
    def test_deleting_project_removes_all_children(
        self,
        client: TestClient,
        db,
        test_user: User,
        auth_headers: dict,
        temp_storage,
    ) -> None:
        """Project deletion should cascade to questions, exams, answers, answer_keys."""
        # Create project with full child graph
        project = Project(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="Cascade Test",
            status=ProjectStatus.GRADING.value,
            config={},
        )
        db.add(project)
        db.commit()

        # Add a question
        question = Question(
            id=str(uuid4()),
            project_id=project.id,
            question_number=1,
            question_text="Q1",
            correct_answer="A",
            points=10.0,
            is_confirmed=True,
        )
        # Add an answer key
        answer_key = AnswerKey(
            id=str(uuid4()),
            project_id=project.id,
            original_filename="ak.pdf",
            file_path=f"answer_keys/{project.id}/x.pdf",
            file_type="pdf",
            is_processed=True,
        )
        # Add a student exam
        exam = StudentExam(
            id=str(uuid4()),
            project_id=project.id,
            student_name="Alice",
            student_identifier="A-1",
            file_path=f"projects/{project.id}/e.pdf",
            file_type="pdf",
            status="graded",
        )
        db.add_all([question, answer_key, exam])
        db.commit()

        # Add an exam answer (FK to both exam and question)
        ans = ExamAnswer(
            id=str(uuid4()),
            student_exam_id=exam.id,
            question_id=question.id,
            extracted_answer="A",
            is_correct=True,
            score=10.0,
            max_score=10.0,
        )
        db.add(ans)
        db.commit()

        project_id = project.id
        question_id = question.id
        ak_id = answer_key.id
        exam_id = exam.id
        ans_id = ans.id

        # Delete via API
        r = client.delete(f"/api/v1/projects/{project_id}", headers=auth_headers)
        assert r.status_code == 204

        # No orphans should remain
        db.expire_all()
        assert db.query(Project).filter(Project.id == project_id).first() is None
        assert db.query(Question).filter(Question.id == question_id).first() is None
        assert db.query(AnswerKey).filter(AnswerKey.id == ak_id).first() is None
        assert db.query(StudentExam).filter(StudentExam.id == exam_id).first() is None
        assert db.query(ExamAnswer).filter(ExamAnswer.id == ans_id).first() is None


class TestClassCascade:
    def test_deleting_class_removes_enrollments_and_class_projects(
        self,
        client: TestClient,
        db,
        test_user: User,
        auth_headers: dict,
    ) -> None:
        """Class deletion should cascade to enrollments and ClassProject links."""
        clase = Class(
            id=str(uuid4()),
            professor_id=test_user.id,
            name="Cascade Class",
            subject="x",
            semester="2026-1",
            is_active=True,
        )
        project = Project(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="P",
            status=ProjectStatus.DRAFT.value,
            config={},
        )
        db.add_all([clase, project])
        db.commit()

        enrollment = ClassEnrollment(
            id=str(uuid4()),
            class_id=clase.id,
            student_name="X",
            student_identifier="X-1",
        )
        cp = ClassProject(
            id=str(uuid4()),
            class_id=clase.id,
            project_id=project.id,
            display_order=0,
        )
        db.add_all([enrollment, cp])
        db.commit()

        class_id = clase.id
        enrollment_id = enrollment.id
        cp_id = cp.id

        r = client.delete(f"/api/v1/classes/{class_id}", headers=auth_headers)
        assert r.status_code == 204

        db.expire_all()
        assert db.query(Class).filter(Class.id == class_id).first() is None
        assert db.query(ClassEnrollment).filter(ClassEnrollment.id == enrollment_id).first() is None
        assert db.query(ClassProject).filter(ClassProject.id == cp_id).first() is None
        # The project itself should NOT be deleted (only the link)
        assert db.query(Project).filter(Project.id == project.id).first() is not None


class TestStudentExamCascade:
    def test_deleting_student_exam_removes_answers(
        self,
        client: TestClient,
        db,
        test_user: User,
        auth_headers: dict,
        temp_storage,
    ) -> None:
        """Deleting a single StudentExam should remove its ExamAnswer rows."""
        project = Project(
            id=str(uuid4()),
            owner_id=test_user.id,
            name="P",
            status=ProjectStatus.GRADING.value,
            config={},
        )
        db.add(project)
        db.commit()

        question = Question(
            id=str(uuid4()),
            project_id=project.id,
            question_number=1,
            question_text="Q",
            correct_answer="A",
            points=10.0,
            is_confirmed=True,
        )
        exam = StudentExam(
            id=str(uuid4()),
            project_id=project.id,
            file_path=f"projects/{project.id}/e.pdf",
            file_type="pdf",
            status="graded",
        )
        db.add_all([question, exam])
        db.commit()

        ans = ExamAnswer(
            id=str(uuid4()),
            student_exam_id=exam.id,
            question_id=question.id,
            extracted_answer="A",
            is_correct=True,
            score=10.0,
            max_score=10.0,
        )
        db.add(ans)
        db.commit()

        ans_id = ans.id

        r = client.delete(
            f"/api/v1/projects/{project.id}/exams/{exam.id}",
            headers=auth_headers,
        )
        assert r.status_code == 204

        db.expire_all()
        assert db.query(ExamAnswer).filter(ExamAnswer.id == ans_id).first() is None
        # Question survives — only the answer is removed
        assert db.query(Question).filter(Question.id == question.id).first() is not None
