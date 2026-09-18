from django import forms

from .models import Student, StudentParent


class StudentAdminForm(forms.ModelForm):
    parent_first_name = forms.CharField(max_length=100)
    parent_last_name = forms.CharField(max_length=100)
    parent_phone = forms.CharField(max_length=20)
    parent_email = forms.EmailField(required=False)

    relationship = forms.ChoiceField(
        choices=StudentParent.Relationship.choices
    )

    def clean(self):
        cleaned_data = super().clean()

        student_type = cleaned_data.get("student_type")
        admission_year = cleaned_data.get("admission_year")
        exact_admission_date = cleaned_data.get("exact_admission_date")
        entry_class_name = cleaned_data.get("entry_class_name")

        if student_type == Student.StudentType.NEW:
            if not exact_admission_date:
                self.add_error(
                    "exact_admission_date",
                    "Admission date is required for a new student."
                )

        if student_type == Student.StudentType.EXISTING:
            if not admission_year:
                self.add_error(
                    "admission_year",
                    "Enter the year this student first joined the school."
                )

            if not entry_class_name:
                self.add_error(
                    "entry_class_name",
                    "Enter the class this student originally joined."
                )

        return cleaned_data

    class Meta:
        model = Student
        fields = (
            "school",
            "current_class",
            "student_type",
            "surname",
            "first_name",
            "middle_name",
            "gender",
            "date_of_birth",
            "passport",
            "admission_year",
            "exact_admission_date",
            "entry_class_name",
            "previous_school",
            "status",
        )

        help_texts = {
            "student_type": (
                "Choose New Student if the child is joining the school now. "
                "Choose Existing Student if the child was already attending "
                "before the portal was introduced."
            ),
            "admission_year": (
                "For existing students, enter the year they first joined "
                "the school."
            ),
            "exact_admission_date": (
                "Enter the exact date if known. This can be left blank "
                "for existing students when the date is unknown."
            ),
            "entry_class_name": (
                "Enter the class the student first joined, for example "
                "Primary 1 or JSS 1."
            ),
            "previous_school": (
                "For new students, enter the previous school if applicable."
            ),
        }
