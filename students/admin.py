from django.contrib import admin

from .forms import StudentAdminForm
from .models import ParentProfile, Student, StudentParent
from .services import (
    get_or_create_parent_account,
    link_parent_to_student,
)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    form = StudentAdminForm

    class Media:
        js = ("students/student_admin.js",)

    list_display = (
        "admission_number",
        "full_name",
        "school",
        "current_class",
        "student_type",
        "status",
    )

    search_fields = (
        "admission_number",
        "surname",
        "first_name",
        "middle_name",
    )

    list_filter = (
        "school",
        "current_class",
        "student_type",
        "status",
        "gender",
    )

    readonly_fields = (
        "uuid",
        "admission_number",
        "portal_registered_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Student Type",
            {
                "fields": (
                    "student_type",
                )
            },
        ),
        (
            "Student Information",
            {
                "fields": (
                    "surname",
                    "first_name",
                    "middle_name",
                    "gender",
                    "date_of_birth",
                    "passport",
                )
            },
        ),
        (
            "School Information",
            {
                "fields": (
                    "school",
                    "current_class",
                )
            },
        ),
        (
            "Admission History",
            {
                "fields": (
                    "admission_year",
                    "exact_admission_date",
                    "entry_class_name",
                    "previous_school",
                )
            },
        ),
        (
            "Parent / Guardian",
            {
                "fields": (
                    "parent_first_name",
                    "parent_last_name",
                    "parent_phone",
                    "parent_email",
                    "relationship",
                )
            },
        ),
        (
            "Student Status",
            {
                "fields": (
                    "status",
                    "uuid",
                    "admission_number",
                    "portal_registered_at",
                    "updated_at",
                )
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        if change:
            return

        user, parent_profile, temporary_password, parent_created = (
            get_or_create_parent_account(
                phone=form.cleaned_data["parent_phone"],
                first_name=form.cleaned_data["parent_first_name"],
                last_name=form.cleaned_data["parent_last_name"],
                email=form.cleaned_data["parent_email"],
            )
        )

        link_parent_to_student(
            student=obj,
            parent_profile=parent_profile,
            relationship=form.cleaned_data["relationship"],
            is_primary_contact=True,
        )

        if parent_created:
            self.message_user(
                request,
                (
                    f"Student registered successfully. "
                    f"Admission Number: {obj.admission_number}. "
                    f"Parent temporary password: {temporary_password}"
                ),
            )


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "account_activated",
        "phone_verified",
        "email_verified",
    )

    search_fields = (
        "user__phone",
        "user__first_name",
        "user__last_name",
    )


@admin.register(StudentParent)
class StudentParentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "parent",
        "relationship",
        "is_primary_contact",
    )

    list_filter = (
        "relationship",
        "is_primary_contact",
    )
