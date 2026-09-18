from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra_fields):
        if not phone:
            raise ValueError("A phone number is required.")

        phone = phone.strip()
        user = self.model(phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", User.Role.PLATFORM_OWNER)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")

        return self.create_user(phone, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        PLATFORM_OWNER = "PLATFORM_OWNER", "Platform Owner"
        SCHOOL_ADMIN = "SCHOOL_ADMIN", "School Admin"
        TEACHER = "TEACHER", "Teacher"
        PARENT = "PARENT", "Parent"

    username = None

    phone = models.CharField(
        max_length=20,
        unique=True
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices
    )

    must_change_password = models.BooleanField(default=True)

    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.phone


class SchoolMembership(models.Model):
    class StaffRole(models.TextChoices):
        SCHOOL_ADMIN = "SCHOOL_ADMIN", "School Admin"
        TEACHER = "TEACHER", "Teacher"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="school_memberships"
    )

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="staff_memberships"
    )

    staff_role = models.CharField(
        max_length=20,
        choices=StaffRole.choices
    )

    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "school"],
                name="unique_user_membership_per_school"
            )
        ]

    def __str__(self):
        return (
            f"{self.user.phone} - "
            f"{self.school.name} - "
            f"{self.get_staff_role_display()}"
        )
