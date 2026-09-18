from schools.models import School


def active_school(request):
    """
    Make the current school available automatically to school-facing templates.

    Priority:
    1. School selected in the user's session.
    2. User's active school membership.
    """

    school = None

    selected_school_id = request.session.get("selected_school_id")

    if selected_school_id:
        school = School.objects.filter(
            id=selected_school_id,
            is_active=True,
        ).first()

    if school is None and getattr(request, "user", None):
        if request.user.is_authenticated:
            membership = (
                request.user.school_memberships
                .filter(is_active=True)
                .select_related("school")
                .first()
            )

            if membership:
                school = membership.school

    return {
        "school": school,
    }
