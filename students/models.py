from django.db import models, transaction

# Create your models here.
import uuid

from django.db import models, transaction
from django.conf import settings

from schools.models import AdmissionNumberCounter, School, SchoolClass


class Student(models.Model):
    class Department(models.TextChoices):
        SCIENCE = "SCIENCE", "Science"
        COMMERCIAL = "COMMERCIAL", "Commercial"
        ARTS = "ARTS", "Arts"

    class StudentType(models.TextChoices):
        NEW = "NEW", "New Student"
        EXISTING = "EXISTING", "Existing Student"

    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        GRADUATED = "GRADUATED", "Graduated"
        TRANSFERRED = "TRANSFERRED", "Transferred"
        SUSPENDED = "SUSPENDED", "Suspended"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True
    )

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="students"
    )

    department = models.CharField(
        max_length=20,
        choices=Department.choices,
        blank=True,
        default="",
    )

    current_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        related_name="students"
    )

    student_type = models.CharField(
        max_length=10,
        choices=StudentType.choices
    )

    admission_number = models.CharField(
        max_length=40,
        unique=True,
        editable=False
    )

    surname = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)

    gender = models.CharField(
        max_length=10,
        choices=Gender.choices
    )

    date_of_birth = models.DateField(blank=True, null=True)

    passport = models.ImageField(
        upload_to="student_passports/",
        blank=True,
        null=True
    )

    admission_year = models.PositiveIntegerField()
    exact_admission_date = models.DateField(blank=True, null=True)

    entry_class_name = models.CharField(
        max_length=100,
        blank=True
    )

    previous_school = models.CharField(
        max_length=200,
        blank=True
    )

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.ACTIVE
    )

    portal_registered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def full_name(self):
        names = [
            self.surname,
            self.first_name,
            self.middle_name
        ]
        return " ".join(name for name in names if name)

    def generate_admission_number(self):
        with transaction.atomic():
            counter, created = AdmissionNumberCounter.objects.select_for_update().get_or_create(
                school=self.school,
                admission_year=self.admission_year,
                defaults={"last_number": 0}
            )

            counter.last_number += 1
            counter.save(update_fields=["last_number"])

            return (
                f"{self.school.code}-"
                f"{self.admission_year}-"
                f"{counter.last_number:06d}"
            )

    def save(self, *args, **kwargs):
        if not self.admission_number:
            self.admission_number = self.generate_admission_number()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.admission_number} - {self.full_name}"



class ParentProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="parent_profile"
    )

    address = models.TextField(blank=True)
    account_activated = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        full_name = self.user.get_full_name().strip()
        return full_name or self.user.phone


class StudentParent(models.Model):
    class Relationship(models.TextChoices):
        FATHER = "FATHER", "Father"
        MOTHER = "MOTHER", "Mother"
        GUARDIAN = "GUARDIAN", "Guardian"
        GRANDPARENT = "GRANDPARENT", "Grandparent"
        OTHER = "OTHER", "Other"

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="parent_links"
    )

    parent = models.ForeignKey(
        ParentProfile,
        on_delete=models.CASCADE,
        related_name="student_links"
    )

    relationship = models.CharField(
        max_length=20,
        choices=Relationship.choices
    )

    is_primary_contact = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "parent"],
                name="unique_parent_per_student"
            )
        ]

    def __str__(self):
        return (
            f"{self.parent} - "
            f"{self.student.full_name} "
            f"({self.get_relationship_display()})"
        )
