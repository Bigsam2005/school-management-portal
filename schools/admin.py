from django.contrib import admin

from .models import (
    AdmissionNumberCounter,
    School,
    SchoolClass,
    SchoolSection,
)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "subscription_plan",
        "subscription_status",
        "school_admin_can_manage_fees",
        "is_active",
    )
    search_fields = ("name", "code", "email", "phone")
    list_filter = (
        "subscription_plan",
        "subscription_status",
        "school_admin_can_manage_fees",
        "is_active",
    )
    readonly_fields = ("uuid", "code", "slug", "created_at", "updated_at")


@admin.register(SchoolSection)
class SchoolSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "order", "is_active")
    list_filter = ("school", "is_active")


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ("name", "arm", "section", "school", "is_active")
    list_filter = ("section__school", "section", "is_active")


@admin.register(AdmissionNumberCounter)
class AdmissionNumberCounterAdmin(admin.ModelAdmin):
    list_display = ("school", "admission_year", "last_number")
    readonly_fields = ("last_number",)


from .models import AcademicSession, AcademicTerm


@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "school",
        "is_current",
        "is_active",
    )

    list_filter = (
        "school",
        "is_current",
        "is_active",
    )

    search_fields = (
        "name",
        "school__name",
        "school__code",
    )


@admin.register(AcademicTerm)
class AcademicTermAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "session",
        "school",
        "is_current",
        "is_active",
    )

    list_filter = (
        "school",
        "session",
        "name",
        "is_current",
        "is_active",
    )
