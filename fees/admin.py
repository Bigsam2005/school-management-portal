from django.contrib import admin
from .models import FeeStructure


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "school",
        "school_class",
        "session",
        "term",
        "amount",
        "is_active",
    )

    list_filter = (
        "school",
        "session",
        "term",
        "school_class",
        "is_active",
    )

    search_fields = (
        "title",
        "school__name",
        "school__code",
        "school_class__name",
    )


from .models import StudentPayment


@admin.register(StudentPayment)
class StudentPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "fee_structure",
        "amount",
        "reference",
        "paid_at",
        "is_confirmed",
    )

    list_filter = (
        "is_confirmed",
        "paid_at",
        "fee_structure__school",
        "fee_structure__session",
        "fee_structure__term",
    )

    search_fields = (
        "student__admission_number",
        "student__surname",
        "student__first_name",
        "reference",
    )

    readonly_fields = (
        "paid_at",
        "created_at",
    )
