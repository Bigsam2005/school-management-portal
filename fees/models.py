from django.db import models
from schools.models import School, SchoolClass, AcademicSession, AcademicTerm


class FeeStructure(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="fee_structures"
    )

    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name="fee_structures"
    )

    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name="fee_structures"
    )

    term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.CASCADE,
        related_name="fee_structures"
    )

    title = models.CharField(
        max_length=100,
        default="School Fees"
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "school",
                    "school_class",
                    "session",
                    "term",
                    "title",
                ],
                name="unique_fee_structure"
            )
        ]

    def __str__(self):
        return (
            f"{self.school.code} - "
            f"{self.school_class} - "
            f"{self.session.name} - "
            f"{self.term.get_name_display()} - "
            f"{self.title}"
        )


class StudentPayment(models.Model):
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="payments"
    )

    fee_structure = models.ForeignKey(
        FeeStructure,
        on_delete=models.PROTECT,
        related_name="payments"
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    reference = models.CharField(
        max_length=100,
        unique=True
    )

    paid_at = models.DateTimeField(
        auto_now_add=True
    )

    is_confirmed = models.BooleanField(
        default=True
    )

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.PROTECT,
        related_name="student_payments",
        null=True,
        blank=True,
    )

    paystack_subaccount_code = models.CharField(
        max_length=100,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.student} - {self.reference} - {self.amount}"
