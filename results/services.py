from schools.models import Subject, SchoolClass, SchoolSection
from students.models import Student
from .models import StudentResult


def get_student_required_subjects(student):
    """
    Return the active subjects this student is expected to take.
    """

    subjects = Subject.objects.filter(
        school=student.school,
        classes=student.current_class,
        is_active=True,
    ).distinct()

    section = student.current_class.section

    # Primary and JSS use all active subjects assigned to the class.
    if section.name != "Senior Secondary":
        return subjects

    # Senior Secondary depends on student department.
    if student.department == Student.Department.SCIENCE:
        return subjects.filter(senior_science=True)

    if student.department == Student.Department.COMMERCIAL:
        return subjects.filter(senior_commercial=True)

    if student.department == Student.Department.ARTS:
        return subjects.filter(senior_arts=True)

    # No department assigned yet = result cannot be complete.
    return subjects.none()


def get_student_result_completeness(student, session, term):
    required_subjects = get_student_required_subjects(student)

    required_subject_ids = set(
        required_subjects.values_list("id", flat=True)
    )

    completed_subject_ids = set(
        StudentResult.objects.filter(
            student=student,
            session=session,
            term=term,
            subject_id__in=required_subject_ids,
        ).values_list("subject_id", flat=True)
    )

    missing_subject_ids = (
        required_subject_ids - completed_subject_ids
    )

    missing_subjects = Subject.objects.filter(
        id__in=missing_subject_ids
    ).order_by("name")

    required_count = len(required_subject_ids)
    completed_count = len(completed_subject_ids)

    # Senior Secondary student without department is incomplete,
    # even if no subjects are returned.
    department_missing = (
        student.current_class.section.name == "Senior Secondary"
        and not student.department
    )

    is_complete = (
        not department_missing
        and required_count > 0
        and completed_count == required_count
    )

    return {
        "student": student,
        "required_count": required_count,
        "completed_count": completed_count,
        "missing_subjects": missing_subjects,
        "department_missing": department_missing,
        "is_complete": is_complete,
    }


def get_class_result_completeness(school_class, session, term):
    students = Student.objects.filter(
        school=school_class.section.school,
        current_class=school_class,
        status="ACTIVE",
    ).select_related(
        "current_class",
        "current_class__section",
        "school",
    ).order_by(
        "surname",
        "first_name",
    )

    student_statuses = []

    complete_count = 0

    for student in students:
        status = get_student_result_completeness(
            student,
            session,
            term,
        )

        student_statuses.append(status)

        if status["is_complete"]:
            complete_count += 1

    total_students = students.count()
    incomplete_count = total_students - complete_count

    is_ready = (
        total_students > 0
        and incomplete_count == 0
    )

    return {
        "school_class": school_class,
        "total_students": total_students,
        "complete_count": complete_count,
        "incomplete_count": incomplete_count,
        "is_ready": is_ready,
        "students": student_statuses,
    }


from django.db.models import Sum, Avg

from .models import StudentTermSummary


def update_student_term_summary(student, session, term):
    """
    Recalculate and save one student's term summary.
    """

    required_subjects = get_student_required_subjects(student)

    results = StudentResult.objects.filter(
        student=student,
        session=session,
        term=term,
        subject__in=required_subjects,
    )

    required_count = required_subjects.count()
    completed_count = results.count()

    if required_count == 0 or completed_count != required_count:
        StudentTermSummary.objects.filter(
            student=student,
            session=session,
            term=term,
        ).delete()

        return None

    totals = results.aggregate(
        total=Sum("total_score"),
        average=Avg("total_score"),
    )

    summary, _ = StudentTermSummary.objects.update_or_create(
        school=student.school,
        student=student,
        school_class=student.current_class,
        session=session,
        term=term,
        defaults={
            "subject_count": required_count,
            "total_score": totals["total"] or 0,
            "average_score": totals["average"] or 0,
        },
    )

    return summary


def update_class_positions(school_class, session, term):
    """
    Recalculate positions for completed students in one class.
    Uses standard competition ranking:
    1, 2, 2, 4...
    """

    summaries = list(
        StudentTermSummary.objects.filter(
            school_class=school_class,
            session=session,
            term=term,
        ).order_by(
            "-average_score",
            "student__surname",
            "student__first_name",
        )
    )

    previous_average = None
    current_position = 0

    for index, summary in enumerate(summaries, start=1):
        if (
            previous_average is None
            or summary.average_score != previous_average
        ):
            current_position = index

        summary.position = current_position
        summary.save(update_fields=["position"])

        previous_average = summary.average_score

    return summaries


def get_result_remarks(average_score):
    """
    Generate encouraging teacher and school-management remarks
    based on the student's term average.
    """

    average = float(average_score or 0)

    if average >= 80:
        teacher_remark = (
            "Excellent performance. Keep maintaining this strong effort "
            "and continue aiming higher."
        )
        management_remark = (
            "An outstanding result. Keep up the excellent work and "
            "continue making the school proud."
        )

    elif average >= 70:
        teacher_remark = (
            "Very good performance. Keep working hard and stay consistent "
            "across all subjects."
        )
        management_remark = (
            "A very good result. Maintain this effort and continue striving "
            "for even greater achievement."
        )

    elif average >= 60:
        teacher_remark = (
            "Good performance. With more focus and consistent effort, "
            "you can achieve an even better result."
        )
        management_remark = (
            "A good result. Continue working steadily and aim for stronger "
            "performance next term."
        )

    elif average >= 50:
        teacher_remark = (
            "Fair performance. More attention and regular study will help "
            "you improve significantly."
        )
        management_remark = (
            "There is room for improvement. Stay focused, work consistently, "
            "and aim higher next term."
        )

    elif average >= 40:
        teacher_remark = (
            "More effort is needed. Focus on your weaker subjects and seek "
            "help whenever necessary."
        )
        management_remark = (
            "Improvement is required. Remain determined, work harder, and "
            "make good use of the support available to you."
        )

    else:
        teacher_remark = (
            "Significant improvement is needed. Do not give up; study more "
            "consistently and ask for help in difficult subjects."
        )
        management_remark = (
            "This result requires greater effort. Stay committed, accept "
            "guidance, and work steadily toward a stronger performance."
        )

    return {
        "teacher_remark": teacher_remark,
        "management_remark": management_remark,
    }


from .models import StudentAnnualSummary


def update_student_annual_summary(student, session):
    """
    Build/update one student's annual summary from
    First, Second and Third Term summaries.
    """

    term_summaries = {
        summary.term.name: summary
        for summary in StudentTermSummary.objects.filter(
            student=student,
            session=session,
            school_class=student.current_class,
        ).select_related("term")
    }

    required_terms = ["FIRST", "SECOND", "THIRD"]

    if not all(term in term_summaries for term in required_terms):
        StudentAnnualSummary.objects.filter(
            student=student,
            school_class=student.current_class,
            session=session,
        ).delete()

        return None

    first = term_summaries["FIRST"].average_score
    second = term_summaries["SECOND"].average_score
    third = term_summaries["THIRD"].average_score

    annual_average = (
        first + second + third
    ) / Decimal("3")

    annual, _ = StudentAnnualSummary.objects.update_or_create(
        school=student.school,
        student=student,
        school_class=student.current_class,
        session=session,
        defaults={
            "first_term_average": first,
            "second_term_average": second,
            "third_term_average": third,
            "annual_average": annual_average,
        },
    )

    return annual


def update_class_annual_positions(school_class, session):
    """
    Rank completed annual summaries in one class.
    Ties use standard competition ranking:
    1, 2, 2, 4...
    """

    summaries = list(
        StudentAnnualSummary.objects.filter(
            school_class=school_class,
            session=session,
        ).order_by(
            "-annual_average",
            "student__surname",
            "student__first_name",
        )
    )

    previous_average = None
    current_position = 0

    for index, summary in enumerate(summaries, start=1):
        if (
            previous_average is None
            or summary.annual_average != previous_average
        ):
            current_position = index

        summary.annual_position = current_position
        summary.save(
            update_fields=["annual_position"]
        )

        previous_average = summary.annual_average

    return summaries


def get_next_school_class(student):
    """
    Return the next academic class, without accidentally
    treating another arm of the same class as promotion.

    Example:
    JSS 1A -> JSS 2
    not JSS 1B.
    """

    current_class = student.current_class
    current_section = current_class.section

    # First try the next class inside the same section.
    next_class = SchoolClass.objects.filter(
        section=current_section,
        is_active=True,
        order__gt=current_class.order,
    ).order_by(
        "order",
        "name",
        "arm",
    ).first()

    if next_class:
        return next_class

    # If this is the last class in the section,
    # move to the first class of the next section.
    next_section = SchoolSection.objects.filter(
        school=student.school,
        is_active=True,
        order__gt=current_section.order,
    ).order_by(
        "order",
        "name",
    ).first()

    if not next_section:
        # Final class in the entire school.
        return None

    return SchoolClass.objects.filter(
        section=next_section,
        is_active=True,
    ).order_by(
        "order",
        "name",
        "arm",
    ).first()
