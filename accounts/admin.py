from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import SchoolMembership, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User

    list_display = (
        "phone",
        "first_name",
        "last_name",
        "role",
        "is_active",
        "is_staff",
    )

    list_filter = (
        "role",
        "is_active",
        "is_staff",
        "is_superuser",
    )

    search_fields = (
        "phone",
        "first_name",
        "last_name",
        "email",
    )

    ordering = ("phone",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        (
            "Personal information",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "email",
                )
            },
        ),
        (
            "Portal access",
            {
                "fields": (
                    "role",
                    "must_change_password",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Important dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone",
                    "first_name",
                    "last_name",
                    "role",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                ),
            },
        ),
    )


@admin.register(SchoolMembership)
class SchoolMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "school",
        "staff_role",
        "is_active",
        "joined_at",
    )

    list_filter = (
        "school",
        "staff_role",
        "is_active",
    )

    search_fields = (
        "user__phone",
        "user__first_name",
        "user__last_name",
        "school__name",
    )

    readonly_fields = ("joined_at",)
