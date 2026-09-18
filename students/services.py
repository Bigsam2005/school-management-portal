
from accounts.models import User
from students.models import ParentProfile


def get_or_create_parent_account(
    *,
    phone,
    first_name="",
    last_name="",
    email="",
):
    phone = phone.strip()

    user = User.objects.filter(phone=phone).first()

    if user:
        if user.role != User.Role.PARENT:
            raise ValueError(
                "This phone number already belongs to a non-parent account."
            )

        parent_profile, _ = ParentProfile.objects.get_or_create(
            user=user
        )

        return user, parent_profile, None, False

    parent_pin = phone[-6:]

    user = User.objects.create_user(
        phone=phone,
        password=parent_pin,
        first_name=first_name,
        last_name=last_name,
        email=email,
        role=User.Role.PARENT,
        must_change_password=False,
        is_active=True,
    )

    parent_profile = ParentProfile.objects.create(
        user=user,
        account_activated=False,
    )

    return (
        user,
        parent_profile,
        parent_pin,
        True,
    )


def link_parent_to_student(
    *,
    student,
    parent_profile,
    relationship,
    is_primary_contact=True,
):
    from students.models import StudentParent

    link, created = StudentParent.objects.get_or_create(
        student=student,
        parent=parent_profile,
        defaults={
            "relationship": relationship,
            "is_primary_contact": is_primary_contact,
        },
    )

    if not created:
        link.relationship = relationship
        link.is_primary_contact = is_primary_contact
        link.save(
            update_fields=[
                "relationship",
                "is_primary_contact",
            ]
        )

    return link, created


def register_student_with_parent(
    *,
    school,
    current_class,
    student_type,
    surname,
    first_name,
    middle_name,
    gender,
    date_of_birth=None,
    admission_year=None,
    entry_class_name="",
    parent_phone,
    parent_first_name,
    parent_last_name,
    parent_email="",
    relationship="GUARDIAN",
    exact_admission_date=None,
    previous_school="",
):
    from students.models import Student
    from django.utils import timezone

    # Normalise admission year.
    if admission_year in (None, ""):
        if student_type == Student.StudentType.NEW:
            admission_year = timezone.now().year
        else:
            raise ValueError(
                "Admission year is required for an existing student."
            )
    else:
        try:
            admission_year = int(admission_year)
        except (TypeError, ValueError):
            raise ValueError(
                "Please enter a valid admission year."
            )

    student = Student.objects.create(
        school=school,
        current_class=current_class,
        student_type=student_type,
        surname=surname,
        first_name=first_name,
        middle_name=middle_name,
        gender=gender,
        date_of_birth=date_of_birth,
        admission_year=admission_year,
        entry_class_name=entry_class_name,
        exact_admission_date=exact_admission_date,
        previous_school=previous_school,
    )

    user, parent_profile, temporary_password, parent_created = (
        get_or_create_parent_account(
            phone=parent_phone,
            first_name=parent_first_name,
            last_name=parent_last_name,
            email=parent_email,
        )
    )

    link_parent_to_student(
        student=student,
        parent_profile=parent_profile,
        relationship=relationship,
        is_primary_contact=True,
    )

    return {
        "student": student,
        "parent_user": user,
        "parent_profile": parent_profile,
        "temporary_password": temporary_password,
        "parent_created": parent_created,
    }
