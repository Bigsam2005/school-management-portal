import uuid

from django.db import models
from django.utils.text import slugify


class School(models.Model):
    class SubscriptionPlan(models.TextChoices):
        LIFETIME_FREE = "LIFETIME_FREE", "Lifetime Free"
        BASIC = "BASIC", "Basic"
        PREMIUM = "PREMIUM", "Premium"
        ENTERPRISE = "ENTERPRISE", "Enterprise"

    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        EXPIRED = "EXPIRED", "Expired"
        SUSPENDED = "SUSPENDED", "Suspended"

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=20, unique=True, editable=False)
    slug = models.SlugField(max_length=220, unique=True, editable=False)

    motto = models.CharField(max_length=255, blank=True)
    logo = models.ImageField(
        upload_to="school_logos/",
        blank=True,
        null=True
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    primary_color = models.CharField(max_length=7, default="#0D6EFD")
    secondary_color = models.CharField(max_length=7, default="#FFFFFF")

    subscription_plan = models.CharField(
        max_length=20,
        choices=SubscriptionPlan.choices,
        default=SubscriptionPlan.BASIC
    )
    subscription_status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE
    )
    subscription_expiry_date = models.DateField(blank=True, null=True)

    is_active = models.BooleanField(default=True)

    school_admin_can_manage_fees = models.BooleanField(
        default=True,
        help_text="Allow this school's admin to create and manage fee structures.",
    )

    # Paystack settlement configuration for this specific school.
    paystack_subaccount_code = models.CharField(
        max_length=100,
        blank=True,
    )
    settlement_bank_code = models.CharField(
        max_length=20,
        blank=True,
    )
    settlement_bank_name = models.CharField(
        max_length=120,
        blank=True,
    )
    settlement_account_number = models.CharField(
        max_length=20,
        blank=True,
    )
    settlement_account_name = models.CharField(
        max_length=200,
        blank=True,
    )
    paystack_subaccount_active = models.BooleanField(
        default=False,
    )

    portal_service_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=400.00,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def generate_school_code(self):
        ignored_words = {"OF", "THE", "AND"}

        words = [
            word
            for word in self.name.upper().split()
            if word not in ignored_words
        ]

        if len(words) > 1:
            base_code = "".join(word[0] for word in words)
        else:
            base_code = words[0][:4] if words else "SCH"

        code = base_code
        number = 1

        while School.objects.filter(code=code).exclude(pk=self.pk).exists():
            code = f"{base_code}{number}"
            number += 1

        return code

    def generate_unique_slug(self):
        base_slug = slugify(self.name) or "school"
        school_slug = base_slug
        number = 1

        while School.objects.filter(
            slug=school_slug
        ).exclude(pk=self.pk).exists():
            school_slug = f"{base_slug}-{number}"
            number += 1

        return school_slug

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_school_code()

        if not self.slug:
            self.slug = self.generate_unique_slug()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class SchoolSection(models.Model):
    class AcademicLevel(models.TextChoices):
        PRIMARY = "PRIMARY", "Primary"
        SECONDARY = "SECONDARY", "Secondary"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="sections"
    )
    name = models.CharField(max_length=100)

    academic_level = models.CharField(
        max_length=20,
        choices=AcademicLevel.choices,
        default=AcademicLevel.PRIMARY,
    )

    order = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"],
                name="unique_section_name_per_school"
            )
        ]

    def __str__(self):
        return f"{self.school.code} - {self.name}"


class SchoolClass(models.Model):
    section = models.ForeignKey(
        SchoolSection,
        on_delete=models.CASCADE,
        related_name="classes"
    )
    name = models.CharField(max_length=50)
    arm = models.CharField(max_length=10, blank=True)
    order = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["section__order", "order", "name", "arm"]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "name", "arm"],
                name="unique_class_and_arm_per_section"
            )
        ]

    @property
    def school(self):
        return self.section.school

    @property
    def display_name(self):
        if self.arm:
            return f"{self.name} {self.arm}"

        return self.name

    def __str__(self):
        return (
            f"{self.section.school.code} - "
            f"{self.section.name} - {self.display_name}"
        )


class Subject(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subjects",
    )

    name = models.CharField(max_length=100)

    code = models.CharField(
        max_length=20,
        blank=True,
    )

    classes = models.ManyToManyField(
        SchoolClass,
        related_name="subjects",
        blank=True,
    )

    senior_science = models.BooleanField(default=False)
    senior_commercial = models.BooleanField(default=False)
    senior_arts = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"],
                name="unique_subject_name_per_school",
            )
        ]

    def __str__(self):
        return f"{self.school.code} - {self.name}"


class AdmissionNumberCounter(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="admission_counters"
    )

    admission_year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["school", "admission_year"],
                name="unique_admission_counter_per_school_year"
            )
        ]

    def __str__(self):
        return (
            f"{self.school.code} - "
            f"{self.admission_year} - "
            f"{self.last_number}"
        )


class AcademicSession(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="academic_sessions"
    )

    name = models.CharField(
        max_length=20
    )

    is_current = models.BooleanField(
        default=False
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ["-name"]

        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"],
                name="unique_academic_session_per_school"
            )
        ]

    def __str__(self):
        return f"{self.school.code} - {self.name}"


class AcademicTerm(models.Model):
    class TermName(models.TextChoices):
        FIRST = "FIRST", "First Term"
        SECOND = "SECOND", "Second Term"
        THIRD = "THIRD", "Third Term"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="academic_terms"
    )

    session = models.ForeignKey(
        AcademicSession,
        on_delete=models.CASCADE,
        related_name="terms"
    )

    name = models.CharField(
        max_length=10,
        choices=TermName.choices
    )

    is_current = models.BooleanField(
        default=False
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
                fields=["session", "name"],
                name="unique_term_per_session"
            )
        ]

    def __str__(self):
        return (
            f"{self.school.code} - "
            f"{self.session.name} - "
            f"{self.get_name_display()}"
        )


class TeacherClassAssignment(models.Model):
    teacher_membership = models.ForeignKey(
        "accounts.SchoolMembership",
        on_delete=models.CASCADE,
        related_name="class_assignments"
    )

    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.CASCADE,
        related_name="teacher_assignments"
    )

    is_active = models.BooleanField(
        default=True
    )

    assigned_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["teacher_membership", "school_class"],
                name="unique_teacher_class_assignment"
            )
        ]

    def __str__(self):
        return (
            f"{self.teacher_membership.user.phone} - "
            f"{self.school_class.display_name}"
        )
