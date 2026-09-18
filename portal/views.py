from datetime import timedelta
import requests
import logging
from decimal import Decimal, ROUND_UP
from decimal import Decimal, InvalidOperation

payment_logger = logging.getLogger("django.payment")

from django.contrib import messages
from django.db import transaction
from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.db.models import Sum, Q
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date

from accounts.models import User, SchoolMembership
from schools.models import School, SchoolSection, SchoolClass, TeacherClassAssignment, Subject, AcademicSession, AcademicTerm
from students.models import Student
from students.services import register_student_with_parent
from fees.models import FeeStructure, StudentPayment
from results.services import (
    get_class_result_completeness,
    get_student_required_subjects,
    update_student_term_summary,
    update_class_positions,
    update_student_annual_summary,
    update_class_annual_positions,
    get_next_school_class,
    get_result_remarks,
)
from results.models import (
    StudentResult,
    StudentTermSummary,
    StudentAnnualSummary,
    ClassResultPublication,
    ResultAuditLog,
)



def bootstrap_school_structure(school):
    """
    Create the standard academic structure for a newly registered school.

    Safe to call more than once. Existing sections/classes are reused,
    so this will not duplicate the school's structure.
    """

    structure = [
        {
            "name": "Primary",
            "academic_level": "PRIMARY",
            "section_order": 1,
            "classes": [
                ("Primary 1", 1),
                ("Primary 2", 2),
                ("Primary 3", 3),
                ("Primary 4", 4),
                ("Primary 5", 5),
                ("Primary 6", 6),
            ],
        },
        {
            "name": "Junior Secondary",
            "academic_level": "SECONDARY",
            "section_order": 2,
            "classes": [
                ("JSS 1", 1),
                ("JSS 2", 2),
                ("JSS 3", 3),
            ],
        },
        {
            "name": "Senior Secondary",
            "academic_level": "SECONDARY",
            "section_order": 3,
            "classes": [
                ("SS 1", 1),
                ("SS 2", 2),
                ("SS 3", 3),
            ],
        },
    ]

    created_sections = 0
    created_classes = 0

    with transaction.atomic():
        for section_data in structure:
            section, section_created = SchoolSection.objects.get_or_create(
                school=school,
                name=section_data["name"],
                defaults={
                    "academic_level": section_data["academic_level"],
                    "order": section_data["section_order"],
                    "is_active": True,
                },
            )

            if section_created:
                created_sections += 1

            for class_name, class_order in section_data["classes"]:
                school_class, class_created = SchoolClass.objects.get_or_create(
                    section=section,
                    name=class_name,
                    arm="",
                    defaults={
                        "order": class_order,
                        "is_active": True,
                    },
                )

                if class_created:
                    created_classes += 1

    return {
        "sections_created": created_sections,
        "classes_created": created_classes,
    }


def home(request):
    # 1. Preserve the school currently selected through its slug portal.
    school_slug = request.session.get("selected_school_slug")

    if school_slug:
        school = School.objects.filter(
            slug=school_slug,
        ).first()

        if school:
            return redirect(
                "school_portal",
                school_slug=school.slug,
            )

    # 2. Logged-in School Admin / Teacher fallback:
    # recover their real school from membership.
    if request.user.is_authenticated:
        membership = request.user.school_memberships.filter(
            is_active=True,
        ).select_related("school").first()

        if membership and membership.school:
            request.session["selected_school_slug"] = membership.school.slug

            return redirect(
                "school_portal",
                school_slug=membership.school.slug,
            )

    # 3. Only visitors with no school context see the generic homepage.
    return render(request, "portal/home.html")


def school_portal(request, school_slug):
    from django.utils import timezone

    # Find the school even when inactive so we can explain the reason.
    school = School.objects.filter(
        slug=school_slug,
    ).first()

    if not school:
        messages.error(request, "School portal not found.")
        return redirect("home")

    unavailable_title = None
    unavailable_message = None

    # CEO manually disabled the whole school portal.
    if not school.is_active:
        unavailable_title = "Portal Temporarily Inactive"
        unavailable_message = (
            "This school portal has been temporarily deactivated. "
            "Please contact the school administration or platform administrator."
        )

    # Subscription manually suspended.
    elif school.subscription_status == "SUSPENDED":
        unavailable_title = "Portal Suspended"
        unavailable_message = (
            "Access to this school portal is currently suspended. "
            "Please contact the school administration or platform administrator."
        )

    # Subscription marked expired.
    elif school.subscription_status == "EXPIRED":
        unavailable_title = "Subscription Expired"
        unavailable_message = (
            "This school's portal subscription has expired. "
            "Please contact the school administration to renew access."
        )

    # Also protect against an expiry date passing before status is manually changed.
    elif (
        school.subscription_expiry_date
        and school.subscription_expiry_date < timezone.localdate()
    ):
        unavailable_title = "Subscription Expired"
        unavailable_message = (
            "This school's portal subscription has expired. "
            "Please contact the school administration to renew access."
        )

    if unavailable_title:
        return render(
            request,
            "portal/school_portal_unavailable.html",
            {
                "school": school,
                "unavailable_title": unavailable_title,
                "unavailable_message": unavailable_message,
            },
            status=403,
        )

    # Only an available school becomes the selected school.
    request.session["selected_school_id"] = school.id
    request.session["selected_school_slug"] = school.slug

    return render(
        request,
        "portal/school_portal.html",
        {
            "school": school,
        },
    )


def parent_login(request, school_slug=None):
    # School-specific URL is the source of truth.
    if school_slug:
        school = School.objects.filter(
            slug=school_slug,
            is_active=True,
        ).first()

        if not school:
            return redirect("home")

        # Keep this for dashboard/logout compatibility.
        request.session["selected_school_id"] = school.id
        request.session["selected_school_slug"] = school.slug

    else:
        # Compatibility with the older global login URLs.
        selected_school_id = request.session.get("selected_school_id")

        if not selected_school_id:
            return redirect("home")

        school = School.objects.filter(
            id=selected_school_id,
            is_active=True,
        ).first()

        if not school:
            request.session.pop("selected_school_id", None)
            request.session.pop("selected_school_slug", None)
            return redirect("home")

    if request.user.is_authenticated:
        if request.user.role == User.Role.PARENT:
            parent_profile = getattr(
                request.user,
                "parent_profile",
                None,
            )

            if (
                parent_profile
                and parent_profile.student_links.filter(
                    student__school=school
                ).exists()
            ):
                return redirect("parent_dashboard")

            logout(request)

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()
        pin = request.POST.get("password", "").strip()

        user = User.objects.filter(
            phone=phone,
            role=User.Role.PARENT,
            is_active=True,
        ).first()

        expected_pin = phone[-6:] if len(phone) >= 6 else ""

        if not user or pin != expected_pin:
            messages.error(
                request,
                "The phone number or Parent PIN is incorrect.",
            )

        elif not hasattr(user, "parent_profile"):
            messages.error(
                request,
                "Your parent profile has not been set up yet.",
            )

        elif not user.parent_profile.student_links.filter(
            student__school=school
        ).exists():
            messages.error(
                request,
                "This parent account has no child registered in this school.",
            )

        else:
            login(request, user)

            # login/logout can rotate or flush the session,
            # so restore the school selected from the school-specific URL.
            request.session["selected_school_id"] = school.id
            request.session["selected_school_slug"] = school.slug

            return redirect("parent_dashboard")

    return render(
        request,
        "portal/parent_login.html",
        {
            "school": school,
        },
    )

def parent_dashboard(request):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    if request.user.role != User.Role.PARENT:
        return redirect("home")

    parent_profile = request.user.parent_profile

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        logout(request)
        messages.error(
            request,
            "Please select your school and log in again.",
        )
        return redirect("home")

    school = School.objects.filter(
        id=selected_school_id,
        is_active=True,
    ).first()

    if not school:
        logout(request)
        request.session.pop("selected_school_id", None)
        request.session.pop("selected_school_slug", None)
        return redirect("home")

    child_links = parent_profile.student_links.filter(
        student__school=school,
    ).select_related(
        "student",
        "student__current_class",
        "student__school",
    )

    children = [link.student for link in child_links]

    child_fee_data = []

    for child in children:
        fee_structure = FeeStructure.objects.filter(
            school=child.school,
            school_class=child.current_class,
            session__is_current=True,
            term__is_current=True,
            is_active=True,
        ).first()

        total_fee = fee_structure.amount if fee_structure else 0

        total_paid = 0
        if fee_structure:
            total_paid = (
                StudentPayment.objects.filter(
                    student=child,
                    fee_structure=fee_structure,
                    is_confirmed=True,
                ).aggregate(total=Sum("amount"))["total"]
                or 0
            )

        balance = total_fee - total_paid

        child_fee_data.append({
            "student": child,
            "fee_structure": fee_structure,
            "total_fee": total_fee,
            "total_paid": total_paid,
            "balance": balance,
        })

    context = {
        "parent_profile": parent_profile,
        "children": children,
        "child_fee_data": child_fee_data,
        "children": children,
        "school": school,
    }

    return render(
        request,
        "portal/parent_dashboard.html",
        context,
    )


def school_admin_dashboard(request):
    if not request.user.is_authenticated:
        return redirect("home")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        school_id=selected_school_id,
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    context = {
        "school": school,
        "student_count": school.students.count(),
        "section_count": school.sections.count(),
    }

    return render(
        request,
        "portal/school_admin_dashboard.html",
        context,
    )


def school_admin_login(request, school_slug=None):
    # School-specific URL is the source of truth.
    if school_slug:
        school = School.objects.filter(
            slug=school_slug,
            is_active=True,
        ).first()

        if not school:
            return redirect("home")

        # Keep this for dashboard/logout compatibility.
        request.session["selected_school_id"] = school.id
        request.session["selected_school_slug"] = school.slug

    else:
        # Compatibility with the older global login URLs.
        selected_school_id = request.session.get("selected_school_id")

        if not selected_school_id:
            return redirect("home")

        school = School.objects.filter(
            id=selected_school_id,
            is_active=True,
        ).first()

        if not school:
            request.session.pop("selected_school_id", None)
            request.session.pop("selected_school_slug", None)
            return redirect("home")

    if request.user.is_authenticated:
        if request.user.role == User.Role.SCHOOL_ADMIN:
            membership = SchoolMembership.objects.filter(
                user=request.user,
                school=school,
                staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
                is_active=True,
            ).first()

            if membership:
                return redirect("school_admin_dashboard")

            logout(request)

    error = None

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(
            request,
            username=phone,
            password=password,
        )

        if user is None:
            error = "Invalid phone number or password."

        elif user.role != User.Role.SCHOOL_ADMIN:
            error = "This account is not a School Admin account."

        else:
            membership = SchoolMembership.objects.filter(
                user=user,
                school=school,
                staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
                is_active=True,
            ).first()

            if not membership:
                error = "This administrator does not belong to this school."

            elif not school.is_active:
                error = "This school portal is currently inactive."

            else:
                login(request, user)

                request.session["selected_school_id"] = school.id
                request.session["selected_school_slug"] = school.slug

                if user.must_change_password:
                    return redirect("school_admin_change_password")

                return redirect("school_admin_dashboard")

    return render(
        request,
        "portal/school_admin_login.html",
        {
            "error": error,
            "school": school,
        },
    )


def school_admin_change_password(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    request.session["selected_school_id"] = school.id
    request.session["selected_school_slug"] = school.slug

    # Remove stale messages carried over from CEO/school creation.
    # Real password errors created during POST will still display normally.
    if request.method == "GET":
        list(messages.get_messages(request))

    if request.method == "POST":
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")

        if len(password1) < 8:
            messages.error(
                request,
                "Password must be at least 8 characters.",
            )
        elif password1 != password2:
            messages.error(
                request,
                "Passwords do not match.",
            )
        else:
            request.user.set_password(password1)
            request.user.must_change_password = False
            request.user.save(
                update_fields=["password", "must_change_password"]
            )

            login(request, request.user)

            messages.success(
                request,
                "Your password has been changed successfully.",
            )
            return redirect("school_admin_dashboard")

    return render(
        request,
        "portal/school_admin_change_password.html",
        {"school": school},
    )


def school_admin_fee_status(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = None
    if session:
        term = school.academic_terms.filter(
            session=session,
            is_current=True,
            is_active=True,
        ).first()

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related(
        "section",
    ).order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    class_rows = []

    total_expected_all = Decimal("0")
    total_collected_all = Decimal("0")

    for school_class in classes:
        students = Student.objects.filter(
            school=school,
            current_class=school_class,
            status=Student.Status.ACTIVE,
        )

        student_count = students.count()

        fee_structure = None

        if session and term:
            fee_structure = FeeStructure.objects.filter(
                school=school,
                school_class=school_class,
                session=session,
                term=term,
                title="School Fees",
                is_active=True,
            ).first()

        total_fee = fee_structure.amount if fee_structure else Decimal("0")

        fully_paid = 0
        part_paid = 0
        unpaid = 0
        collected = Decimal("0")

        for student in students:
            paid = Decimal("0")

            if fee_structure:
                paid = (
                    StudentPayment.objects.filter(
                        student=student,
                        fee_structure=fee_structure,
                        is_confirmed=True,
                    ).aggregate(
                        total=Sum("amount")
                    )["total"]
                    or Decimal("0")
                )

            collected += paid

            if total_fee <= 0:
                unpaid += 1
            elif paid >= total_fee:
                fully_paid += 1
            elif paid > 0:
                part_paid += 1
            else:
                unpaid += 1

        expected = total_fee * student_count
        outstanding = expected - collected

        if outstanding < 0:
            outstanding = Decimal("0")

        total_expected_all += expected
        total_collected_all += collected

        class_rows.append({
            "school_class": school_class,
            "student_count": student_count,
            "fully_paid": fully_paid,
            "part_paid": part_paid,
            "unpaid": unpaid,
            "fee_structure": fee_structure,
            "fee_amount": f"{total_fee:,.2f}",
            "expected": f"{expected:,.2f}",
            "collected": f"{collected:,.2f}",
            "outstanding": f"{outstanding:,.2f}",
        })

    overall_outstanding = total_expected_all - total_collected_all

    if overall_outstanding < 0:
        overall_outstanding = Decimal("0")

    return render(
        request,
        "portal/school_admin_fee_status.html",
        {
            "school": school,
            "session": session,
            "term": term,
            "class_rows": class_rows,
            "overall_expected": f"{total_expected_all:,.2f}",
            "overall_collected": f"{total_collected_all:,.2f}",
            "overall_outstanding": f"{overall_outstanding:,.2f}",
        },
    )



def school_admin_class_payments(request, class_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    try:
        school_class = SchoolClass.objects.select_related(
            "section"
        ).get(
            id=class_id,
            section__school=school,
            is_active=True,
        )
    except SchoolClass.DoesNotExist:
        messages.error(request, "That class was not found.")
        return redirect("school_admin_fee_status")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = None

    if session:
        term = school.academic_terms.filter(
            session=session,
            is_current=True,
            is_active=True,
        ).first()

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect("school_admin_fee_status")

    fee_structure = FeeStructure.objects.filter(
        school=school,
        school_class=school_class,
        session=session,
        term=term,
        title="School Fees",
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            f"School fee has not been set for {school_class.display_name}.",
        )
        return redirect("school_admin_fee_status")

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "all").strip().lower()

    if status_filter not in {
        "all",
        "paid",
        "part-paid",
        "unpaid",
    }:
        status_filter = "all"

    students = Student.objects.filter(
        school=school,
        current_class=school_class,
        status=Student.Status.ACTIVE,
    ).order_by(
        "surname",
        "first_name",
        "middle_name",
    )

    if search_query:
        students = students.filter(
            Q(surname__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(middle_name__icontains=search_query)
            | Q(admission_number__icontains=search_query)
        )

    rows = []

    fully_paid_count = 0
    part_paid_count = 0
    unpaid_count = 0

    class_total_collected = Decimal("0")

    all_class_students = Student.objects.filter(
        school=school,
        current_class=school_class,
        status=Student.Status.ACTIVE,
    )

    # Counts/totals are calculated from the whole class,
    # so search/filtering does not change the summary cards.
    for student in all_class_students:
        paid = (
            StudentPayment.objects.filter(
                student=student,
                fee_structure=fee_structure,
                is_confirmed=True,
            ).aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0")
        )

        class_total_collected += paid

        if paid >= fee_structure.amount:
            fully_paid_count += 1
        elif paid > 0:
            part_paid_count += 1
        else:
            unpaid_count += 1

    for student in students:
        payments = StudentPayment.objects.filter(
            student=student,
            fee_structure=fee_structure,
            is_confirmed=True,
        ).order_by("-paid_at")

        paid = (
            payments.aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0")
        )

        balance = fee_structure.amount - paid

        if balance < 0:
            balance = Decimal("0")

        if paid >= fee_structure.amount:
            payment_status = "paid"
            payment_status_label = "Fully Paid"

        elif paid > 0:
            payment_status = "part-paid"
            payment_status_label = "Part-paid"

        else:
            payment_status = "unpaid"
            payment_status_label = "Unpaid"

        if (
            status_filter != "all"
            and payment_status != status_filter
        ):
            continue

        latest_payment = payments.first()

        rows.append({
            "student": student,
            "fee_amount": f"{fee_structure.amount:,.2f}",
            "paid": f"{paid:,.2f}",
            "balance": f"{balance:,.2f}",
            "payment_status": payment_status,
            "payment_status_label": payment_status_label,
            "latest_payment": latest_payment,
        })

    class_student_count = all_class_students.count()

    expected = fee_structure.amount * class_student_count
    outstanding = expected - class_total_collected

    if outstanding < 0:
        outstanding = Decimal("0")

    return render(
        request,
        "portal/school_admin_class_payments.html",
        {
            "school": school,
            "school_class": school_class,
            "session": session,
            "term": term,
            "fee_structure": fee_structure,
            "fee_amount": f"{fee_structure.amount:,.2f}",
            "rows": rows,

            "student_count": class_student_count,
            "fully_paid_count": fully_paid_count,
            "part_paid_count": part_paid_count,
            "unpaid_count": unpaid_count,

            "expected": f"{expected:,.2f}",
            "collected": f"{class_total_collected:,.2f}",
            "outstanding": f"{outstanding:,.2f}",

            "search_query": search_query,
            "status_filter": status_filter,
        },
    )



def school_admin_student_payment_detail(request, class_id, student_uuid):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    try:
        school_class = SchoolClass.objects.select_related(
            "section"
        ).get(
            id=class_id,
            section__school=school,
            is_active=True,
        )
    except SchoolClass.DoesNotExist:
        messages.error(request, "That class was not found.")
        return redirect("school_admin_fee_status")

    try:
        student = Student.objects.select_related(
            "current_class",
            "school",
        ).get(
            uuid=student_uuid,
            school=school,
            current_class=school_class,
        )
    except Student.DoesNotExist:
        messages.error(request, "That student was not found in this class.")
        return redirect(
            "school_admin_class_payments",
            class_id=school_class.id,
        )

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = None

    if session:
        term = school.academic_terms.filter(
            session=session,
            is_current=True,
            is_active=True,
        ).first()

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect(
            "school_admin_class_payments",
            class_id=school_class.id,
        )

    fee_structure = FeeStructure.objects.filter(
        school=school,
        school_class=school_class,
        session=session,
        term=term,
        title="School Fees",
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            "No active school fee was found for this class.",
        )
        return redirect(
            "school_admin_class_payments",
            class_id=school_class.id,
        )

    payments = StudentPayment.objects.filter(
        student=student,
        fee_structure=fee_structure,
        is_confirmed=True,
    ).order_by("paid_at")

    total_paid = (
        payments.aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    balance = fee_structure.amount - total_paid

    if balance < 0:
        balance = Decimal("0")

    if total_paid >= fee_structure.amount:
        payment_status = "Fully Paid"
        status_class = "paid"

    elif total_paid > 0:
        payment_status = "Part-paid"
        status_class = "part-paid"

    else:
        payment_status = "Unpaid"
        status_class = "unpaid"

    payment_rows = []

    running_total = Decimal("0")

    for number, payment in enumerate(payments, start=1):
        running_total += payment.amount

        remaining_after = fee_structure.amount - running_total

        if remaining_after < 0:
            remaining_after = Decimal("0")

        payment_rows.append({
            "number": number,
            "payment": payment,
            "amount": f"{payment.amount:,.2f}",
            "running_total": f"{running_total:,.2f}",
            "remaining_after": f"{remaining_after:,.2f}",
        })

    return render(
        request,
        "portal/school_admin_student_payment_detail.html",
        {
            "school": school,
            "school_class": school_class,
            "student": student,
            "session": session,
            "term": term,
            "fee_structure": fee_structure,
            "fee_amount": f"{fee_structure.amount:,.2f}",
            "total_paid": f"{total_paid:,.2f}",
            "balance": f"{balance:,.2f}",
            "payment_status": payment_status,
            "status_class": status_class,
            "payment_rows": payment_rows,
            "payment_count": payments.count(),
        },
    )



def school_admin_payment_receipt(
    request,
    class_id,
    student_uuid,
    payment_id,
):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    try:
        school_class = SchoolClass.objects.select_related(
            "section"
        ).get(
            id=class_id,
            section__school=school,
            is_active=True,
        )
    except SchoolClass.DoesNotExist:
        messages.error(request, "That class was not found.")
        return redirect("school_admin_fee_status")

    try:
        student = Student.objects.select_related(
            "school",
            "current_class",
        ).get(
            uuid=student_uuid,
            school=school,
            current_class=school_class,
        )
    except Student.DoesNotExist:
        messages.error(
            request,
            "That student was not found in this class.",
        )
        return redirect(
            "school_admin_class_payments",
            class_id=school_class.id,
        )

    payment = StudentPayment.objects.filter(
        id=payment_id,
        student=student,
        fee_structure__school=school,
        fee_structure__school_class=school_class,
        is_confirmed=True,
    ).select_related(
        "fee_structure",
        "fee_structure__session",
        "fee_structure__term",
        "fee_structure__school_class",
    ).first()

    if not payment:
        messages.error(
            request,
            "That confirmed payment receipt was not found.",
        )
        return redirect(
            "school_admin_student_payment_detail",
            class_id=school_class.id,
            student_uuid=student.uuid,
        )

    service_fee = school.portal_service_fee
    total_charged = payment.amount + service_fee

    return render(
        request,
        "portal/school_admin_payment_receipt.html",
        {
            "school": school,
            "school_class": school_class,
            "student": student,
            "payment": payment,
            "fee_structure": payment.fee_structure,
            "session": payment.fee_structure.session,
            "term": payment.fee_structure.term,
            "payment_amount": f"{payment.amount:,.2f}",
            "service_fee": f"{service_fee:,.2f}",
            "total_charged": f"{total_charged:,.2f}",
        },
    )


def school_admin_fees(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    if not school.school_admin_can_manage_fees:
        messages.error(
            request,
            "Fee management has been disabled for this school by the platform administrator.",
        )
        return redirect("school_admin_dashboard")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = None
    if session:
        term = school.academic_terms.filter(
            session=session,
            is_current=True,
            is_active=True,
        ).first()

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related(
        "section",
    ).order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    if request.method == "POST":
        if not session or not term:
            messages.error(
                request,
                "A current academic session and term must exist before fees can be set.",
            )
            return redirect("school_admin_fees")

        class_id = request.POST.get("school_class", "").strip()
        amount_raw = (
            request.POST.get("amount", "")
            .replace("₦", "")
            .replace(",", "")
            .strip()
        )

        try:
            school_class = SchoolClass.objects.get(
                id=class_id,
                section__school=school,
                is_active=True,
            )
        except (SchoolClass.DoesNotExist, ValueError):
            messages.error(request, "Please select a valid class.")
            return redirect("school_admin_fees")

        try:
            amount = Decimal(amount_raw)
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Please enter a valid fee amount.")
            return redirect("school_admin_fees")

        if amount <= 0:
            messages.error(request, "Fee amount must be greater than zero.")
            return redirect("school_admin_fees")

        fee_structure = FeeStructure.objects.filter(
            school=school,
            school_class=school_class,
            session=session,
            term=term,
            title="School Fees",
        ).first()

        if fee_structure:
            has_confirmed_payments = StudentPayment.objects.filter(
                fee_structure=fee_structure,
                is_confirmed=True,
            ).exists()

            if has_confirmed_payments and fee_structure.amount != amount:
                messages.error(
                    request,
                    f"{school_class.display_name} fee cannot be changed because confirmed payments already exist.",
                )
                return redirect("school_admin_fees")

            fee_structure.amount = amount
            fee_structure.is_active = True
            fee_structure.save(
                update_fields=[
                    "amount",
                    "is_active",
                ]
            )

            messages.success(
                request,
                f"{school_class.display_name} school fee updated successfully.",
            )
        else:
            FeeStructure.objects.create(
                school=school,
                school_class=school_class,
                session=session,
                term=term,
                title="School Fees",
                amount=amount,
                is_active=True,
            )

            messages.success(
                request,
                f"{school_class.display_name} school fee created successfully.",
            )

        return redirect("school_admin_fees")

    fee_structures = FeeStructure.objects.none()

    if session and term:
        fee_structures = (
            FeeStructure.objects.filter(
                school=school,
                session=session,
                term=term,
                title="School Fees",
            )
            .select_related(
                "school_class",
                "school_class__section",
                "session",
                "term",
            )
            .order_by(
                "school_class__section__order",
                "school_class__order",
                "school_class__name",
                "school_class__arm",
            )
        )

    fee_rows = []
    existing_fee_map = {}

    for fee in fee_structures:
        locked = StudentPayment.objects.filter(
            fee_structure=fee,
            is_confirmed=True,
        ).exists()

        fee_rows.append({
            "fee": fee,
            "formatted_amount": f"{fee.amount:,.2f}",
            "locked": locked,
        })

        existing_fee_map[fee.school_class_id] = {
            "amount": f"{fee.amount:,.2f}",
            "locked": locked,
        }

    return render(
        request,
        "portal/school_admin_fees.html",
        {
            "school": school,
            "session": session,
            "term": term,
            "classes": classes,
            "fee_rows": fee_rows,
            "existing_fee_map": existing_fee_map,
        },
    )



def school_admin_students(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    class_rows = []
    for school_class in classes:
        student_count = Student.objects.filter(
            school=school,
            current_class=school_class,
            status=Student.Status.ACTIVE,
        ).count()

        class_rows.append({
            "school_class": school_class,
            "student_count": student_count,
        })

    query = request.GET.get("q", "").strip()
    search_results = Student.objects.none()

    if query:
        search_results = Student.objects.filter(
            school=school,
            status=Student.Status.ACTIVE,
        ).filter(
            Q(surname__icontains=query)
            | Q(first_name__icontains=query)
            | Q(middle_name__icontains=query)
            | Q(admission_number__icontains=query)
        ).select_related(
            "current_class",
            "current_class__section",
        ).order_by(
            "surname",
            "first_name",
        )

    registration_success = request.session.pop(
        "registration_success",
        None,
    )

    return render(
        request,
        "portal/school_admin_students.html",
        {
            "school": school,
            "class_rows": class_rows,
            "query": query,
            "search_results": search_results,
            "registration_success": registration_success,
        },
    )


def school_admin_class_students(request, class_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    try:
        school_class = SchoolClass.objects.select_related(
            "section"
        ).get(
            id=class_id,
            section__school=school,
            is_active=True,
        )
    except SchoolClass.DoesNotExist:
        messages.error(request, "That class was not found.")
        return redirect("school_admin_students")

    students = Student.objects.filter(
        school=school,
        current_class=school_class,
        status=Student.Status.ACTIVE,
    )

    query = request.GET.get("q", "").strip()

    if query:
        students = students.filter(
            Q(surname__icontains=query)
            | Q(first_name__icontains=query)
            | Q(middle_name__icontains=query)
            | Q(admission_number__icontains=query)
        )

    students = students.order_by(
        "surname",
        "first_name",
    )

    return render(
        request,
        "portal/school_admin_class_students.html",
        {
            "school": school,
            "school_class": school_class,
            "students": students,
            "query": query,
            "student_count": students.count(),
        },
    )


def school_admin_add_student(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school

    classes = school.sections.prefetch_related("classes")

    if request.method == "POST":
        try:
            current_class = SchoolClass.objects.get(
                id=request.POST.get("current_class"),
                section__school=school,
            )

            result = register_student_with_parent(
                school=school,
                current_class=current_class,
                student_type=request.POST.get("student_type"),
                surname=request.POST.get("surname", "").strip(),
                first_name=request.POST.get("first_name", "").strip(),
                middle_name=request.POST.get("middle_name", "").strip(),
                gender=request.POST.get("gender"),
                date_of_birth=request.POST.get("date_of_birth") or None,
                admission_year=request.POST.get("admission_year"),
                entry_class_name=request.POST.get("entry_class_name", "").strip(),
                parent_phone=request.POST.get("parent_phone", "").strip(),
                parent_first_name="",
                parent_last_name=request.POST.get("parent_name", "").strip(),
                parent_email=request.POST.get("parent_email", "").strip(),
                relationship=request.POST.get("relationship"),
                previous_school=request.POST.get("previous_school", "").strip(),
            )

            student = result["student"]

            request.session["registration_success"] = {
                "student_name": student.full_name,
                "admission_number": student.admission_number,
                "parent_phone": result["parent_user"].phone,
                "temporary_password": result["temporary_password"],
                "parent_created": result["parent_created"],
            }

            return redirect("school_admin_students")

        except Exception as error:
            print(
                "ADD STUDENT ERROR:",
                type(error).__name__,
                str(error),
                flush=True,
            )
            messages.error(
                request,
                f"Student registration failed: {error}",
            )

    return render(
        request,
        "portal/school_admin_add_student.html",
        {
            "school": school,
            "sections": classes,
            "form_data": request.POST,
        },
    )



def _logout_school_slug(request):
    """Recover the current school's slug before Django clears the session."""

    # Best source: school-specific portal/login already stored the slug.
    school_slug = request.session.get("selected_school_slug")
    if school_slug:
        return school_slug

    # Older/session-compatible source.
    school_id = request.session.get("selected_school_id")
    if school_id:
        school = School.objects.filter(id=school_id).first()
        if school:
            return school.slug

    # School Admin / Teacher fallback.
    if request.user.is_authenticated:
        membership = request.user.school_memberships.filter(
            is_active=True
        ).select_related("school").first()

        if membership and membership.school:
            return membership.school.slug

        # Parent fallback: recover school through one of their linked students.
        parent_profile = getattr(request.user, "parent_profile", None)
        if parent_profile:
            link = parent_profile.student_links.select_related(
                "student__school"
            ).first()

            if link and link.student and link.student.school:
                return link.student.school.slug

    return None


def parent_logout(request):
    school_slug = _logout_school_slug(request)
    logout(request)

    if school_slug:
        return redirect("school_portal", school_slug=school_slug)

    return redirect("home")
def school_admin_logout(request):
    school_slug = _logout_school_slug(request)
    logout(request)

    if school_slug:
        return redirect("school_portal", school_slug=school_slug)

    return redirect("home")
def parent_school_fees(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    from students.models import Student, StudentParent

    parent_profile = getattr(request.user, "parent_profile", None)

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student

    fee_structure = FeeStructure.objects.filter(
        school=student.school,
        school_class=student.current_class,
        session__is_current=True,
        term__is_current=True,
        is_active=True,
    ).first()

    total_fee = fee_structure.amount if fee_structure else 0

    payments = StudentPayment.objects.filter(
        student=student,
        fee_structure=fee_structure,
        is_confirmed=True,
    ).order_by("-paid_at") if fee_structure else StudentPayment.objects.none()

    total_paid = payments.aggregate(
        total=Sum("amount")
    )["total"] or 0

    balance = total_fee - total_paid

    return render(
        request,
        "portal/parent_school_fees.html",
        {
            "school": student.school,
            "student": student,
            "fee_structure": fee_structure,
            "payments": payments,
            "total_fee": total_fee,
            "total_paid": total_paid,
            "balance": balance,
        },
    )



def parent_pay_school_fees(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    from students.models import StudentParent

    parent_profile = getattr(request.user, "parent_profile", None)

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student

    fee_structure = FeeStructure.objects.filter(
        school=student.school,
        school_class=student.current_class,
        session__is_current=True,
        term__is_current=True,
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            "No active school fee has been configured for this student."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    total_paid = (
        StudentPayment.objects.filter(
            student=student,
            fee_structure=fee_structure,
            is_confirmed=True,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    balance = fee_structure.amount - total_paid

    if balance <= 0:
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    payment_count = StudentPayment.objects.filter(
        student=student,
        fee_structure=fee_structure,
        is_confirmed=True,
    ).count()

    remaining_payment_slots = max(0, 3 - payment_count)

    # Standard installment minimum is based on one-third
    # of the ORIGINAL school fee, rounded upward to ₦1,000.
    standard_minimum = (
        (fee_structure.amount / Decimal("3"))
        / Decimal("1000")
    ).quantize(
        Decimal("1"),
        rounding=ROUND_UP,
    ) * Decimal("1000")

    # If only one slot remains OR the outstanding balance is
    # already below the standard minimum, clear the full balance.
    if remaining_payment_slots <= 1 or balance <= standard_minimum:
        minimum_payment = balance
    else:
        minimum_payment = standard_minimum

    if request.method == "POST":
        try:
            amount = Decimal(
                request.POST.get("amount", "0")
            )

            if amount <= 0:
                raise ValueError(
                    "Payment amount must be greater than zero."
                )

            if amount > balance:
                raise ValueError(
                    "You cannot pay more than the outstanding balance."
                )

            if remaining_payment_slots <= 0:
                raise ValueError(
                    "No installment slots remain."
                )

            if remaining_payment_slots == 1:
                if amount != balance:
                    raise ValueError(
                        "This is your final installment. "
                        "You must pay the full outstanding balance."
                    )
            elif minimum_payment == balance and amount != balance:
                raise ValueError(
                    f"You must pay exactly the remaining balance of ₦{balance:,.2f}."
                )
            elif amount < minimum_payment:
                raise ValueError(
                    f"The minimum payment is ₦{minimum_payment:,.2f}."
                )

            request.session["pending_payment"] = {
                "student_uuid": str(student.uuid),
                "fee_structure_id": fee_structure.id,
                "amount": str(amount),
            }

            return redirect(
                "parent_payment_review",
                student_uuid=student.uuid,
            )

        except Exception as error:
            print(
                "ADD STUDENT ERROR:",
                type(error).__name__,
                str(error),
                flush=True,
            )
            messages.error(
                request,
                str(error),
            )

    return render(
        request,
        "portal/parent_pay_school_fees.html",
        {
            "student": student,
            "fee_structure": fee_structure,
            "total_paid": total_paid,
            "balance": balance,
            "minimum_payment": minimum_payment,
            "payment_count": payment_count,
            "remaining_payment_slots": remaining_payment_slots,
        },
    )



def parent_payment_review(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    from students.models import StudentParent

    parent_profile = getattr(request.user, "parent_profile", None)

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    pending_payment = request.session.get("pending_payment")

    if not pending_payment:
        messages.error(
            request,
            "No pending payment was found."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    if pending_payment.get("student_uuid") != str(student_uuid):
        messages.error(
            request,
            "The payment details do not match this student."
        )
        return redirect("parent_dashboard")

    student = link.student

    fee_structure = FeeStructure.objects.filter(
        id=pending_payment.get("fee_structure_id"),
        school=student.school,
        school_class=student.current_class,
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            "The selected school fee is no longer available."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    amount = Decimal(
        pending_payment.get("amount", "0")
    )

    total_paid = (
        StudentPayment.objects.filter(
            student=student,
            fee_structure=fee_structure,
            is_confirmed=True,
        ).aggregate(total=Sum("amount"))["total"]
        or 0
    )

    balance_before_payment = fee_structure.amount - total_paid
    balance_after_payment = balance_before_payment - amount

    service_fee = student.school.portal_service_fee
    total_charge = amount + service_fee

    return render(
        request,
        "portal/parent_payment_review.html",
        {
            "student": student,
            "fee_structure": fee_structure,
            "amount": amount,
            "service_fee": service_fee,
            "total_charge": total_charge,
            "balance_before_payment": balance_before_payment,
            "balance_after_payment": balance_after_payment,
        },
    )



def parent_start_payment(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    if request.method != "POST":
        return redirect(
            "parent_payment_review",
            student_uuid=student_uuid,
        )

    pending_payment = request.session.get("pending_payment")

    if not pending_payment:
        messages.error(
            request,
            "No pending payment was found."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    if pending_payment.get("student_uuid") != str(student_uuid):
        messages.error(
            request,
            "The payment details do not match this student."
        )
        return redirect("parent_dashboard")

    from students.models import StudentParent

    parent_profile = getattr(request.user, "parent_profile", None)

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student

    fee_structure = FeeStructure.objects.filter(
        id=pending_payment.get("fee_structure_id"),
        school=student.school,
        school_class=student.current_class,
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            "The selected school fee is no longer available."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    if not request.user.email:
        messages.error(
            request,
            "A parent email address is required before payment."
        )
        return redirect(
            "parent_payment_review",
            student_uuid=student.uuid,
        )

    amount = Decimal(
        pending_payment.get("amount", "0")
    )

    school = student.school

    if not school.paystack_subaccount_active or not school.paystack_subaccount_code:
        messages.error(
            request,
            "Online payment is not yet configured for this school.",
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    service_fee = school.portal_service_fee
    total_charge = amount + service_fee

    # Paystack requires the transaction amount in kobo.
    paystack_amount = int(total_charge * 100)

    callback_url = request.build_absolute_uri(
        f"/parent/student/{student.uuid}/fees/payment-callback/"
    )

    payload = {
        "email": request.user.email,
        "amount": str(paystack_amount),
        "callback_url": callback_url,
        "subaccount": school.paystack_subaccount_code,
        "transaction_charge": int(service_fee * 100),
        "metadata": {
            "student_uuid": str(student.uuid),
            "admission_number": student.admission_number,
            "fee_structure_id": fee_structure.id,
            "school_fee_amount": str(amount),
            "service_fee": str(service_fee),
        },
    }

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.paystack.co/transaction/initialize",
            json=payload,
            headers=headers,
            timeout=30,
        )

        data = response.json()

    except requests.RequestException:
        payment_logger.exception(
            "PAYSTACK INITIALIZE CONNECTION FAILED student_uuid=%s school_id=%s",
            student.uuid,
            school.id,
        )
        messages.error(
            request,
            "Unable to connect to the payment service. Please try again."
        )
        return redirect(
            "parent_payment_review",
            student_uuid=student.uuid,
        )

    if not response.ok or not data.get("status"):
        payment_logger.error(
            "PAYSTACK INITIALIZE REJECTED student_uuid=%s school_id=%s http_status=%s message=%s",
            student.uuid,
            school.id,
            response.status_code,
            data.get("message", ""),
        )
        messages.error(
            request,
            data.get(
                "message",
                "Payment could not be initialized."
            ),
        )
        return redirect(
            "parent_payment_review",
            student_uuid=student.uuid,
        )

    request.session["pending_payment"]["paystack_reference"] = (
        data["data"]["reference"]
    )
    request.session["pending_payment"]["service_fee"] = str(service_fee)
    request.session["pending_payment"]["school_id"] = school.id
    request.session["pending_payment"]["paystack_subaccount_code"] = (
        school.paystack_subaccount_code
    )
    request.session.modified = True

    return redirect(
        data["data"]["authorization_url"]
    )



def parent_payment_callback(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    reference = request.GET.get("reference")

    if not reference:
        payment_logger.error(
            "PAYSTACK CALLBACK MISSING REFERENCE student_uuid=%s",
            student_uuid,
        )
        messages.error(
            request,
            "No payment reference was returned."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    pending_payment = request.session.get("pending_payment")

    if not pending_payment:
        payment_logger.error(
            "PAYSTACK CALLBACK WITHOUT PENDING PAYMENT student_uuid=%s reference=%s",
            student_uuid,
            reference,
        )
        messages.error(
            request,
            "No pending payment was found."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    if pending_payment.get("student_uuid") != str(student_uuid):
        payment_logger.error(
            "PAYSTACK STUDENT MISMATCH callback_student=%s pending_student=%s reference=%s",
            student_uuid,
            pending_payment.get("student_uuid"),
            reference,
        )
        messages.error(
            request,
            "The payment does not match this student."
        )
        return redirect("parent_dashboard")

    expected_reference = pending_payment.get(
        "paystack_reference"
    )

    if expected_reference and reference != expected_reference:
        payment_logger.error(
            "PAYSTACK REFERENCE MISMATCH student_uuid=%s received=%s expected=%s",
            student_uuid,
            reference,
            expected_reference,
        )
        messages.error(
            request,
            "Payment reference mismatch."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers=headers,
            timeout=30,
        )

        data = response.json()

    except requests.RequestException:
        payment_logger.exception(
            "PAYSTACK VERIFY CONNECTION FAILED student_uuid=%s reference=%s",
            student_uuid,
            reference,
        )
        messages.error(
            request,
            "Unable to verify the payment right now."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    if (
        not response.ok
        or not data.get("status")
        or data.get("data", {}).get("status") != "success"
    ):
        payment_logger.error(
            "PAYSTACK VERIFY FAILED student_uuid=%s reference=%s http_status=%s paystack_status=%s message=%s",
            student_uuid,
            reference,
            response.status_code,
            data.get("data", {}).get("status"),
            data.get("message", ""),
        )
        messages.error(
            request,
            "Payment was not successful."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    verified = data["data"]

    expected_school_fee = Decimal(
        pending_payment.get("amount", "0")
    )

    service_fee = Decimal(
        pending_payment.get("service_fee", "400.00")
    )

    expected_total_kobo = int(
        (expected_school_fee + service_fee) * 100
    )

    verified_requested_amount = verified.get("requested_amount")

    if verified_requested_amount is None:
        verified_requested_amount = verified.get("amount")

    if int(verified_requested_amount or 0) != expected_total_kobo:
        payment_logger.error(
            "PAYSTACK AMOUNT MISMATCH student_uuid=%s reference=%s received_kobo=%s expected_kobo=%s",
            student_uuid,
            reference,
            verified_requested_amount,
            expected_total_kobo,
        )
        messages.error(
            request,
            "Verified payment amount does not match the expected amount."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    from students.models import StudentParent

    parent_profile = getattr(
        request.user,
        "parent_profile",
        None,
    )

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        messages.error(
            request,
            "Your school session could not be verified.",
        )
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student

    fee_structure = FeeStructure.objects.filter(
        id=pending_payment.get("fee_structure_id"),
        school=student.school,
        school_class=student.current_class,
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            "The fee record could not be found."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student_uuid,
        )

    expected_school_id = pending_payment.get("school_id")
    expected_subaccount = pending_payment.get(
        "paystack_subaccount_code",
        "",
    )

    if expected_school_id and int(expected_school_id) != student.school_id:
        payment_logger.error(
            "PAYSTACK SCHOOL MISMATCH student_uuid=%s reference=%s pending_school=%s actual_school=%s",
            student_uuid,
            reference,
            expected_school_id,
            student.school_id,
        )
        messages.error(
            request,
            "Payment school verification failed.",
        )
        return redirect("parent_dashboard")

    payment, created = StudentPayment.objects.get_or_create(
        reference=reference,
        defaults={
            "student": student,
            "fee_structure": fee_structure,
            "amount": expected_school_fee,
            "school": student.school,
            "paystack_subaccount_code": expected_subaccount,
            "is_confirmed": True,
        },
    )

    if created:
        messages.success(
            request,
            "Payment verified and recorded successfully."
        )
    else:
        messages.info(
            request,
            "This payment was already recorded."
        )

    request.session.pop("pending_payment", None)

    return redirect(
        "parent_school_fees",
        student_uuid=student_uuid,
    )



def parent_payment_receipt(request, student_uuid, payment_id):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    from students.models import StudentParent

    parent_profile = getattr(
        request.user,
        "parent_profile",
        None,
    )

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student

    payment = StudentPayment.objects.filter(
        id=payment_id,
        student=student,
        is_confirmed=True,
    ).select_related(
        "fee_structure",
        "fee_structure__session",
        "fee_structure__term",
    ).first()

    if not payment:
        messages.error(
            request,
            "Receipt not found."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    service_fee = student.school.portal_service_fee
    total_charged = payment.amount + service_fee

    return render(
        request,
        "portal/parent_payment_receipt.html",
        {
            "student": student,
            "payment": payment,
            "fee_structure": payment.fee_structure,
            "service_fee": service_fee,
            "total_charged": total_charged,
        },
    )



def parent_fee_clearance(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    from students.models import StudentParent

    parent_profile = getattr(
        request.user,
        "parent_profile",
        None,
    )

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student

    fee_structure = FeeStructure.objects.filter(
        school=student.school,
        school_class=student.current_class,
        session__is_current=True,
        term__is_current=True,
        is_active=True,
    ).first()

    if not fee_structure:
        messages.error(
            request,
            "No active school fee record was found."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    payments = StudentPayment.objects.filter(
        student=student,
        fee_structure=fee_structure,
        is_confirmed=True,
    )

    total_paid = (
        payments.aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    balance = fee_structure.amount - total_paid

    if balance > 0:
        messages.error(
            request,
            "School Fee Clearance is available only after full payment."
        )
        return redirect(
            "parent_school_fees",
            student_uuid=student.uuid,
        )

    return render(
        request,
        "portal/parent_fee_clearance.html",
        {
            "student": student,
            "fee_structure": fee_structure,
            "total_fee": fee_structure.amount,
            "total_paid": total_paid,
            "payments": payments.order_by("paid_at"),
        },
    )




def school_admin_teachers(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role="SCHOOL_ADMIN",
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found for this account.",
        )
        return redirect("home")

    school = membership.school

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        surname = request.POST.get("surname", "").strip()
        phone = request.POST.get("phone", "").strip()
        class_id = request.POST.get("school_class", "").strip()

        if not first_name or not surname or not phone or not class_id:
            messages.error(request, "Please complete all teacher fields.")
            return redirect("school_admin_teachers")

        try:
            school_class = SchoolClass.objects.get(
                id=class_id,
                section__school=school,
                is_active=True,
            )
        except SchoolClass.DoesNotExist:
            messages.error(request, "The selected class is invalid.")
            return redirect("school_admin_teachers")

        user = User.objects.filter(phone=phone).first()
        teacher_created = False

        if user:
            if user.role != User.Role.TEACHER:
                messages.error(
                    request,
                    "That phone number already belongs to a non-teacher account.",
                )
                return redirect("school_admin_teachers")

            user.first_name = first_name
            user.last_name = surname
            user.is_active = True
            user.save(update_fields=["first_name", "last_name", "is_active"])
        else:
            user = User.objects.create_user(
                phone=phone,
                password=surname,
                role=User.Role.TEACHER,
                first_name=first_name,
                last_name=surname,
            )
            user.must_change_password = True
            user.save(update_fields=["must_change_password"])
            teacher_created = True

        teacher_membership, created = SchoolMembership.objects.get_or_create(
            user=user,
            school=school,
            defaults={
                "staff_role": SchoolMembership.StaffRole.TEACHER,
                "is_active": True,
            },
        )

        if not created:
            teacher_membership.staff_role = SchoolMembership.StaffRole.TEACHER
            teacher_membership.is_active = True
            teacher_membership.save(
                update_fields=["staff_role", "is_active"]
            )

        TeacherClassAssignment.objects.filter(
            school_class=school_class,
            is_active=True,
        ).exclude(
            teacher_membership=teacher_membership,
        ).update(is_active=False)

        assignment, created = TeacherClassAssignment.objects.get_or_create(
            teacher_membership=teacher_membership,
            school_class=school_class,
            defaults={"is_active": True},
        )

        if not created and not assignment.is_active:
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])

        if teacher_created:
            messages.success(
                request,
                f"{first_name} {surname} assigned to "
                f"{school_class.display_name}. Temporary password: {surname}",
            )
        else:
            messages.success(
                request,
                f"{first_name} {surname} assigned to "
                f"{school_class.display_name}.",
            )

        return redirect("school_admin_teachers")

    assignments = TeacherClassAssignment.objects.filter(
        teacher_membership__school=school,
        teacher_membership__staff_role=SchoolMembership.StaffRole.TEACHER,
    ).select_related(
        "teacher_membership__user",
        "school_class",
        "school_class__section",
    ).order_by(
        "school_class__section__order",
        "school_class__order",
        "school_class__name",
    )

    return render(
        request,
        "portal/school_admin_teachers.html",
        {
            "school": school,
            "classes": classes,
            "assignments": assignments,
        },
    )


def teacher_login(request, school_slug=None):
    # School-specific URL is the source of truth.
    if school_slug:
        school = School.objects.filter(
            slug=school_slug,
            is_active=True,
        ).first()

        if not school:
            return redirect("home")

        # Keep this for dashboard/logout compatibility.
        request.session["selected_school_id"] = school.id
        request.session["selected_school_slug"] = school.slug

    else:
        # Compatibility with the older global login URLs.
        selected_school_id = request.session.get("selected_school_id")

        if not selected_school_id:
            return redirect("home")

        school = School.objects.filter(
            id=selected_school_id,
            is_active=True,
        ).first()

        if not school:
            request.session.pop("selected_school_id", None)
            request.session.pop("selected_school_slug", None)
            return redirect("home")

    if request.user.is_authenticated:
        if request.user.role == User.Role.TEACHER:
            membership = SchoolMembership.objects.filter(
                user=request.user,
                school=school,
                staff_role=SchoolMembership.StaffRole.TEACHER,
                is_active=True,
            ).first()

            if membership:
                if request.user.must_change_password:
                    return redirect("teacher_change_password")
                return redirect("teacher_dashboard")

            logout(request)

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")

        candidate = User.objects.filter(
            phone=phone,
            role=User.Role.TEACHER,
        ).first()

        # Clear expired lock automatically.
        if candidate and candidate.locked_until:
            if candidate.locked_until <= timezone.now():
                candidate.failed_login_attempts = 0
                candidate.locked_until = None
                candidate.save(
                    update_fields=[
                        "failed_login_attempts",
                        "locked_until",
                    ]
                )

        # Refuse login while temporarily locked.
        if (
            candidate
            and candidate.locked_until
            and candidate.locked_until > timezone.now()
        ):
            messages.error(
                request,
                "Too many failed login attempts. This account is temporarily locked for 15 minutes.",
            )
            return render(
                request,
                "portal/teacher_login.html",
                {"school": school},
            )

        user = authenticate(
            request,
            phone=phone,
            password=password,
        )

        if user is None:
            if candidate:
                candidate.failed_login_attempts += 1

                if candidate.failed_login_attempts >= 5:
                    candidate.locked_until = timezone.now() + timedelta(minutes=15)
                    candidate.save(
                        update_fields=[
                            "failed_login_attempts",
                            "locked_until",
                        ]
                    )

                    messages.error(
                        request,
                        "Too many failed login attempts. This account has been locked for 15 minutes.",
                    )
                else:
                    candidate.save(
                        update_fields=["failed_login_attempts"]
                    )

                    remaining = 5 - candidate.failed_login_attempts

                    messages.error(
                        request,
                        f"The phone number or password is incorrect. {remaining} login attempt(s) remaining.",
                    )
            else:
                messages.error(
                    request,
                    "The phone number or password is incorrect.",
                )

        elif user.role != User.Role.TEACHER:
            messages.error(
                request,
                "This account is not registered as a teacher.",
            )

        elif not user.is_active:
            messages.error(
                request,
                "This teacher account is currently inactive.",
            )

        else:
            membership = SchoolMembership.objects.filter(
                user=user,
                school=school,
                staff_role=SchoolMembership.StaffRole.TEACHER,
                is_active=True,
            ).first()

            if not membership:
                messages.error(
                    request,
                    "This teacher does not belong to this school.",
                )

            else:
                # Successful login resets failed attempts.
                if user.failed_login_attempts or user.locked_until:
                    user.failed_login_attempts = 0
                    user.locked_until = None
                    user.save(
                        update_fields=[
                            "failed_login_attempts",
                            "locked_until",
                        ]
                    )

                login(request, user)

                request.session["selected_school_id"] = school.id
                request.session["selected_school_slug"] = school.slug

                if user.must_change_password:
                    return redirect("teacher_change_password")

                return redirect("teacher_dashboard")

    return render(
        request,
        "portal/teacher_login.html",
        {
            "school": school,
        },
    )

def teacher_change_password(request):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    if request.method == "POST":
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")

        if len(password1) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        elif password1 != password2:
            messages.error(request, "Passwords do not match.")
        else:
            request.user.set_password(password1)
            request.user.must_change_password = False
            request.user.save(update_fields=["password", "must_change_password"])

            login(request, request.user)

            messages.success(
                request,
                "Your password has been changed successfully.",
            )
            return redirect("teacher_dashboard")

    return render(
        request,
        "portal/teacher_change_password.html",
    )


def teacher_dashboard(request):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    if request.user.must_change_password:
        return redirect("teacher_change_password")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        school_id=selected_school_id,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    assignments = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).select_related(
        "school_class",
        "school_class__section",
    )

    assigned_classes = [
        assignment.school_class
        for assignment in assignments
    ]

    student_count = Student.objects.filter(
        school=membership.school,
        current_class__in=assigned_classes,
        status="ACTIVE",
    ).count()

    return render(
        request,
        "portal/teacher_dashboard.html",
        {
            "school": membership.school,
            "membership": membership,
            "assigned_classes": assigned_classes,
            "student_count": student_count,
        },
    )


def teacher_logout(request):
    school_slug = _logout_school_slug(request)
    logout(request)

    if school_slug:
        return redirect("school_portal", school_slug=school_slug)

    return redirect("home")
def teacher_students(request):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    assignments = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).select_related(
        "school_class",
        "school_class__section",
    )

    assigned_classes = [
        assignment.school_class
        for assignment in assignments
    ]

    students = Student.objects.filter(
        school=membership.school,
        current_class__in=assigned_classes,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
    ).order_by(
        "current_class__order",
        "surname",
        "first_name",
    )

    query = request.GET.get("q", "").strip()

    if query:
        from django.db.models import Q

        students = students.filter(
            Q(first_name__icontains=query)
            | Q(middle_name__icontains=query)
            | Q(surname__icontains=query)
            | Q(admission_number__icontains=query)
        )

    registration_success = request.session.pop(
        "registration_success",
        None,
    )

    return render(
        request,
        "portal/teacher_students.html",
        {
            "school": membership.school,
            "students": students,
            "assigned_classes": assigned_classes,
            "query": query,
            "registration_success": registration_success,
        },
    )



def teacher_add_student(request):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    assignments = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).select_related(
        "school_class",
        "school_class__section",
    )

    assigned_classes = [
        assignment.school_class
        for assignment in assignments
    ]

    school = membership.school

    if request.method == "POST":
        try:
            current_class = SchoolClass.objects.get(
                id=request.POST.get("current_class"),
                section__school=school,
                id__in=[c.id for c in assigned_classes],
            )

            result = register_student_with_parent(
                school=school,
                current_class=current_class,
                student_type=request.POST.get("student_type"),
                surname=request.POST.get("surname", "").strip(),
                first_name=request.POST.get("first_name", "").strip(),
                middle_name=request.POST.get("middle_name", "").strip(),
                gender=request.POST.get("gender"),
                admission_year=int(request.POST.get("admission_year")),
                entry_class_name=request.POST.get("entry_class_name", "").strip(),
                parent_phone=request.POST.get("parent_phone", "").strip(),
                parent_first_name="",
                parent_last_name=request.POST.get("parent_name", "").strip(),
                parent_email=request.POST.get("parent_email", "").strip(),
                relationship=request.POST.get("relationship"),
                previous_school=request.POST.get("previous_school", "").strip(),
            )

            student = result["student"]

            request.session["registration_success"] = {
                "student_name": student.full_name,
                "admission_number": student.admission_number,
                "parent_phone": result["parent_user"].phone,
                "temporary_password": result["temporary_password"],
                "parent_created": result["parent_created"],
            }

            return redirect("teacher_students")

        except Exception as error:
            print(
                "ADD STUDENT ERROR:",
                type(error).__name__,
                str(error),
                flush=True,
            )
            messages.error(
                request,
                f"Student registration failed: {error}",
            )

    return render(
        request,
        "portal/teacher_add_student.html",
        {
            "school": school,
            "assigned_classes": assigned_classes,
            "form_data": request.POST,
        },
    )



def school_admin_subjects(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    subjects = Subject.objects.filter(
        school=school,
    ).prefetch_related(
        "classes",
        "classes__section",
    ).order_by("name")

    query = request.GET.get("q", "").strip()

    if query:
        subjects = subjects.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
        )

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    return render(
        request,
        "portal/school_admin_subjects.html",
        {
            "school": school,
            "subjects": subjects,
            "classes": classes,
            "query": query,
        },
    )



def school_admin_add_subject(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("code", "").strip()
        class_ids = request.POST.getlist("classes")

        try:
            subject = Subject.objects.create(
                school=school,
                name=name,
                code=code,
                is_active=True,
            )

            selected_classes = classes.filter(
                id__in=class_ids
            )

            subject.classes.set(selected_classes)

            messages.success(
                request,
                f"{subject.name} added successfully.",
            )

            return redirect("school_admin_subjects")

        except Exception as error:
            print(
                "ADD STUDENT ERROR:",
                type(error).__name__,
                str(error),
                flush=True,
            )
            messages.error(
                request,
                f"Subject creation failed: {error}",
            )

    return render(
        request,
        "portal/school_admin_add_subject.html",
        {
            "school": school,
            "classes": classes,
        },
    )



def school_admin_generate_subjects(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    primary_subjects = [
        ("Mathematics", "MATH"),
        ("English Language", "ENG"),
        ("Basic Science", "BSC"),
        ("Computer Studies", "ICT"),
        ("Social Studies", "SOC"),
        ("Civic Education", "CIV"),
        ("Agricultural Science", "AGR"),
        ("Physical & Health Education", "PHE"),
        ("Cultural & Creative Arts", "CCA"),
    ]

    junior_subjects = [
        ("Mathematics", "MATH"),
        ("English Language", "ENG"),
        ("Basic Science", "BSC"),
        ("Basic Technology", "BTECH"),
        ("Computer Studies", "ICT"),
        ("Business Studies", "BUS"),
        ("Social Studies", "SOC"),
        ("Civic Education", "CIV"),
        ("Agricultural Science", "AGR"),
        ("Physical & Health Education", "PHE"),
        ("Cultural & Creative Arts", "CCA"),
    ]

    senior_core_subjects = [
        ("Mathematics", "MATH"),
        ("English Language", "ENG"),
        ("Civic Education", "CIV"),
        ("Computer Studies", "ICT"),
    ]

    senior_optional_subjects = [
        ("Biology", "BIO"),
        ("Chemistry", "CHEM"),
        ("Physics", "PHY"),
        ("Further Mathematics", "FMATH"),
        ("Economics", "ECO"),
        ("Government", "GOV"),
        ("Literature in English", "LIT"),
        ("Commerce", "COM"),
        ("Financial Accounting", "ACC"),
        ("Geography", "GEO"),
        ("Agricultural Science", "AGR"),
    ]

    primary_classes = SchoolClass.objects.filter(
        section__school=school,
        section__name="Primary",
        is_active=True,
    )

    junior_classes = SchoolClass.objects.filter(
        section__school=school,
        section__name="Junior Secondary",
        is_active=True,
    )

    senior_classes = SchoolClass.objects.filter(
        section__school=school,
        section__name="Senior Secondary",
        is_active=True,
    )

    created_count = 0

    def create_and_assign(subject_list, classes):
        nonlocal created_count

        for subject_name, subject_code in subject_list:
            subject, created = Subject.objects.get_or_create(
                school=school,
                name=subject_name,
                defaults={
                    "code": subject_code,
                    "is_active": True,
                },
            )

            if not subject.code:
                subject.code = subject_code
                subject.save(update_fields=["code"])

            subject.classes.add(*classes)

            if created:
                created_count += 1

    create_and_assign(primary_subjects, primary_classes)
    create_and_assign(junior_subjects, junior_classes)
    create_and_assign(senior_core_subjects, senior_classes)

    # Create senior elective subjects without forcing them
    # onto every Senior Secondary class.
    for subject_name, subject_code in senior_optional_subjects:
        subject, created = Subject.objects.get_or_create(
            school=school,
            name=subject_name,
            defaults={
                "code": subject_code,
                "is_active": True,
            },
        )

        if not subject.code:
            subject.code = subject_code
            subject.save(update_fields=["code"])

        if created:
            created_count += 1

    messages.success(
        request,
        f"Default subjects generated successfully. "
        f"{created_count} new subject(s) created.",
    )

    return redirect("school_admin_subjects")



def school_admin_edit_subject(request, subject_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    subject = Subject.objects.filter(
        id=subject_id,
        school=school,
    ).first()

    if not subject:
        messages.error(
            request,
            "Subject not found.",
        )
        return redirect("school_admin_subjects")

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    if request.method == "POST":
        try:
            subject.name = request.POST.get("name", "").strip()
            subject.code = request.POST.get("code", "").strip()

            subject.senior_science = (
                request.POST.get("senior_science") == "on"
            )
            subject.senior_commercial = (
                request.POST.get("senior_commercial") == "on"
            )
            subject.senior_arts = (
                request.POST.get("senior_arts") == "on"
            )

            subject.is_active = request.POST.get("is_active") == "on"
            subject.save()

            class_ids = request.POST.getlist("classes")

            selected_classes = classes.filter(
                id__in=class_ids
            )

            subject.classes.set(selected_classes)

            messages.success(
                request,
                f"{subject.name} updated successfully.",
            )

            return redirect("school_admin_subjects")

        except Exception as error:
            print(
                "ADD STUDENT ERROR:",
                type(error).__name__,
                str(error),
                flush=True,
            )
            messages.error(
                request,
                f"Subject update failed: {error}",
            )

    return render(
        request,
        "portal/school_admin_edit_subject.html",
        {
            "school": school,
            "subject": subject,
            "classes": classes,
        },
    )



def school_admin_set_student_department(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    student = Student.objects.filter(
        uuid=student_uuid,
        school=membership.school,
    ).select_related(
        "current_class",
        "current_class__section",
    ).first()

    if not student:
        messages.error(
            request,
            "Student not found.",
        )
        return redirect("school_admin_students")

    if student.current_class.section.academic_level != "SECONDARY":
        messages.error(
            request,
            "Department is only used for Senior Secondary students.",
        )
        return redirect("school_admin_students")

    if student.current_class.section.name != "Senior Secondary":
        messages.error(
            request,
            "Department is only used for Senior Secondary students.",
        )
        return redirect("school_admin_students")

    if request.method == "POST":
        department = request.POST.get("department", "").strip()

        valid_departments = {
            Student.Department.SCIENCE,
            Student.Department.COMMERCIAL,
            Student.Department.ARTS,
        }

        if department not in valid_departments:
            messages.error(
                request,
                "Please select a valid department.",
            )
        else:
            student.department = department
            student.save(update_fields=["department"])

            messages.success(
                request,
                f"{student.full_name} department updated successfully.",
            )

            return redirect("school_admin_students")

    return render(
        request,
        "portal/school_admin_set_department.html",
        {
            "student": student,
            "school": membership.school,
        },
    )



def teacher_set_student_department(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    assigned_class_ids = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).values_list(
        "school_class_id",
        flat=True,
    )

    student = Student.objects.filter(
        uuid=student_uuid,
        school=membership.school,
        current_class_id__in=assigned_class_ids,
    ).select_related(
        "current_class",
        "current_class__section",
    ).first()

    if not student:
        messages.error(
            request,
            "Student not found in your assigned classes.",
        )
        return redirect("teacher_students")

    if student.current_class.section.name != "Senior Secondary":
        messages.error(
            request,
            "Department is only used for Senior Secondary students.",
        )
        return redirect("teacher_students")

    if request.method == "POST":
        department = request.POST.get("department", "").strip()

        valid_departments = {
            Student.Department.SCIENCE,
            Student.Department.COMMERCIAL,
            Student.Department.ARTS,
        }

        if department not in valid_departments:
            messages.error(
                request,
                "Please select a valid department.",
            )
        else:
            student.department = department
            student.save(update_fields=["department"])

            messages.success(
                request,
                f"{student.full_name} department updated successfully.",
            )

            return redirect("teacher_students")

    return render(
        request,
        "portal/teacher_set_department.html",
        {
            "student": student,
            "school": membership.school,
        },
    )



def school_admin_results(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    class_statuses = []

    if session and term:
        for school_class in classes:
            class_statuses.append(
                get_class_result_completeness(
                    school_class,
                    session,
                    term,
                )
            )

    # Global school result search.
    search_query = request.GET.get("q", "").strip()
    search_results = []

    if search_query:
        from django.db.models import Q

        search_results = (
            Student.objects.filter(
                school=school,
                status="ACTIVE",
            )
            .filter(
                Q(first_name__icontains=search_query)
                | Q(surname__icontains=search_query)
                | Q(admission_number__icontains=search_query)
                | Q(parent_links__parent__user__phone__icontains=search_query)
            )
            .select_related(
                "current_class",
                "current_class__section",
            )
            .distinct()
            .order_by("surname", "first_name")
        )

    return render(
        request,
        "portal/school_admin_results.html",
        {
            "school": school,
            "session": session,
            "term": term,
            "class_statuses": class_statuses,
            "search_query": search_query,
            "search_results": search_results,
        },
    )



def school_admin_class_result_details(request, class_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    school_class = SchoolClass.objects.filter(
        id=class_id,
        section__school=school,
        is_active=True,
    ).select_related("section").first()

    if not school_class:
        messages.error(
            request,
            "Class not found.",
        )
        return redirect("school_admin_results")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect("school_admin_results")

    class_status = get_class_result_completeness(
        school_class,
        session,
        term,
    )

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=school_class,
        session=session,
        term=term,
    ).first()

    return render(
        request,
        "portal/school_admin_class_result_details.html",
        {
            "school": school,
            "school_class": school_class,
            "session": session,
            "term": term,
            "class_status": class_status,
            "publication": publication,
        },
    )



def teacher_results(request):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    school = membership.school

    assignments = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).select_related(
        "school_class",
        "school_class__section",
    )

    assigned_classes = [
        assignment.school_class
        for assignment in assignments
    ]

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    class_statuses = []

    if session and term:
        for school_class in assigned_classes:
            class_statuses.append(
                get_class_result_completeness(
                    school_class,
                    session,
                    term,
                )
            )

    return render(
        request,
        "portal/teacher_results.html",
        {
            "school": school,
            "session": session,
            "term": term,
            "class_statuses": class_statuses,
        },
    )



def teacher_class_results(request, class_id):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    assignment = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        school_class_id=class_id,
        is_active=True,
    ).select_related(
        "school_class",
        "school_class__section",
    ).first()

    if not assignment:
        messages.error(
            request,
            "You are not assigned to this class.",
        )
        return redirect("teacher_results")

    school_class = assignment.school_class
    school = membership.school

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect("teacher_results")

    students = Student.objects.filter(
        school=school,
        current_class=school_class,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
    ).order_by(
        "surname",
        "first_name",
    )

    student_rows = []

    for student in students:
        subjects = get_student_required_subjects(student)

        completed_count = StudentResult.objects.filter(
            student=student,
            session=session,
            term=term,
            subject__in=subjects,
        ).count()

        student_rows.append(
            {
                "student": student,
                "subject_count": subjects.count(),
                "completed_count": completed_count,
                "department_missing": (
                    school_class.section.name == "Senior Secondary"
                    and not student.department
                ),
            }
        )

    return render(
        request,
        "portal/teacher_class_results.html",
        {
            "school": school,
            "school_class": school_class,
            "session": session,
            "term": term,
            "student_rows": student_rows,
        },
    )






def teacher_student_term_report(request, student_uuid, term_id):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        return redirect("home")

    school = membership.school

    assigned_class_ids = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).values_list("school_class_id", flat=True)

    student = Student.objects.filter(
        uuid=student_uuid,
        school=school,
        current_class_id__in=assigned_class_ids,
        status="ACTIVE",
    ).select_related("current_class").first()

    if not student:
        messages.error(request, "This student is no longer in your assigned class.")
        return redirect("teacher_results")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    if not session:
        return redirect("teacher_results")

    summary = StudentTermSummary.objects.filter(
        school=school,
        student=student,
        school_class=student.current_class,
        session=session,
        term_id=term_id,
    ).select_related(
        "school_class",
        "session",
        "term",
    ).first()

    if not summary:
        messages.error(request, "That term report is not available.")
        return redirect(
            "teacher_student_term_reports",
            student_uuid=student.uuid,
        )

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=summary.school_class,
        session=session,
        term=summary.term,
        is_published=True,
    ).first()

    if not publication:
        messages.error(request, "That term result has not been published.")
        return redirect(
            "teacher_student_term_reports",
            student_uuid=student.uuid,
        )

    results = StudentResult.objects.filter(
        student=student,
        school_class=summary.school_class,
        session=session,
        term=summary.term,
    ).select_related("subject").order_by("subject__name")

    class_size = StudentTermSummary.objects.filter(
        school=school,
        school_class=summary.school_class,
        session=session,
        term=summary.term,
    ).count()

    remarks = get_result_remarks(summary.average_score)

    annual_summary = None
    if summary.term.name == "THIRD":
        annual_summary = StudentAnnualSummary.objects.filter(
            student=student,
            school_class=summary.school_class,
            session=session,
        ).first()

    return render(
        request,
        "portal/parent_student_result.html",
        {
            "school": school,
            "student": student,
            "session": session,
            "term": summary.term,
            "results": results,
            "summary": summary,
            "class_size": class_size,
            "publication": publication,
            "annual_summary": annual_summary,
            "teacher_remark": remarks["teacher_remark"],
            "management_remark": remarks["management_remark"],
        },
    )


def teacher_student_term_reports(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        return redirect("home")

    school = membership.school

    assigned_class_ids = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).values_list("school_class_id", flat=True)

    # Student must STILL be in one of this teacher's assigned classes.
    student = Student.objects.filter(
        uuid=student_uuid,
        school=school,
        current_class_id__in=assigned_class_ids,
        status="ACTIVE",
    ).select_related("current_class").first()

    if not student:
        messages.error(request, "This student is no longer in your assigned class.")
        return redirect("teacher_results")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    if not session:
        messages.error(request, "No current academic session was found.")
        return redirect("teacher_results")

    summaries = (
        StudentTermSummary.objects.filter(
            school=school,
            student=student,
            school_class=student.current_class,
            session=session,
        )
        .select_related("session", "term", "school_class")
    )

    reports = []

    for summary in summaries:
        published = ClassResultPublication.objects.filter(
            school=school,
            school_class=summary.school_class,
            session=summary.session,
            term=summary.term,
            is_published=True,
        ).exists()

        if published:
            reports.append(summary)

    term_order = {"FIRST": 1, "SECOND": 2, "THIRD": 3}
    reports.sort(key=lambda x: term_order.get(x.term.name, 99))

    return render(
        request,
        "portal/teacher_student_term_reports.html",
        {
            "school": school,
            "student": student,
            "session": session,
            "reports": reports,
        },
    )


def teacher_student_report(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active teacher membership was found.")
        return redirect("home")

    assigned_class_ids = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).values_list("school_class_id", flat=True)

    student = Student.objects.filter(
        uuid=student_uuid,
        school=membership.school,
        current_class_id__in=assigned_class_ids,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
    ).first()

    if not student:
        messages.error(request, "Student not found in your assigned classes.")
        return redirect("teacher_results")

    school = membership.school

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(request, "No current academic session or term was found.")
        return redirect("teacher_results")

    summary = StudentTermSummary.objects.filter(
        school=school,
        student=student,
        session=session,
        term=term,
    ).select_related("school_class").first()

    if not summary:
        messages.error(request, "Student result summary is not available.")
        return redirect(
            "teacher_class_results",
            class_id=student.current_class.id,
        )

    result_class = summary.school_class

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
        is_published=True,
    ).first()

    if not publication:
        messages.error(request, "This result has not been published yet.")
        return redirect(
            "teacher_class_results",
            class_id=result_class.id,
        )

    results = StudentResult.objects.filter(
        student=student,
        school_class=result_class,
        session=session,
        term=term,
    ).select_related("subject").order_by("subject__name")

    class_size = StudentTermSummary.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
    ).count()

    remarks = get_result_remarks(summary.average_score)

    annual_summary = None
    if term.name == "THIRD":
        annual_summary = StudentAnnualSummary.objects.filter(
            student=student,
            school_class=result_class,
            session=session,
        ).first()

    return render(
        request,
        "portal/parent_student_result.html",
        {
            "school": school,
            "student": student,
            "session": session,
            "term": term,
            "results": results,
            "summary": summary,
            "class_size": class_size,
            "publication": publication,
            "annual_summary": annual_summary,
            "teacher_remark": remarks["teacher_remark"],
            "management_remark": remarks["management_remark"],
        },
    )



def teacher_student_result_entry(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("teacher_login")

    if request.user.role != User.Role.TEACHER:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.TEACHER,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active teacher membership was found.",
        )
        return redirect("home")

    assigned_class_ids = TeacherClassAssignment.objects.filter(
        teacher_membership=membership,
        is_active=True,
    ).values_list(
        "school_class_id",
        flat=True,
    )

    student = Student.objects.filter(
        uuid=student_uuid,
        school=membership.school,
        current_class_id__in=assigned_class_ids,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
    ).first()

    if not student:
        messages.error(
            request,
            "Student not found in your assigned classes.",
        )
        return redirect("teacher_results")

    school = membership.school
    school_class = student.current_class

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect(
            "teacher_class_results",
            class_id=school_class.id,
        )

    if (
        school_class.section.name == "Senior Secondary"
        and not student.department
    ):
        messages.error(
            request,
            "Assign the student's department before entering results.",
        )
        return redirect(
            "teacher_set_student_department",
            student_uuid=student.uuid,
        )

    subjects = get_student_required_subjects(student).order_by("name")

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=school_class,
        session=session,
        term=term,
        is_published=True,
    ).first()

    if request.method == "POST" and publication:
        messages.error(
            request,
            "This result has already been published and can no longer be edited by a teacher.",
        )
        return redirect(
            "teacher_class_results",
            class_id=school_class.id,
        )

    if request.method == "POST":
        errors = []

        for subject in subjects:
            ca_raw = request.POST.get(
                f"ca_{subject.id}",
                "",
            ).strip()

            exam_raw = request.POST.get(
                f"exam_{subject.id}",
                "",
            ).strip()

            if ca_raw == "" and exam_raw == "":
                continue

            if ca_raw == "" or exam_raw == "":
                errors.append(
                    f"{subject.name}: enter both CA and Exam scores."
                )
                continue

            try:
                ca_score = Decimal(ca_raw)
                exam_score = Decimal(exam_raw)
            except InvalidOperation:
                errors.append(
                    f"{subject.name}: scores must be valid numbers."
                )
                continue

            result, created = StudentResult.objects.get_or_create(
                school=school,
                student=student,
                school_class=school_class,
                subject=subject,
                session=session,
                term=term,
                defaults={
                    "ca_score": ca_score,
                    "exam_score": exam_score,
                },
            )

        old_ca_score = None if created else result.ca_score
        old_exam_score = None if created else result.exam_score

        if not created:
            result.ca_score = ca_score
            result.exam_score = exam_score

        try:
            result.save()

            if created:
                ResultAuditLog.objects.create(
                    result=result,
                    changed_by=request.user,
                    action=ResultAuditLog.Action.CREATED,
                    old_ca_score=None,
                    old_exam_score=None,
                    new_ca_score=result.ca_score,
                    new_exam_score=result.exam_score,
                )

            elif old_ca_score != result.ca_score or old_exam_score != result.exam_score:
                ResultAuditLog.objects.create(
                    result=result,
                    changed_by=request.user,
                    action=ResultAuditLog.Action.UPDATED,
                    old_ca_score=old_ca_score,
                    old_exam_score=old_exam_score,
                    new_ca_score=result.ca_score,
                    new_exam_score=result.exam_score,
                )

        except Exception as error:
            errors.append(
                f"{subject.name}: {error}"
            )

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            update_student_term_summary(
                student,
                session,
                term,
            )

            update_class_positions(
                school_class,
                session,
                term,
            )

            if term.name == "THIRD":
                update_student_annual_summary(
                    student,
                    session,
                )

                update_class_annual_positions(
                    school_class,
                    session,
                )

            messages.success(
                request,
                f"{student.full_name} result saved successfully.",
            )

            return redirect(
                "teacher_class_results",
                class_id=school_class.id,
            )

    existing_results = {
        result.subject_id: result
        for result in StudentResult.objects.filter(
            student=student,
            session=session,
            term=term,
            subject__in=subjects,
        )
    }

    subject_rows = []

    for subject in subjects:
        subject_rows.append(
            {
                "subject": subject,
                "result": existing_results.get(subject.id),
            }
        )

    if school_class.section.academic_level == "PRIMARY":
        ca_max = 40
        exam_max = 60
    else:
        ca_max = 30
        exam_max = 70

    return render(
        request,
        "portal/teacher_student_result_entry.html",
        {
            "school": school,
            "student": student,
            "school_class": school_class,
            "session": session,
            "term": term,
            "subject_rows": subject_rows,
            "ca_max": ca_max,
            "exam_max": exam_max,
        },
    )



def school_admin_publish_class_results(request, class_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    if request.method != "POST":
        return redirect(
            "school_admin_class_result_details",
            class_id=class_id,
        )

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    school_class = SchoolClass.objects.filter(
        id=class_id,
        section__school=school,
        is_active=True,
    ).select_related("section").first()

    if not school_class:
        messages.error(
            request,
            "Class not found.",
        )
        return redirect("school_admin_results")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect("school_admin_results")

    class_status = get_class_result_completeness(
        school_class,
        session,
        term,
    )

    if not class_status["is_ready"]:
        messages.error(
            request,
            "This class is not complete yet. "
            "All students must have complete results before publishing.",
        )

        return redirect(
            "school_admin_class_result_details",
            class_id=school_class.id,
        )

    update_class_positions(
        school_class,
        session,
        term,
    )

    publication, _ = ClassResultPublication.objects.get_or_create(
        school=school,
        school_class=school_class,
        session=session,
        term=term,
    )

    resumption_date_raw = request.POST.get(
        "next_term_resumption_date",
        "",
    ).strip()

    resumption_date = parse_date(resumption_date_raw)

    if not resumption_date:
        messages.error(
            request,
            "Please select the next term resumption date before publishing.",
        )
        return redirect(
            "school_admin_class_result_details",
            class_id=school_class.id,
        )

    publication.is_published = True
    publication.published_at = timezone.now()
    publication.published_by = request.user
    publication.next_term_resumption_date = resumption_date

    publication.save(
        update_fields=[
            "is_published",
            "published_at",
            "published_by",
            "next_term_resumption_date",
            "updated_at",
        ]
    )

    messages.success(
        request,
        f"{school_class.display_name} results published successfully.",
    )

    return redirect(
        "school_admin_class_result_details",
        class_id=school_class.id,
    )



def parent_student_result(
    request,
    student_uuid,
    session_id=None,
    term_id=None,
):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    if request.user.role != User.Role.PARENT:
        return redirect("home")

    from students.models import StudentParent

    parent_profile = getattr(
        request.user,
        "parent_profile",
        None,
    )

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
        "student__current_class__section",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student
    school = student.school

    if session_id is not None and term_id is not None:
        session = school.academic_sessions.filter(
            id=session_id,
            is_active=True,
        ).first()

        term = school.academic_terms.filter(
            id=term_id,
            session=session,
            is_active=True,
        ).first() if session else None
    else:
        session = school.academic_sessions.filter(
            is_current=True,
            is_active=True,
        ).first()

        term = school.academic_terms.filter(
            session=session,
            is_current=True,
            is_active=True,
        ).first() if session else None

    if not session or not term:
        messages.error(
            request,
            "The requested academic session or term was not found.",
        )
        return redirect("parent_dashboard")

    # The saved term summary is the permanent record of which
    # class this student belonged to for this session and term.
    summary = StudentTermSummary.objects.filter(
        school=school,
        student=student,
        session=session,
        term=term,
    ).select_related(
        "school_class",
    ).first()

    if not summary:
        messages.error(
            request,
            "Student result summary is not available.",
        )
        return redirect("parent_dashboard")

    result_class = summary.school_class

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
        is_published=True,
    ).first()

    if not publication:
        messages.error(
            request,
            "This result has not been published yet.",
        )
        return redirect("parent_dashboard")

    results = StudentResult.objects.filter(
        student=student,
        school_class=result_class,
        session=session,
        term=term,
    ).select_related(
        "subject",
    ).order_by(
        "subject__name",
    )

    class_size = StudentTermSummary.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
    ).count()

    remarks = get_result_remarks(
        summary.average_score
    )

    annual_summary = None

    if term.name == "THIRD":
        annual_summary = StudentAnnualSummary.objects.filter(
            student=student,
            school_class=result_class,
            session=session,
        ).first()

    return render(
        request,
        "portal/parent_student_result.html",
        {
            "school": school,
            "student": student,
            "session": session,
            "term": term,
            "results": results,
            "summary": summary,
            "class_size": class_size,
            "publication": publication,
            "annual_summary": annual_summary,
            "teacher_remark": remarks["teacher_remark"],
            "management_remark": remarks["management_remark"],
        },
    )



def school_admin_update_resumption_date(request, class_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    if request.method != "POST":
        return redirect(
            "school_admin_class_result_details",
            class_id=class_id,
        )

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        return redirect("home")

    school = membership.school

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class_id=class_id,
        is_published=True,
    ).order_by("-published_at").first()

    if not publication:
        messages.error(
            request,
            "No published result was found for this class.",
        )
        return redirect(
            "school_admin_class_result_details",
            class_id=class_id,
        )

    resumption_date = parse_date(
        request.POST.get(
            "next_term_resumption_date",
            "",
        ).strip()
    )

    if not resumption_date:
        messages.error(
            request,
            "Please select a valid resumption date.",
        )
        return redirect(
            "school_admin_class_result_details",
            class_id=class_id,
        )

    publication.next_term_resumption_date = resumption_date
    publication.save(
        update_fields=[
            "next_term_resumption_date",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "Next term resumption date updated successfully.",
    )

    return redirect(
        "school_admin_class_result_details",
        class_id=class_id,
    )



def school_admin_academic_year(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    if not session:
        return render(
            request,
            "portal/school_admin_academic_year.html",
            {
                "school": school,
                "session": None,
                "terms": [],
                "current_term": None,
                "next_term_name": None,
            },
        )

    terms = school.academic_terms.filter(
        session=session,
        is_active=True,
    )

    current_term = terms.filter(
        is_current=True,
    ).first()

    term_order = {
        "FIRST": 1,
        "SECOND": 2,
        "THIRD": 3,
    }

    next_term_name = None

    if current_term:
        if current_term.name == "FIRST":
            next_term_name = "SECOND"
        elif current_term.name == "SECOND":
            next_term_name = "THIRD"

    if request.method == "POST":
        requested_term = request.POST.get(
            "term",
            "",
        ).strip()

        if not current_term:
            messages.error(
                request,
                "No current term was found.",
            )
            return redirect("school_admin_academic_year")

        if not next_term_name:
            messages.error(
                request,
                "Third Term is already the current term.",
            )
            return redirect("school_admin_academic_year")

        if requested_term != next_term_name:
            messages.error(
                request,
                "Terms must be started in order.",
            )
            return redirect("school_admin_academic_year")

        active_classes = SchoolClass.objects.filter(
            section__school=school,
            is_active=True,
        )

        unpublished_classes = []

        for school_class in active_classes:
            published = ClassResultPublication.objects.filter(
                school=school,
                school_class=school_class,
                session=session,
                term=current_term,
                is_published=True,
            ).exists()

            if not published:
                unpublished_classes.append(
                    school_class.display_name
                )

        if unpublished_classes:
            messages.error(
                request,
                "You cannot start the next term yet. "
                "Some class results are still unpublished: "
                + ", ".join(unpublished_classes),
            )
            return redirect("school_admin_academic_year")

        next_term, _ = AcademicTerm.objects.get_or_create(
            school=school,
            session=session,
            name=next_term_name,
            defaults={
                "is_active": True,
                "is_current": False,
            },
        )

        with transaction.atomic():
            AcademicTerm.objects.filter(
                school=school,
                session=session,
                is_current=True,
            ).update(
                is_current=False,
            )

            next_term.is_active = True
            next_term.is_current = True
            next_term.save(
                update_fields=[
                    "is_active",
                    "is_current",
                ]
            )

        messages.success(
            request,
            f"{next_term.get_name_display()} started successfully.",
        )

        return redirect("school_admin_academic_year")

    return render(
        request,
        "portal/school_admin_academic_year.html",
        {
            "school": school,
            "session": session,
            "terms": terms,
            "current_term": current_term,
            "next_term_name": next_term_name,
        },
    )



def school_admin_promotions(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    if not session:
        messages.error(
            request,
            "No current academic session was found.",
        )
        return redirect("school_admin_dashboard")

    summaries = (
        StudentAnnualSummary.objects.filter(
            school=school,
            session=session,
        )
        .select_related(
            "student",
            "school_class",
            "school_class__section",
        )
        .order_by(
            "school_class__section__order",
            "school_class__order",
            "annual_position",
            "student__surname",
            "student__first_name",
        )
    )

    rows = []

    for summary in summaries:
        next_class = get_next_school_class(
            summary.student
        )

        rows.append(
            {
                "summary": summary,
                "next_class": next_class,
            }
        )

    return render(
        request,
        "portal/school_admin_promotions.html",
        {
            "school": school,
            "session": session,
            "rows": rows,
        },
    )



def school_admin_set_promotion_decision(request, summary_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    if request.method != "POST":
        return redirect("school_admin_promotions")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        return redirect("home")

    summary = StudentAnnualSummary.objects.filter(
        id=summary_id,
        school=membership.school,
    ).select_related(
        "student",
    ).first()

    if not summary:
        messages.error(
            request,
            "Annual summary not found.",
        )
        return redirect("school_admin_promotions")

    decision = request.POST.get(
        "decision",
        "",
    ).strip().upper()

    next_class = get_next_school_class(
        summary.student
    )

    allowed = {"REPEAT"}

    if next_class:
        allowed.add("PROMOTED")
    else:
        allowed.add("GRADUATED")

    if decision not in allowed:
        messages.error(
            request,
            "Invalid promotion decision.",
        )
        return redirect("school_admin_promotions")

    summary.promotion_status = decision
    summary.save(
        update_fields=[
            "promotion_status",
            "updated_at",
        ]
    )

    messages.success(
        request,
        f"{summary.student.full_name} marked as "
        f"{summary.get_promotion_status_display()}.",
    )

    return redirect("school_admin_promotions")



def school_admin_start_new_session(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    if request.method != "POST":
        return redirect("school_admin_academic_year")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    current_session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    current_term = school.academic_terms.filter(
        session=current_session,
        is_current=True,
        is_active=True,
    ).first() if current_session else None

    if not current_session or not current_term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect("school_admin_academic_year")

    if current_term.name != "THIRD":
        messages.error(
            request,
            "A new academic session can only start after Third Term.",
        )
        return redirect("school_admin_academic_year")

    active_classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    )

    unpublished_third_term_classes = []

    for school_class in active_classes:
        published = ClassResultPublication.objects.filter(
            school=school,
            school_class=school_class,
            session=current_session,
            term=current_term,
            is_published=True,
        ).exists()

        if not published:
            unpublished_third_term_classes.append(
                school_class.display_name
            )

    if unpublished_third_term_classes:
        messages.error(
            request,
            "You cannot start a new academic session yet. "
            "Some Third Term results are still unpublished: "
            + ", ".join(unpublished_third_term_classes),
        )
        return redirect("school_admin_academic_year")

    summaries = StudentAnnualSummary.objects.filter(
        school=school,
        session=current_session,
    ).select_related(
        "student",
        "student__current_class",
        "student__current_class__section",
    )

    pending_count = summaries.filter(
        promotion_status="PENDING",
    ).count()

    if pending_count:
        messages.error(
            request,
            f"{pending_count} student promotion decision(s) are still pending.",
        )
        return redirect("school_admin_promotions")

    new_session_name = request.POST.get(
        "new_session_name",
        "",
    ).strip()

    if not new_session_name:
        messages.error(
            request,
            "Please enter the new academic session name.",
        )
        return redirect("school_admin_academic_year")

    import re

    session_match = re.fullmatch(r"(\d{4})/(\d{4})", new_session_name)

    if not session_match:
        messages.error(
            request,
            "Academic session must be written like 2027/2028.",
        )
        return redirect("school_admin_academic_year")

    start_year = int(session_match.group(1))
    end_year = int(session_match.group(2))

    if end_year != start_year + 1:
        messages.error(
            request,
            "Academic session must contain consecutive years, for example 2027/2028.",
        )
        return redirect("school_admin_academic_year")

    current_match = re.fullmatch(r"(\d{4})/(\d{4})", current_session.name)

    if current_match:
        expected_start = int(current_match.group(2))
        expected_session = f"{expected_start}/{expected_start + 1}"

        if new_session_name != expected_session:
            messages.error(
                request,
                f"The next academic session must be {expected_session}.",
            )
            return redirect("school_admin_academic_year")

    if AcademicSession.objects.filter(
        school=school,
        name=new_session_name,
    ).exists():
        messages.error(
            request,
            "That academic session already exists.",
        )
        return redirect("school_admin_academic_year")

    with transaction.atomic():
        for summary in summaries:
            student = summary.student

            if summary.promotion_status == "PROMOTED":
                next_class = get_next_school_class(student)

                if not next_class:
                    raise ValueError(
                        f"No next class found for {student.full_name}."
                    )

                student.current_class = next_class
                student.status = Student.Status.ACTIVE
                student.save(
                    update_fields=[
                        "current_class",
                        "status",
                        "updated_at",
                    ]
                )

            elif summary.promotion_status == "REPEAT":
                student.status = Student.Status.ACTIVE
                student.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

            elif summary.promotion_status == "GRADUATED":
                student.status = Student.Status.GRADUATED
                student.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

        AcademicTerm.objects.filter(
            school=school,
            session=current_session,
            is_current=True,
        ).update(
            is_current=False,
        )

        current_session.is_current = False
        current_session.save(
            update_fields=["is_current"]
        )

        new_session = AcademicSession.objects.create(
            school=school,
            name=new_session_name,
            is_current=True,
            is_active=True,
        )

        for term_name in ("FIRST", "SECOND", "THIRD"):
            AcademicTerm.objects.create(
                school=school,
                session=new_session,
                name=term_name,
                is_current=(term_name == "FIRST"),
                is_active=True,
            )

    messages.success(
        request,
        f"{new_session_name} started successfully. "
        "First Term is now current.",
    )

    return redirect("school_admin_academic_year")



def parent_student_result_history(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("parent_login")

    if request.user.role != User.Role.PARENT:
        return redirect("home")

    from students.models import StudentParent

    parent_profile = getattr(
        request.user,
        "parent_profile",
        None,
    )

    if not parent_profile:
        return redirect("parent_login")

    selected_school_id = request.session.get("selected_school_id")

    if not selected_school_id:
        return redirect("parent_dashboard")

    link = StudentParent.objects.filter(
        parent=parent_profile,
        student__uuid=student_uuid,
        student__school_id=selected_school_id,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    ).first()

    if not link:
        return redirect("parent_dashboard")

    student = link.student
    school = student.school

    # Build permanent result history from the student's saved
    # term summaries instead of student.current_class.
    term_summaries = (
        StudentTermSummary.objects.filter(
            school=school,
            student=student,
        )
        .select_related(
            "school_class",
            "session",
            "term",
        )
    )

    publication_ids = []

    for summary in term_summaries:
        publication_id = (
            ClassResultPublication.objects.filter(
                school=school,
                school_class=summary.school_class,
                session=summary.session,
                term=summary.term,
                is_published=True,
            )
            .values_list("id", flat=True)
            .first()
        )

        if publication_id:
            publication_ids.append(publication_id)

    publications = (
        ClassResultPublication.objects.filter(
            id__in=set(publication_ids),
        )
        .select_related(
            "school_class",
            "session",
            "term",
        )
        .order_by(
            "-session__name",
            "term__name",
        )
    )

    return render(
        request,
        "portal/parent_student_result_history.html",
        {
            "school": school,
            "student": student,
            "publications": publications,
        },
    )



def school_admin_student_historical_report(request, student_uuid, session_id, term_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school

    student = Student.objects.filter(
        uuid=student_uuid,
        school=school,
    ).first()

    if not student:
        messages.error(request, "Student was not found.")
        return redirect("school_admin_results")

    summary = StudentTermSummary.objects.filter(
        school=school,
        student=student,
        session_id=session_id,
        term_id=term_id,
    ).select_related(
        "school_class",
        "session",
        "term",
    ).first()

    if not summary:
        messages.error(request, "Result summary was not found.")
        return redirect(
            "school_admin_student_result_history",
            student_uuid=student.uuid,
        )

    session = summary.session
    term = summary.term
    result_class = summary.school_class

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
        is_published=True,
    ).first()

    if not publication:
        messages.error(request, "This result has not been published.")
        return redirect(
            "school_admin_student_result_history",
            student_uuid=student.uuid,
        )

    results = StudentResult.objects.filter(
        student=student,
        school_class=result_class,
        session=session,
        term=term,
    ).select_related("subject").order_by("subject__name")

    class_size = StudentTermSummary.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
    ).count()

    remarks = get_result_remarks(summary.average_score)

    annual_summary = None
    if term.name == "THIRD":
        annual_summary = StudentAnnualSummary.objects.filter(
            student=student,
            school_class=result_class,
            session=session,
        ).first()

    return render(
        request,
        "portal/parent_student_result.html",
        {
            "school": school,
            "student": student,
            "session": session,
            "term": term,
            "results": results,
            "summary": summary,
            "class_size": class_size,
            "publication": publication,
            "annual_summary": annual_summary,
            "teacher_remark": remarks["teacher_remark"],
            "management_remark": remarks["management_remark"],
        },
    )


def school_admin_student_result_history(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school

    student = Student.objects.filter(
        uuid=student_uuid,
        school=school,
    ).first()

    if not student:
        messages.error(request, "Student was not found.")
        return redirect("school_admin_results")

    term_summaries = (
        StudentTermSummary.objects.filter(
            school=school,
            student=student,
        )
        .select_related(
            "school_class",
            "session",
            "term",
        )
    )

    publication_ids = []

    for summary in term_summaries:
        publication_id = (
            ClassResultPublication.objects.filter(
                school=school,
                school_class=summary.school_class,
                session=summary.session,
                term=summary.term,
                is_published=True,
            )
            .values_list("id", flat=True)
            .first()
        )

        if publication_id:
            publication_ids.append(publication_id)

    publications = (
        ClassResultPublication.objects.filter(
            id__in=set(publication_ids),
        )
        .select_related(
            "school_class",
            "session",
            "term",
        )
        .order_by(
            "-session__name",
            "term__name",
        )
    )

    return render(
        request,
        "portal/school_admin_student_result_history.html",
        {
            "school": school,
            "student": student,
            "publications": publications,
        },
    )


def school_admin_edit_teacher(request, membership_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    admin_membership = request.user.school_memberships.filter(
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not admin_membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = admin_membership.school

    teacher_membership = SchoolMembership.objects.filter(
        id=membership_id,
        school=school,
        staff_role=SchoolMembership.StaffRole.TEACHER,
    ).select_related("user", "school").first()

    if not teacher_membership:
        messages.error(request, "Teacher account was not found.")
        return redirect("school_admin_teachers")

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    current_assignment = TeacherClassAssignment.objects.filter(
        teacher_membership=teacher_membership,
        is_active=True,
    ).select_related("school_class").first()

    if request.method == "POST":
        first_name = request.POST.get("first_name", "").strip()
        surname = request.POST.get("surname", "").strip()
        phone = request.POST.get("phone", "").strip()
        class_id = request.POST.get("school_class", "").strip()

        if not first_name or not surname or not phone or not class_id:
            messages.error(request, "Please complete all teacher fields.")
            return redirect(
                "school_admin_edit_teacher",
                membership_id=teacher_membership.id,
            )

        try:
            school_class = SchoolClass.objects.get(
                id=class_id,
                section__school=school,
                is_active=True,
            )
        except SchoolClass.DoesNotExist:
            messages.error(request, "The selected class is invalid.")
            return redirect(
                "school_admin_edit_teacher",
                membership_id=teacher_membership.id,
            )

        duplicate_phone = User.objects.filter(phone=phone).exclude(
            id=teacher_membership.user_id
        ).exists()

        if duplicate_phone:
            messages.error(request, "That phone number is already in use.")
            return redirect(
                "school_admin_edit_teacher",
                membership_id=teacher_membership.id,
            )

        user = teacher_membership.user
        user.first_name = first_name
        user.last_name = surname
        user.phone = phone
        user.save(update_fields=["first_name", "last_name", "phone"])

        TeacherClassAssignment.objects.filter(
            teacher_membership=teacher_membership,
            is_active=True,
        ).update(is_active=False)

        TeacherClassAssignment.objects.filter(
            school_class=school_class,
            is_active=True,
        ).exclude(
            teacher_membership=teacher_membership
        ).update(is_active=False)

        assignment, created = TeacherClassAssignment.objects.get_or_create(
            teacher_membership=teacher_membership,
            school_class=school_class,
            defaults={"is_active": True},
        )

        if not created and not assignment.is_active:
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])

        messages.success(
            request,
            f"{first_name} {surname} updated and assigned to {school_class.display_name}.",
        )

        return redirect("school_admin_teachers")

    return render(
        request,
        "portal/school_admin_edit_teacher.html",
        {
            "school": school,
            "teacher_membership": teacher_membership,
            "teacher": teacher_membership.user,
            "classes": classes,
            "current_assignment": current_assignment,
        },
    )


def school_admin_deactivate_teacher(request, membership_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    admin_membership = request.user.school_memberships.filter(
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not admin_membership:
        return redirect("home")

    teacher_membership = SchoolMembership.objects.filter(
        id=membership_id,
        school=admin_membership.school,
        staff_role=SchoolMembership.StaffRole.TEACHER,
    ).select_related("user").first()

    if not teacher_membership:
        messages.error(request, "Teacher account was not found.")
        return redirect("school_admin_teachers")

    teacher_membership.is_active = False
    teacher_membership.save(update_fields=["is_active"])

    teacher_membership.user.is_active = False
    teacher_membership.user.save(update_fields=["is_active"])

    messages.success(
        request,
        f"{teacher_membership.user.first_name} {teacher_membership.user.last_name} was deactivated.",
    )

    return redirect("school_admin_teachers")


def school_admin_reactivate_teacher(request, membership_id):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    admin_membership = request.user.school_memberships.filter(
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not admin_membership:
        return redirect("home")

    teacher_membership = SchoolMembership.objects.filter(
        id=membership_id,
        school=admin_membership.school,
        staff_role=SchoolMembership.StaffRole.TEACHER,
    ).select_related("user").first()

    if not teacher_membership:
        messages.error(request, "Teacher account was not found.")
        return redirect("school_admin_teachers")

    teacher_membership.is_active = True
    teacher_membership.save(update_fields=["is_active"])

    teacher_membership.user.is_active = True
    teacher_membership.user.save(update_fields=["is_active"])

    messages.success(
        request,
        f"{teacher_membership.user.first_name} {teacher_membership.user.last_name} was reactivated. Assign a class if needed.",
    )

    return redirect("school_admin_teachers")

def school_admin_result_audit(request):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(
            request,
            "No active school-admin membership was found.",
        )
        return redirect("home")

    school = membership.school

    audit_logs = ResultAuditLog.objects.filter(
        result__school=school,
    ).select_related(
        "result",
        "result__student",
        "result__subject",
        "result__school_class",
        "result__session",
        "result__term",
        "changed_by",
    ).order_by("-changed_at")

    return render(
        request,
        "portal/school_admin_result_audit.html",
        {
            "school": school,
            "audit_logs": audit_logs,
        },
    )


def school_admin_student_report(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school

    student = Student.objects.filter(
        uuid=student_uuid,
        school=school,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
    ).first()

    if not student:
        messages.error(request, "Student was not found.")
        return redirect("school_admin_results")

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(request, "No current academic session or term was found.")
        return redirect("school_admin_results")

    summary = StudentTermSummary.objects.filter(
        school=school,
        student=student,
        session=session,
        term=term,
    ).select_related("school_class").first()

    if not summary:
        messages.error(request, "Student result summary is not available.")
        return redirect("school_admin_results")

    result_class = summary.school_class

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
        is_published=True,
    ).first()

    if not publication:
        messages.error(request, "This result has not been published yet.")
        return redirect(
            "school_admin_class_result_details",
            class_id=result_class.id,
        )

    results = StudentResult.objects.filter(
        student=student,
        school_class=result_class,
        session=session,
        term=term,
    ).select_related("subject").order_by("subject__name")

    class_size = StudentTermSummary.objects.filter(
        school=school,
        school_class=result_class,
        session=session,
        term=term,
    ).count()

    remarks = get_result_remarks(summary.average_score)

    annual_summary = None
    if term.name == "THIRD":
        annual_summary = StudentAnnualSummary.objects.filter(
            student=student,
            school_class=result_class,
            session=session,
        ).first()

    return render(
        request,
        "portal/parent_student_result.html",
        {
            "school": school,
            "student": student,
            "session": session,
            "term": term,
            "results": results,
            "summary": summary,
            "class_size": class_size,
            "publication": publication,
            "annual_summary": annual_summary,
            "teacher_remark": remarks["teacher_remark"],
            "management_remark": remarks["management_remark"],
        },
    )


def school_admin_correct_student_result(request, student_uuid):
    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = SchoolMembership.objects.filter(
        user=request.user,
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school

    student = Student.objects.filter(
        uuid=student_uuid,
        school=school,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
    ).first()

    if not student or not student.current_class:
        messages.error(request, "Student was not found.")
        return redirect("school_admin_results")

    school_class = student.current_class

    session = school.academic_sessions.filter(
        is_current=True,
        is_active=True,
    ).first()

    term = school.academic_terms.filter(
        session=session,
        is_current=True,
        is_active=True,
    ).first() if session else None

    if not session or not term:
        messages.error(
            request,
            "No current academic session or term was found.",
        )
        return redirect("school_admin_results")

    publication = ClassResultPublication.objects.filter(
        school=school,
        school_class=school_class,
        session=session,
        term=term,
        is_published=True,
    ).first()

    if not publication:
        messages.error(
            request,
            "This class result has not been published yet. Teachers should make corrections before publication.",
        )
        return redirect(
            "school_admin_class_result_details",
            class_id=school_class.id,
        )

    subjects = get_student_required_subjects(student).order_by("name")

    if request.method == "POST":
        errors = []
        changed_count = 0

        for subject in subjects:
            result = StudentResult.objects.filter(
                school=school,
                student=student,
                school_class=school_class,
                subject=subject,
                session=session,
                term=term,
            ).first()

            if not result:
                continue

            ca_raw = request.POST.get(f"ca_{subject.id}", "").strip()
            exam_raw = request.POST.get(f"exam_{subject.id}", "").strip()

            if ca_raw == "" or exam_raw == "":
                errors.append(
                    f"{subject.name}: both CA and Exam scores are required."
                )
                continue

            try:
                ca_score = Decimal(ca_raw)
                exam_score = Decimal(exam_raw)
            except InvalidOperation:
                errors.append(
                    f"{subject.name}: scores must be valid numbers."
                )
                continue

            old_ca_score = result.ca_score
            old_exam_score = result.exam_score

            if old_ca_score == ca_score and old_exam_score == exam_score:
                continue

            result.ca_score = ca_score
            result.exam_score = exam_score

            try:
                result.save()

                ResultAuditLog.objects.create(
                    result=result,
                    changed_by=request.user,
                    action=ResultAuditLog.Action.UPDATED,
                    old_ca_score=old_ca_score,
                    old_exam_score=old_exam_score,
                    new_ca_score=result.ca_score,
                    new_exam_score=result.exam_score,
                )

                changed_count += 1

            except Exception as error:
                errors.append(
                    f"{subject.name}: {error}"
                )

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            if changed_count:
                update_student_term_summary(
                    student,
                    session,
                    term,
                )

                update_class_positions(
                    school_class,
                    session,
                    term,
                )

                if term.name == "THIRD":
                    update_student_annual_summary(
                        student,
                        session,
                    )

                    update_class_annual_positions(
                        school_class,
                        session,
                    )

                messages.success(
                    request,
                    f"{student.full_name}'s published result was corrected successfully.",
                )
            else:
                messages.info(
                    request,
                    "No result scores were changed.",
                )

            return redirect(
                "school_admin_correct_student_result",
                student_uuid=student.uuid,
            )

    result_rows = []

    for subject in subjects:
        result = StudentResult.objects.filter(
            school=school,
            student=student,
            school_class=school_class,
            subject=subject,
            session=session,
            term=term,
        ).first()

        if result:
            result_rows.append(
                {
                    "subject": subject,
                    "result": result,
                }
            )

    return render(
        request,
        "portal/school_admin_correct_student_result.html",
        {
            "school": school,
            "student": student,
            "school_class": school_class,
            "session": session,
            "term": term,
            "result_rows": result_rows,
        },
    )


# ===== PLATFORM OWNER / CEO DASHBOARD =====

def platform_owner_dashboard(request):
    if not request.user.is_authenticated:
        return redirect("platform_owner_login")

    if request.user.role != User.Role.PLATFORM_OWNER:
        logout(request)
        return redirect("platform_owner_login")

    schools = School.objects.all().order_by("name")

    school_rows = []

    total_students = 0
    total_teachers = 0
    total_school_admins = 0

    for school in schools:
        student_count = Student.objects.filter(
            school=school,
            status=Student.Status.ACTIVE,
        ).count()

        teacher_count = SchoolMembership.objects.filter(
            school=school,
            staff_role=SchoolMembership.StaffRole.TEACHER,
            is_active=True,
        ).count()

        admin_count = SchoolMembership.objects.filter(
            school=school,
            staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
            is_active=True,
        ).count()

        total_students += student_count
        total_teachers += teacher_count
        total_school_admins += admin_count

        school_rows.append({
            "school": school,
            "student_count": student_count,
            "teacher_count": teacher_count,
            "admin_count": admin_count,
        })

    context = {
        "school_rows": school_rows,
        "total_schools": schools.count(),
        "active_schools": schools.filter(is_active=True).count(),
        "inactive_schools": schools.filter(is_active=False).count(),
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_school_admins": total_school_admins,
    }

    return render(
        request,
        "portal/platform_owner_dashboard.html",
        context,
    )



# ===== CEO CREATE SCHOOL =====
def platform_owner_create_school(request):
    if not request.user.is_authenticated:
        return redirect("platform_owner_login")

    if request.user.role != User.Role.PLATFORM_OWNER:
        logout(request)
        return redirect("platform_owner_login")

    if request.method != "POST":
        return redirect("platform_owner_dashboard")

    school_name = request.POST.get("school_name", "").strip()
    motto = request.POST.get("motto", "").strip()
    school_email = request.POST.get("school_email", "").strip()
    school_phone = request.POST.get("school_phone", "").strip()
    address = request.POST.get("address", "").strip()

    admin_first_name = request.POST.get("admin_first_name", "").strip()
    admin_surname = request.POST.get("admin_surname", "").strip()
    admin_phone = request.POST.get("admin_phone", "").strip()
    temporary_password = request.POST.get("temporary_password", "").strip()

    academic_session = request.POST.get("academic_session", "").strip()
    starting_term = request.POST.get("starting_term", "").strip().upper()

    if not academic_session or not starting_term:
        messages.error(
            request,
            "Academic session and starting term are required.",
        )
        return redirect("platform_owner_dashboard")

    import re

    session_match = re.fullmatch(r"(\d{4})/(\d{4})", academic_session)

    if not session_match:
        messages.error(
            request,
            "Academic session must be written like 2026/2027.",
        )
        return redirect("platform_owner_dashboard")

    start_year = int(session_match.group(1))
    end_year = int(session_match.group(2))

    if end_year != start_year + 1:
        messages.error(
            request,
            "Academic session must contain consecutive years, for example 2026/2027.",
        )
        return redirect("platform_owner_dashboard")

    if starting_term not in {"FIRST", "SECOND", "THIRD"}:
        messages.error(request, "Invalid starting term selected.")
        return redirect("platform_owner_dashboard")

    if not school_name:
        messages.error(request, "School name is required.")
        return redirect("platform_owner_dashboard")

    if not admin_first_name or not admin_surname or not admin_phone:
        messages.error(request, "School administrator details are required.")
        return redirect("platform_owner_dashboard")

    if len(temporary_password) < 6:
        messages.error(
            request,
            "Temporary administrator password must contain at least 6 characters.",
        )
        return redirect("platform_owner_dashboard")

    if School.objects.filter(name__iexact=school_name).exists():
        messages.error(request, "A school with this name already exists.")
        return redirect("platform_owner_dashboard")

    if User.objects.filter(phone=admin_phone).exists():
        messages.error(
            request,
            "That administrator phone number is already attached to an account.",
        )
        return redirect("platform_owner_dashboard")

    try:
        with transaction.atomic():
            school = School.objects.create(
                name=school_name,
                motto=motto,
                email=school_email,
                phone=school_phone,
                address=address,
            )

            structure = bootstrap_school_structure(school)

        academic_session_obj = AcademicSession.objects.create(
            school=school,
            name=academic_session,
            is_current=True,
            is_active=True,
        )

        for term_name in ("FIRST", "SECOND", "THIRD"):
            AcademicTerm.objects.create(
                school=school,
                session=academic_session_obj,
                name=term_name,
                is_current=(term_name == starting_term),
                is_active=True,
            )

            admin_user = User.objects.create_user(
                phone=admin_phone,
                password=temporary_password,
                role=User.Role.SCHOOL_ADMIN,
                first_name=admin_first_name,
                last_name=admin_surname,
            )

            admin_user.must_change_password = True
            admin_user.is_active = True
            admin_user.save(
                update_fields=[
                    "must_change_password",
                    "is_active",
                ]
            )

            SchoolMembership.objects.create(
                user=admin_user,
                school=school,
                staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
                is_active=True,
            )

        messages.success(
            request,
            (
                f"{school.name} created successfully. "
                f"{structure['sections_created']} sections and "
                f"{structure['classes_created']} classes were created. "
                f"Administrator login: {admin_phone}. "
                "The administrator will be required to change the temporary password."
            ),
        )

    except Exception as exc:
        print(f"CEO school creation error: {exc}")
        messages.error(
            request,
            "School creation could not be completed. Please check the information entered and try again.",
        )

    return redirect("platform_owner_dashboard")



def platform_owner_paystack_banks(request):
    if not request.user.is_authenticated:
        return JsonResponse(
            {"ok": False, "message": "Authentication required."},
            status=401,
        )

    if request.user.role != User.Role.PLATFORM_OWNER:
        return JsonResponse(
            {"ok": False, "message": "You are not allowed to perform this action."},
            status=403,
        )

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    try:
        response = requests.get(
            "https://api.paystack.co/bank",
            params={
                "country": "nigeria",
                "currency": "NGN",
                "perPage": 100,
            },
            headers=headers,
            timeout=30,
        )

        data = response.json()

    except requests.RequestException:
        return JsonResponse(
            {
                "ok": False,
                "message": "Unable to load banks right now. Please try again.",
            },
            status=502,
        )
    except ValueError:
        return JsonResponse(
            {
                "ok": False,
                "message": "The payment service returned an invalid response.",
            },
            status=502,
        )

    if not response.ok or not data.get("status"):
        return JsonResponse(
            {
                "ok": False,
                "message": "Banks could not be loaded.",
            },
            status=502,
        )

    banks = []

    for bank in data.get("data", []):
        name = str(bank.get("name", "")).strip()
        code = str(bank.get("code", "")).strip()

        if name and code:
            banks.append({
                "name": name,
                "code": code,
            })

    banks.sort(key=lambda item: item["name"].lower())

    return JsonResponse({
        "ok": True,
        "banks": banks,
    })


def platform_owner_verify_bank_account(request, school_id):
    if not request.user.is_authenticated:
        return JsonResponse(
            {"ok": False, "message": "Authentication required."},
            status=401,
        )

    if request.user.role != User.Role.PLATFORM_OWNER:
        return JsonResponse(
            {"ok": False, "message": "You are not allowed to perform this action."},
            status=403,
        )

    if request.method != "POST":
        return JsonResponse(
            {"ok": False, "message": "Invalid request method."},
            status=405,
        )

    school = School.objects.filter(id=school_id).first()

    if school:
        # Ensure optional Early Years structure exists.
        # These are OFF by default and can be enabled per school by the CEO.
        early_section, _ = SchoolSection.objects.get_or_create(
            school=school,
            name="Early Years",
            defaults={
                "academic_level": "PRIMARY",
                "order": 0,
                "is_active": False,
            },
        )

        optional_classes = [
            ("Creche", 1),
            ("Nursery 1", 2),
            ("Nursery 2", 3),
        ]

        for class_name, class_order in optional_classes:
            SchoolClass.objects.get_or_create(
                section=early_section,
                name=class_name,
                arm="",
                defaults={
                    "order": class_order,
                    "is_active": False,
                },
            )

    if not school:
        return JsonResponse(
            {"ok": False, "message": "School not found."},
            status=404,
        )

    bank_code = request.POST.get("bank_code", "").strip()
    account_number = request.POST.get("account_number", "").strip()

    if not bank_code or not account_number:
        return JsonResponse(
            {
                "ok": False,
                "message": "Please select a bank and enter an account number.",
            },
            status=400,
        )

    if not account_number.isdigit():
        return JsonResponse(
            {
                "ok": False,
                "message": "Account number must contain numbers only.",
            },
            status=400,
        )

    headers = {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
    }

    try:
        response = requests.get(
            "https://api.paystack.co/bank/resolve",
            params={
                "account_number": account_number,
                "bank_code": bank_code,
            },
            headers=headers,
            timeout=30,
        )

        data = response.json()

    except requests.RequestException:
        return JsonResponse(
            {
                "ok": False,
                "message": "Unable to connect to the payment service. Please try again.",
            },
            status=502,
        )
    except ValueError:
        return JsonResponse(
            {
                "ok": False,
                "message": "The payment service returned an invalid response.",
            },
            status=502,
        )

    if not response.ok or not data.get("status"):
        return JsonResponse(
            {
                "ok": False,
                "message": "The bank account could not be verified. Please check the details.",
            },
            status=400,
        )

    resolved = data.get("data") or {}
    account_name = resolved.get("account_name", "").strip()

    if not account_name:
        return JsonResponse(
            {
                "ok": False,
                "message": "The account was found, but no account name was returned.",
            },
            status=400,
        )

    return JsonResponse(
        {
            "ok": True,
            "account_name": account_name,
            "account_number": resolved.get("account_number", account_number),
            "bank_code": bank_code,
        }
    )


# ===== PLATFORM OWNER / CEO AUTH =====

def platform_owner_login(request):
    if request.user.is_authenticated and request.user.role == User.Role.PLATFORM_OWNER:
        return redirect("platform_owner_dashboard")

    error = None

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(
            request,
            username=phone,
            password=password,
        )

        if not user:
            error = "Invalid phone number or password."
        elif user.role != User.Role.PLATFORM_OWNER:
            error = "This account does not have platform owner access."
        elif not user.is_active:
            error = "This account is inactive."
        else:
            login(request, user)
            return redirect("platform_owner_dashboard")

    return render(
        request,
        "portal/platform_owner_login.html",
        {"error": error},
    )


def platform_owner_logout(request):
    logout(request)
    return redirect("platform_owner_login")


# ===== CEO MANAGE SCHOOL =====
def platform_owner_manage_school(request, school_id):
    from schools.models import School
    from django.utils.dateparse import parse_date

    if not request.user.is_authenticated:
        return redirect("platform_owner_login")

    if request.user.role != User.Role.PLATFORM_OWNER:
        logout(request)
        return redirect("platform_owner_login")

    school = School.objects.filter(id=school_id).first()

    if not school:
        messages.error(request, "School not found.")
        return redirect("platform_owner_dashboard")

    if request.method == "POST":
        if request.POST.get("action") == "save_branding":
            import re

            primary_color = request.POST.get(
                "primary_color",
                school.primary_color,
            ).strip()

            secondary_color = request.POST.get(
                "secondary_color",
                school.secondary_color,
            ).strip()

            hex_pattern = r"^#[0-9A-Fa-f]{6}$"

            if not re.match(hex_pattern, primary_color):
                messages.error(request, "Invalid primary colour.")
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            if not re.match(hex_pattern, secondary_color):
                messages.error(request, "Invalid secondary colour.")
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            school.primary_color = primary_color.upper()
            school.secondary_color = secondary_color.upper()

            school.save(
                update_fields=[
                    "primary_color",
                    "secondary_color",
                ]
            )

            messages.success(
                request,
                f"{school.name} branding colours updated successfully.",
            )

            return redirect(
                "platform_owner_manage_school",
                school_id=school.id,
            )

        if request.POST.get("action") == "save_classes":
            selected_class_ids = {
                int(value)
                for value in request.POST.getlist("active_classes")
                if str(value).isdigit()
            }

            school_classes = SchoolClass.objects.filter(
                section__school=school
            ).select_related("section")

            for school_class in school_classes:
                should_be_active = school_class.id in selected_class_ids

                if school_class.is_active != should_be_active:
                    school_class.is_active = should_be_active
                    school_class.save(update_fields=["is_active"])

            active_section_ids = set(
                SchoolClass.objects.filter(
                    section__school=school,
                    is_active=True,
                ).values_list("section_id", flat=True)
            )

            for section in SchoolSection.objects.filter(school=school):
                should_be_active = section.id in active_section_ids

                if section.is_active != should_be_active:
                    section.is_active = should_be_active
                    section.save(update_fields=["is_active"])

            messages.success(
                request,
                f"{school.name} class structure updated successfully.",
            )

            return redirect(
                "platform_owner_manage_school",
                school_id=school.id,
            )

        plan = request.POST.get("subscription_plan", "").strip()
        status = request.POST.get("subscription_status", "").strip()
        expiry_raw = request.POST.get("subscription_expiry_date", "").strip()

        # Payment / settlement configuration
        service_fee_raw = request.POST.get("portal_service_fee", "").strip()
        settlement_bank_code = request.POST.get("settlement_bank_code", "").strip()
        settlement_bank_name = request.POST.get("settlement_bank_name", "").strip()
        settlement_account_number = request.POST.get("settlement_account_number", "").strip()
        settlement_account_name = request.POST.get("settlement_account_name", "").strip()

        try:
            portal_service_fee = Decimal(service_fee_raw or "0")
            if portal_service_fee < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            messages.error(request, "Please enter a valid portal service fee.")
            return redirect(
                "platform_owner_manage_school",
                school_id=school.id,
            )

        if settlement_account_number and not settlement_account_number.isdigit():
            messages.error(request, "Settlement account number must contain numbers only.")
            return redirect(
                "platform_owner_manage_school",
                school_id=school.id,
            )

        valid_plans = {
            "LIFETIME_FREE",
            "BASIC",
            "PREMIUM",
            "ENTERPRISE",
        }

        valid_statuses = {
            "ACTIVE",
            "EXPIRED",
            "SUSPENDED",
        }

        if plan not in valid_plans:
            messages.error(request, "Invalid subscription plan.")
            return redirect(
                "platform_owner_manage_school",
                school_id=school.id,
            )

        if status not in valid_statuses:
            messages.error(request, "Invalid subscription status.")
            return redirect(
                "platform_owner_manage_school",
                school_id=school.id,
            )

        expiry_date = None

        if expiry_raw:
            expiry_date = parse_date(expiry_raw)

            if not expiry_date:
                messages.error(request, "Invalid expiry date.")
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

        school.subscription_plan = plan
        school.subscription_status = status
        school.subscription_expiry_date = expiry_date

        school.is_active = (
            request.POST.get("is_active") == "on"
        )

        school.school_admin_can_manage_fees = (
            request.POST.get("school_admin_can_manage_fees") == "on"
        )

        # Payment / settlement configuration.
        # Never trust the account name sent by the browser. Resolve it again
        # with Paystack before saving or activating online payments.
        has_any_bank_detail = any([
            settlement_bank_code,
            settlement_bank_name,
            settlement_account_number,
        ])

        if has_any_bank_detail:
            if not settlement_bank_code or not settlement_account_number:
                messages.error(
                    request,
                    "Please select a settlement bank and enter the account number.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            headers = {
                "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                "Content-Type": "application/json",
            }

            try:
                verify_response = requests.get(
                    "https://api.paystack.co/bank/resolve",
                    params={
                        "account_number": settlement_account_number,
                        "bank_code": settlement_bank_code,
                    },
                    headers=headers,
                    timeout=30,
                )
                verify_data = verify_response.json()
            except (requests.RequestException, ValueError):
                messages.error(
                    request,
                    "The bank account could not be verified right now. Please try again.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            if not verify_response.ok or not verify_data.get("status"):
                messages.error(
                    request,
                    "The bank account could not be verified. Please check the details.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            verified_account_name = (
                verify_data.get("data", {}).get("account_name", "").strip()
            )

            if not verified_account_name:
                messages.error(
                    request,
                    "Paystack could not confirm the account name. Please try again.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            subaccount_payload = {
                "business_name": school.name,
                "bank_code": settlement_bank_code,
                "account_number": settlement_account_number,
                "percentage_charge": 0,
                "description": f"Settlement account for {school.name}",
            }

            if school.email:
                subaccount_payload["primary_contact_email"] = school.email

            try:
                if school.paystack_subaccount_code:
                    subaccount_response = requests.put(
                        "https://api.paystack.co/subaccount/"
                        + school.paystack_subaccount_code,
                        json=subaccount_payload,
                        headers=headers,
                        timeout=30,
                    )
                else:
                    subaccount_response = requests.post(
                        "https://api.paystack.co/subaccount",
                        json=subaccount_payload,
                        headers=headers,
                        timeout=30,
                    )

                subaccount_data = subaccount_response.json()
            except (requests.RequestException, ValueError):
                messages.error(
                    request,
                    "Online payment setup could not be completed right now. Please try again.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            if not subaccount_response.ok or not subaccount_data.get("status"):
                messages.error(
                    request,
                    "Online payment setup could not be completed. Please check the settlement details.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            returned_code = (
                subaccount_data.get("data", {}).get("subaccount_code")
                or school.paystack_subaccount_code
            )

            if not returned_code:
                messages.error(
                    request,
                    "Paystack did not return a valid settlement account reference.",
                )
                return redirect(
                    "platform_owner_manage_school",
                    school_id=school.id,
                )

            school.settlement_bank_code = settlement_bank_code
            school.settlement_bank_name = settlement_bank_name
            school.settlement_account_number = settlement_account_number
            school.settlement_account_name = verified_account_name
            school.paystack_subaccount_code = returned_code
            school.paystack_subaccount_active = True

        school.portal_service_fee = portal_service_fee

        school.subscription_plan = plan
        school.subscription_status = status
        school.subscription_expiry_date = expiry_date
        school.is_active = request.POST.get("is_active") == "on"
        school.school_admin_can_manage_fees = (
            request.POST.get("school_admin_can_manage_fees") == "on"
        )

        school.save(
            update_fields=[
                "subscription_plan",
                "subscription_status",
                "subscription_expiry_date",
                "is_active",
                "school_admin_can_manage_fees",
                "portal_service_fee",
                "settlement_bank_code",
                "settlement_bank_name",
                "settlement_account_number",
                "settlement_account_name",
                "paystack_subaccount_code",
                "paystack_subaccount_active",
                "updated_at",
            ]
        )

        messages.success(
            request,
            f"{school.name} updated successfully.",
        )

        return redirect(
            "platform_owner_manage_school",
            school_id=school.id,
        )

    class_sections = (
        SchoolSection.objects.filter(school=school)
        .prefetch_related("classes")
        .order_by("order", "name")
    )

    return render(
        request,
        "portal/platform_owner_manage_school.html",
        {
            "school": school,
            "class_sections": class_sections,
        },
    )


def school_admin_parents(request):
    from django.db.models import Q
    from students.models import ParentProfile, StudentParent

    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school
    query = request.GET.get("q", "").strip()
    selected_parent_id = request.GET.get("parent", "").strip()

    links = StudentParent.objects.filter(
        student__school=school,
        student__status="ACTIVE",
    ).select_related(
        "parent",
        "parent__user",
        "student",
        "student__current_class",
        "student__current_class__section",
    ).order_by(
        "parent__user__first_name",
        "parent__user__last_name",
        "student__first_name",
    )

    if query:
        links = links.filter(
            Q(parent__user__first_name__icontains=query)
            | Q(parent__user__last_name__icontains=query)
            | Q(parent__user__phone__icontains=query)
            | Q(parent__user__email__icontains=query)
            | Q(student__first_name__icontains=query)
            | Q(student__surname__icontains=query)
            | Q(student__admission_number__icontains=query)
        )

    parent_map = {}

    for link in links:
        parent = link.parent

        if parent.id not in parent_map:
            parent_map[parent.id] = {
                "parent": parent,
                "children": [],
            }

        parent_map[parent.id]["children"].append(link)

    parent_rows = list(parent_map.values())

    selected_row = None

    if selected_parent_id:
        try:
            selected_id = int(selected_parent_id)
        except (TypeError, ValueError):
            selected_id = None

        if selected_id is not None:
            for row in parent_rows:
                if row["parent"].id == selected_id:
                    selected_row = row
                    break

            # Allow opening a parent even when a search filter is active.
            if selected_row is None:
                detail_links = StudentParent.objects.filter(
                    parent_id=selected_id,
                    student__school=school,
                    student__status="ACTIVE",
                ).select_related(
                    "parent",
                    "parent__user",
                    "student",
                    "student__current_class",
                    "student__current_class__section",
                )

                detail_links = list(detail_links)

                if detail_links:
                    selected_row = {
                        "parent": detail_links[0].parent,
                        "children": detail_links,
                    }

    context = {
        "school": school,
        "parent_rows": parent_rows,
        "selected_row": selected_row,
        "query": query,
        "parent_count": len(parent_rows),
        "student_link_count": sum(len(row["children"]) for row in parent_rows),
    }

    return render(
        request,
        "portal/school_admin_parents.html",
        context,
    )


def school_admin_messages(request):
    from portal.models import SchoolMessage
    from students.models import StudentParent, ParentProfile
    from schools.models import SchoolClass

    if not request.user.is_authenticated:
        return redirect("school_admin_login")

    if request.user.role != User.Role.SCHOOL_ADMIN:
        return redirect("home")

    membership = request.user.school_memberships.filter(
        staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
        is_active=True,
    ).select_related("school").first()

    if not membership:
        messages.error(request, "No active school-admin membership was found.")
        return redirect("home")

    school = membership.school

    classes = SchoolClass.objects.filter(
        section__school=school,
        is_active=True,
    ).select_related("section").order_by(
        "section__order",
        "order",
        "name",
        "arm",
    )

    parent_ids = StudentParent.objects.filter(
        student__school=school,
        student__status="ACTIVE",
    ).values_list("parent_id", flat=True).distinct()

    parents = ParentProfile.objects.filter(
        id__in=parent_ids,
    ).select_related("user").order_by(
        "user__first_name",
        "user__last_name",
        "user__phone",
    )

    if request.method == "POST":
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        target_type = request.POST.get("target_type", "").strip()
        target_class_id = request.POST.get("target_class", "").strip()
        target_parent_id = request.POST.get("target_parent", "").strip()

        if not subject or not body:
            messages.error(request, "Subject and message are required.")
            return redirect("school_admin_messages")

        if target_type not in {
            SchoolMessage.TARGET_ALL_PARENTS,
            SchoolMessage.TARGET_CLASS,
            SchoolMessage.TARGET_PARENT,
        }:
            messages.error(request, "Please select a valid recipient type.")
            return redirect("school_admin_messages")

        target_class = None
        target_parent = None

        if target_type == SchoolMessage.TARGET_CLASS:
            target_class = classes.filter(id=target_class_id).first()
            if not target_class:
                messages.error(request, "Please select a valid class.")
                return redirect("school_admin_messages")

        if target_type == SchoolMessage.TARGET_PARENT:
            target_parent = parents.filter(id=target_parent_id).first()
            if not target_parent:
                messages.error(request, "Please select a valid parent.")
                return redirect("school_admin_messages")

        SchoolMessage.objects.create(
            school=school,
            sender=request.user,
            subject=subject,
            body=body,
            target_type=target_type,
            target_class=target_class,
            target_parent=target_parent,
        )

        messages.success(request, "Message sent successfully.")
        return redirect("school_admin_messages")

    sent_messages = SchoolMessage.objects.filter(
        school=school,
    ).select_related(
        "sender",
        "target_class",
        "target_parent",
        "target_parent__user",
    ).order_by("-created_at")[:50]

    return render(
        request,
        "portal/school_admin_messages.html",
        {
            "school": school,
            "classes": classes,
            "parents": parents,
            "sent_messages": sent_messages,
        },
    )


def parent_messages(request):
    from portal.models import SchoolMessage
    from students.models import StudentParent
    from django.db.models import Q

    if not request.user.is_authenticated:
        return redirect("parent_login")

    if request.user.role != User.Role.PARENT:
        return redirect("home")

    parent_profile = getattr(request.user, "parent_profile", None)
    if not parent_profile:
        return redirect("parent_login")

    # Use only the school the parent actually logged into.
    school_slug = request.session.get("selected_school_slug")
    school_id = request.session.get("selected_school_id")

    school = None

    if school_slug:
        school = School.objects.filter(slug=school_slug).first()

    if not school and school_id:
        school = School.objects.filter(id=school_id).first()

    if not school:
        return redirect("home")

    # Only this parent's children in the selected school.
    child_links = StudentParent.objects.filter(
        parent=parent_profile,
        student__status="ACTIVE",
        student__school=school,
    ).select_related(
        "student",
        "student__school",
        "student__current_class",
    )

    class_ids = {
        link.student.current_class_id
        for link in child_links
        if link.student.current_class_id
    }

    # Never allow messages from another school into this inbox.
    inbox = SchoolMessage.objects.filter(
        school=school,
    ).filter(
        Q(target_type=SchoolMessage.TARGET_ALL_PARENTS)
        | Q(
            target_type=SchoolMessage.TARGET_CLASS,
            target_class_id__in=class_ids,
        )
        | Q(
            target_type=SchoolMessage.TARGET_PARENT,
            target_parent=parent_profile,
        )
    ).select_related(
        "school",
        "sender",
        "target_class",
    ).distinct().order_by("-created_at")

    message_rows = []

    for item in inbox:
        related_children = []

        if (
            item.target_type == SchoolMessage.TARGET_CLASS
            and item.target_class_id
        ):
            for link in child_links:
                if link.student.current_class_id == item.target_class_id:
                    related_children.append(link.student)

        message_rows.append({
            "message": item,
            "related_children": related_children,
        })

    return render(
        request,
        "portal/parent_messages.html",
        {
            "school": school,
            "message_rows": message_rows,
        },
    )

