document.addEventListener("DOMContentLoaded", function () {
    const studentType = document.getElementById("id_student_type");

    if (!studentType) {
        return;
    }

    const admissionYearRow = document.querySelector(".field-admission_year");
    const admissionDateRow = document.querySelector(".field-exact_admission_date");
    const entryClassRow = document.querySelector(".field-entry_class_name");
    const previousSchoolRow = document.querySelector(".field-previous_school");

    function updateFields() {
        const value = studentType.value;

        if (value === "NEW") {
            if (admissionYearRow) admissionYearRow.style.display = "";
            if (admissionDateRow) admissionDateRow.style.display = "";
            if (entryClassRow) entryClassRow.style.display = "none";
            if (previousSchoolRow) previousSchoolRow.style.display = "";
        } else if (value === "EXISTING") {
            if (admissionYearRow) admissionYearRow.style.display = "";
            if (admissionDateRow) admissionDateRow.style.display = "";
            if (entryClassRow) entryClassRow.style.display = "";
            if (previousSchoolRow) previousSchoolRow.style.display = "none";
        } else {
            if (admissionYearRow) admissionYearRow.style.display = "";
            if (admissionDateRow) admissionDateRow.style.display = "";
            if (entryClassRow) entryClassRow.style.display = "";
            if (previousSchoolRow) previousSchoolRow.style.display = "";
        }
    }

    studentType.addEventListener("change", updateFields);
    updateFields();
});
