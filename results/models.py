from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from schools.models import (
    AcademicSession,
    AcademicTerm,
    School,
    SchoolClass,
    Subject,
)
from students.models import Student


class StudentResult(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="student_results",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="subject_results",
    )

    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name="student_results",
    )

    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name="student_results",
    )

    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name="student_results",
    )

    term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.CASCADE,
        related_name="student_results",
    )

    ca_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    exam_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    total_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        editable=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "student",
                    "subject",
                    "session",
                    "term",
                ],
                name="unique_student_subject_result_per_term",
            )
        ]

    def clean(self):
        academic_level = self.school_class.section.academic_level

        if academic_level == "PRIMARY":
            ca_max = Decimal("40")
            exam_max = Decimal("60")
        else:
            ca_max = Decimal("30")
            exam_max = Decimal("70")

        if self.ca_score < 0 or self.ca_score > ca_max:
            raise ValidationError(
                {"ca_score": f"CA cannot exceed {ca_max}."}
            )

        if self.exam_score < 0 or self.exam_score > exam_max:
            raise ValidationError(
                {"exam_score": f"Exam cannot exceed {exam_max}."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()

        self.total_score = (
            Decimal(self.ca_score)
            + Decimal(self.exam_score)
        )

        super().save(*args, **kwargs)

    @property
    def grade(self):
        score = self.total_score

        if score >= 80:
            return "A"
        if score >= 70:
            return "B"
        if score >= 60:
            return "C"
        if score >= 50:
            return "D"
        if score >= 40:
            return "E"
        return "F"

    def __str__(self):
        return (
            f"{self.student.admission_number} - "
            f"{self.subject.name} - {self.total_score}"
        )


class StudentTermSummary(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="term_summaries",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="term_summaries",
    )

    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name="term_summaries",
    )

    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name="term_summaries",
    )

    term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.CASCADE,
        related_name="term_summaries",
    )

    subject_count = models.PositiveIntegerField(default=0)

    total_score = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    average_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    position = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "student",
                    "session",
                    "term",
                ],
                name="unique_student_term_summary",
            )
        ]

    def __str__(self):
        return (
            f"{self.student.admission_number} - "
            f"{self.session.name} - {self.term}"
        )


class ClassResultPublication(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="result_publications",
    )

    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name="result_publications",
    )

    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name="result_publications",
    )

    term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.CASCADE,
        related_name="result_publications",
    )

    is_published = models.BooleanField(default=False)

    published_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    next_term_resumption_date = models.DateField(
        null=True,
        blank=True,
    )

    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_class_results",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "school_class",
                    "session",
                    "term",
                ],
                name="unique_class_result_publication",
            )
        ]

    def __str__(self):
        status = "Published" if self.is_published else "Draft"

        return (
            f"{self.school_class.display_name} - "
            f"{self.session.name} - {self.term} - {status}"
        )


class StudentAnnualSummary(models.Model):
    PROMOTION_STATUS = [
        ("PENDING", "Pending"),
        ("PROMOTED", "Promoted"),
        ("REPEAT", "Repeat"),
        ("GRADUATED", "Graduated"),
    ]

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="annual_summaries",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="annual_summaries",
    )

    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name="annual_summaries",
    )

    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name="annual_summaries",
    )

    first_term_average = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    second_term_average = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    third_term_average = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    annual_average = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )

    annual_position = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    promotion_status = models.CharField(
        max_length=20,
        choices=PROMOTION_STATUS,
        default="PENDING",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "student",
                    "school_class",
                    "session",
                ],
                name="unique_student_annual_summary",
            )
        ]

    def __str__(self):
        return (
            f"{self.student.admission_number} - "
            f"{self.session.name} - {self.annual_average}"
        )

class ResultAuditLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"

    result = models.ForeignKey(
        StudentResult,
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )

    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="result_audit_logs",
    )

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )

    old_ca_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )

    old_exam_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )

    new_ca_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
    )

    new_exam_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
    )

    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.result.student.full_name} - {self.result.subject.name} - {self.action}"

