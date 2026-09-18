from django.contrib import admin
from django.urls import path
from portal.views import school_admin_edit_teacher, school_admin_deactivate_teacher, school_admin_reactivate_teacher

from portal.views import school_admin_parents
from portal.views import school_admin_change_password
from portal.views import school_admin_messages, parent_messages
from portal.views import school_admin_teachers
from portal.views import school_admin_payment_receipt
from portal.views import school_admin_student_payment_detail
from portal.views import school_admin_class_payments
from portal.views import school_admin_fee_status
from portal.views import school_admin_fees
from portal.views import parent_school_fees, parent_pay_school_fees, parent_payment_review, parent_start_payment, parent_payment_callback, parent_payment_receipt, parent_fee_clearance, teacher_login, teacher_change_password, teacher_dashboard, teacher_logout, teacher_students, teacher_add_student, school_admin_subjects, school_admin_add_subject, school_admin_generate_subjects, school_admin_edit_subject, school_admin_set_student_department, teacher_set_student_department, school_admin_results, school_admin_result_audit, school_admin_correct_student_result, school_admin_class_result_details, teacher_results, teacher_class_results, teacher_student_result_entry, teacher_student_report, school_admin_publish_class_results, parent_student_result, school_admin_update_resumption_date, parent_student_result_history

from portal.views import home, school_portal, parent_dashboard, parent_login, parent_logout, school_admin_dashboard, school_admin_login, school_admin_logout, school_admin_students, school_admin_class_students, school_admin_add_student, school_admin_academic_year, school_admin_promotions, school_admin_set_promotion_decision, school_admin_start_new_session


from portal.views import platform_owner_dashboard

from portal.views import platform_owner_login, platform_owner_logout

from portal.views import platform_owner_manage_school, platform_owner_create_school, platform_owner_verify_bank_account, platform_owner_paystack_banks

from portal.views import school_admin_student_report

from portal.views import school_admin_student_result_history

from portal.views import school_admin_student_historical_report

from portal.views import teacher_student_term_reports

from portal.views import teacher_student_term_report

urlpatterns = [
    path(
        "ceo/school/<int:school_id>/",
        platform_owner_manage_school,
        name="platform_owner_manage_school",
    ),
    path(
        "ceo/school/<int:school_id>/verify-bank-account/",
        platform_owner_verify_bank_account,
        name="platform_owner_verify_bank_account",
    ),
    path(
        "ceo/paystack/banks/",
        platform_owner_paystack_banks,
        name="platform_owner_paystack_banks",
    ),
    path(
        "ceo/school/create/",
        platform_owner_create_school,
        name="platform_owner_create_school",
    ),
    path("ceo/login/", platform_owner_login, name="platform_owner_login"),
    path("ceo/logout/", platform_owner_logout, name="platform_owner_logout"),
    path("ceo/", platform_owner_dashboard, name="platform_owner_dashboard"),
    path(
        "s/<slug:school_slug>/",
        school_portal,
        name="school_portal",
    ),

    path(
        "s/<slug:school_slug>/admin/login/",
        school_admin_login,
        name="school_admin_login_school",
    ),

    path(
        "s/<slug:school_slug>/teacher/login/",
        teacher_login,
        name="teacher_login_school",
    ),

    path(
        "s/<slug:school_slug>/parent/login/",
        parent_login,
        name="parent_login_school",
    ),


    path(
        "school-admin/teachers/<int:membership_id>/edit/",
        school_admin_edit_teacher,
        name="school_admin_edit_teacher",
    ),
    path(
        "school-admin/teachers/<int:membership_id>/deactivate/",
        school_admin_deactivate_teacher,
        name="school_admin_deactivate_teacher",
    ),
    path(
        "school-admin/teachers/<int:membership_id>/reactivate/",
        school_admin_reactivate_teacher,
        name="school_admin_reactivate_teacher",
    ),
    path("", home, name="home"),
    path(
        "school-admin/login/",
        school_admin_login,
        name="school_admin_login",
    ),
    path(
        "school-admin/logout/",
        school_admin_logout,
        name="school_admin_logout",
    ),
    path(
        "school-admin/teachers/",
        school_admin_teachers,
        name="school_admin_teachers",
    ),
    path(
        "school-admin/dashboard/",
        school_admin_dashboard,
        name="school_admin_dashboard",
    ),
    path(
        "school-admin/change-password/",
        school_admin_change_password,
        name="school_admin_change_password",
    ),
    path(
        "school-admin/academic-year/",
        school_admin_academic_year,
        name="school_admin_academic_year",
    ),
    path(
        "school-admin/academic-year/start-new-session/",
        school_admin_start_new_session,
        name="school_admin_start_new_session",
    ),
    path(
        "school-admin/promotions/",
        school_admin_promotions,
        name="school_admin_promotions",
    ),
    path(
        "school-admin/promotions/<int:summary_id>/decision/",
        school_admin_set_promotion_decision,
        name="school_admin_set_promotion_decision",
    ),


    path(
        "school-admin/results/student/<uuid:student_uuid>/report/",
        school_admin_student_report,
        name="school_admin_student_report",
    ),
    path(
        "school-admin/results/student/<uuid:student_uuid>/correct/",
        school_admin_correct_student_result,
        name="school_admin_correct_student_result",
    ),
    path(
        "school-admin/results/audit/",
        school_admin_result_audit,
        name="school_admin_result_audit",
    ),
    path(
        "school-admin/results/student/<uuid:student_uuid>/report/<int:session_id>/<int:term_id>/",
        school_admin_student_historical_report,
        name="school_admin_student_historical_report",
    ),
    path(
        "school-admin/results/student/<uuid:student_uuid>/history/",
        school_admin_student_result_history,
        name="school_admin_student_result_history",
    ),
    path(
        "school-admin/results/",
        school_admin_results,
        name="school_admin_results",
    ),
    path(
        "school-admin/results/class/<int:class_id>/",
        school_admin_class_result_details,
        name="school_admin_class_result_details",
    ),
    path(
        "school-admin/results/class/<int:class_id>/publish/",
        school_admin_publish_class_results,
        name="school_admin_publish_class_results",
    ),
    path(
        "school-admin/results/class/<int:class_id>/resumption-date/",
        school_admin_update_resumption_date,
        name="school_admin_update_resumption_date",
    ),
    path(
        "school-admin/fees/status/class/<int:class_id>/student/<uuid:student_uuid>/receipt/<int:payment_id>/",
        school_admin_payment_receipt,
        name="school_admin_payment_receipt",
    ),
    path(
        "school-admin/fees/status/class/<int:class_id>/student/<uuid:student_uuid>/",
        school_admin_student_payment_detail,
        name="school_admin_student_payment_detail",
    ),
    path(
        "school-admin/fees/status/class/<int:class_id>/",
        school_admin_class_payments,
        name="school_admin_class_payments",
    ),
    path(
        "school-admin/fees/status/",
        school_admin_fee_status,
        name="school_admin_fee_status",
    ),
    path(
        "school-admin/fees/",
        school_admin_fees,
        name="school_admin_fees",
    ),
    path(
        "school-admin/students/",
        school_admin_students,
        name="school_admin_students",
    ),
    path(
        "school-admin/students/class/<int:class_id>/",
        school_admin_class_students,
        name="school_admin_class_students",
    ),
    path(
        "school-admin/students/add/",
        school_admin_add_student,
        name="school_admin_add_student",
    ),
    path(
        "school-admin/students/<uuid:student_uuid>/department/",
        school_admin_set_student_department,
        name="school_admin_set_student_department",
    ),
    path(
        "school-admin/subjects/",
        school_admin_subjects,
        name="school_admin_subjects",
    ),
    path(
        "school-admin/subjects/add/",
        school_admin_add_subject,
        name="school_admin_add_subject",
    ),
    path(
        "school-admin/subjects/<int:subject_id>/edit/",
        school_admin_edit_subject,
        name="school_admin_edit_subject",
    ),
    path(
        "school-admin/subjects/generate/",
        school_admin_generate_subjects,
        name="school_admin_generate_subjects",
    ),
    
    path(
        "school-admin/parents/",
        school_admin_parents,
        name="school_admin_parents",
    ),

    path(
        "school-admin/messages/",
        school_admin_messages,
        name="school_admin_messages",
    ),
    path("parent/login/", parent_login, name="parent_login"),
    path("parent/logout/", parent_logout, name="parent_logout"),
    path("parent/dashboard/", parent_dashboard, name="parent_dashboard"),
    path(
        "parent/messages/",
        parent_messages,
        name="parent_messages",
    ),
    
    path(
        "parent/student/<uuid:student_uuid>/result/",
        parent_student_result,
        name="parent_student_result",
    ),
    path(
        "parent/student/<uuid:student_uuid>/results/",
        parent_student_result_history,
        name="parent_student_result_history_list",
    ),
    path(
        "parent/student/<uuid:student_uuid>/result/<int:session_id>/<int:term_id>/",
        parent_student_result,
        name="parent_student_result_history",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/",
        parent_school_fees,
        name="parent_school_fees",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/pay/",
        parent_pay_school_fees,
        name="parent_pay_school_fees",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/payment-review/",
        parent_payment_review,
        name="parent_payment_review",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/start-payment/",
        parent_start_payment,
        name="parent_start_payment",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/payment-callback/",
        parent_payment_callback,
        name="parent_payment_callback",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/receipt/<int:payment_id>/",
        parent_payment_receipt,
        name="parent_payment_receipt",
    ),
    path(
        "parent/student/<uuid:student_uuid>/fees/clearance/",
        parent_fee_clearance,
        name="parent_fee_clearance",
    ),
    path("teacher/login/", teacher_login, name="teacher_login"),
    path("teacher/change-password/", teacher_change_password, name="teacher_change_password"),
    path("teacher/dashboard/", teacher_dashboard, name="teacher_dashboard"),
    path("teacher/students/", teacher_students, name="teacher_students"),
    path(
        "teacher/results/",
        teacher_results,
        name="teacher_results",
    ),
    path(
        "teacher/results/class/<int:class_id>/",
        teacher_class_results,
        name="teacher_class_results",
    ),
    path(
        "teacher/results/student/<uuid:student_uuid>/",
        teacher_student_result_entry,
        name="teacher_student_result_entry",
    ),
    path(
        "teacher/results/student/<uuid:student_uuid>/terms/",
        teacher_student_term_reports,
        name="teacher_student_term_reports",
    ),
    path(
        "teacher/results/student/<uuid:student_uuid>/terms/<int:term_id>/",
        teacher_student_term_report,
        name="teacher_student_term_report",
    ),
    path(
        "teacher/results/student/<uuid:student_uuid>/report/",
        teacher_student_report,
        name="teacher_student_report",
    ),
    path(
        "teacher/students/<uuid:student_uuid>/department/",
        teacher_set_student_department,
        name="teacher_set_student_department",
    ),
    path("teacher/students/add/", teacher_add_student, name="teacher_add_student"),
    path("teacher/logout/", teacher_logout, name="teacher_logout"),

    path("admin/", admin.site.urls),
]
