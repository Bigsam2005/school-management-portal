from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from schools.models import (
    School,
    SchoolSection,
    SchoolClass,
    AcademicSession,
    AcademicTerm,
)
from students.models import Student, ParentProfile, StudentParent
from fees.models import FeeStructure, StudentPayment


class PaymentTortureTests(TestCase):

    def setUp(self):
        self.school = School.objects.create(
            name="PAYMENT TEST SCHOOL",
            code="PTS",
            slug="payment-test-school",
            paystack_subaccount_code="ACCT_TEST_001",
            paystack_subaccount_active=True,
            portal_service_fee=Decimal("400.00"),
        )

        self.other_school = School.objects.create(
            name="OTHER TEST SCHOOL",
            code="OTS",
            slug="other-test-school",
        )

        self.section = SchoolSection.objects.create(
            school=self.school,
            name="Primary",
        )

        self.school_class = SchoolClass.objects.create(
            section=self.section,
            name="Primary 5",
            order=5,
        )

        self.session_obj = AcademicSession.objects.create(
            school=self.school,
            name="2026/2027",
            is_current=True,
        )

        self.term = AcademicTerm.objects.create(
            school=self.school,
            session=self.session_obj,
            name="FIRST",
            is_current=True,
        )

        self.fee_structure = FeeStructure.objects.create(
            school=self.school,
            school_class=self.school_class,
            session=self.session_obj,
            term=self.term,
            amount=Decimal("90000.00"),
        )

        self.parent_user = User.objects.create_user(
            phone="08000000001",
            password="SafeTest123!",
            role=User.Role.PARENT,
            email="paymenttest@example.com",
        )

        self.parent = ParentProfile.objects.create(
            user=self.parent_user,
            account_activated=True,
        )

        self.student = Student.objects.create(
            school=self.school,
            current_class=self.school_class,
            student_type="EXISTING",
            admission_number="PTS-TEST-0001",
            surname="Test",
            first_name="Student",
            gender="MALE",
            admission_year=2026,
        )

        StudentParent.objects.create(
            student=self.student,
            parent=self.parent,
            relationship=StudentParent.Relationship.MOTHER,
        )

        self.client.force_login(self.parent_user)

        session = self.client.session
        session["selected_school_id"] = self.school.id
        session["selected_school_slug"] = self.school.slug
        session.save()

        self.reference = "TORTURE-REF-001"
        self.amount = Decimal("30000.00")
        self.service_fee = Decimal("400.00")

        self.callback_url = reverse(
            "parent_payment_callback",
            kwargs={"student_uuid": self.student.uuid},
        )

    def set_pending_payment(self, **changes):
        data = {
            "student_uuid": str(self.student.uuid),
            "fee_structure_id": self.fee_structure.id,
            "amount": str(self.amount),
            "service_fee": str(self.service_fee),
            "school_id": self.school.id,
            "paystack_reference": self.reference,
            "paystack_subaccount_code": self.school.paystack_subaccount_code,
        }
        data.update(changes)

        session = self.client.session
        session["pending_payment"] = data
        session.save()

    def successful_paystack_response(self, amount_kobo=None):
        if amount_kobo is None:
            amount_kobo = int((self.amount + self.service_fee) * 100)

        response = Mock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "status": True,
            "message": "Verification successful",
            "data": {
                "status": "success",
                "reference": self.reference,
                "requested_amount": amount_kobo,
            },
        }
        return response

    @patch("portal.views.requests.get")
    def test_missing_reference_is_rejected(self, mock_get):
        self.set_pending_payment()

        response = self.client.get(self.callback_url)

        self.assertEqual(StudentPayment.objects.count(), 0)
        mock_get.assert_not_called()
        self.assertEqual(response.status_code, 302)

    @patch("portal.views.requests.get")
    def test_callback_without_pending_payment_is_rejected(self, mock_get):
        response = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(StudentPayment.objects.count(), 0)
        mock_get.assert_not_called()
        self.assertEqual(response.status_code, 302)

    @patch("portal.views.requests.get")
    def test_wrong_student_in_pending_payment_is_rejected(self, mock_get):
        self.set_pending_payment(student_uuid=str(uuid4()))

        response = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(StudentPayment.objects.count(), 0)
        mock_get.assert_not_called()
        self.assertEqual(response.status_code, 302)

    @patch("portal.views.requests.get")
    def test_reference_tampering_is_rejected(self, mock_get):
        self.set_pending_payment()

        response = self.client.get(
            self.callback_url,
            {"reference": "ATTACKER-REFERENCE"},
        )

        self.assertEqual(StudentPayment.objects.count(), 0)
        mock_get.assert_not_called()
        self.assertEqual(response.status_code, 302)

    @patch("portal.views.requests.get")
    def test_failed_paystack_verification_is_rejected(self, mock_get):
        self.set_pending_payment()

        failed = Mock()
        failed.ok = True
        failed.status_code = 200
        failed.json.return_value = {
            "status": True,
            "message": "Verification returned failed",
            "data": {
                "status": "failed",
            },
        }
        mock_get.return_value = failed

        response = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(StudentPayment.objects.count(), 0)
        self.assertEqual(response.status_code, 302)

    @patch("portal.views.requests.get")
    def test_wrong_verified_amount_is_rejected(self, mock_get):
        self.set_pending_payment()

        mock_get.return_value = self.successful_paystack_response(
            amount_kobo=100
        )

        response = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(StudentPayment.objects.count(), 0)
        self.assertEqual(response.status_code, 302)

    @patch("portal.views.requests.get")
    def test_wrong_school_in_pending_payment_is_rejected(self, mock_get):
        self.set_pending_payment(
            school_id=self.other_school.id
        )

        mock_get.return_value = self.successful_paystack_response()

        response = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(StudentPayment.objects.count(), 0)
        self.assertEqual(response.status_code, 302)


    def payment_form_url(self):
        return reverse(
            "parent_pay_school_fees",
            kwargs={"student_uuid": self.student.uuid},
        )

    def create_confirmed_payment(self, amount, reference):
        return StudentPayment.objects.create(
            student=self.student,
            fee_structure=self.fee_structure,
            amount=Decimal(str(amount)),
            reference=reference,
            is_confirmed=True,
            school=self.school,
            paystack_subaccount_code=self.school.paystack_subaccount_code,
        )

    def test_zero_payment_is_rejected(self):
        response = self.client.post(
            self.payment_form_url(),
            {"amount": "0"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 0)

    def test_negative_payment_is_rejected(self):
        response = self.client.post(
            self.payment_form_url(),
            {"amount": "-5000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 0)

    def test_below_minimum_installment_is_rejected(self):
        response = self.client.post(
            self.payment_form_url(),
            {"amount": "29000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 0)

    def test_exact_minimum_installment_is_accepted(self):
        response = self.client.post(
            self.payment_form_url(),
            {"amount": "30000"},
        )

        self.assertEqual(response.status_code, 302)
        pending = self.client.session.get("pending_payment")

        self.assertIsNotNone(pending)
        self.assertEqual(pending["student_uuid"], str(self.student.uuid))
        self.assertEqual(pending["fee_structure_id"], self.fee_structure.id)
        self.assertEqual(pending["amount"], "30000")

    def test_overpayment_is_rejected(self):
        response = self.client.post(
            self.payment_form_url(),
            {"amount": "90001"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 0)

    def test_final_installment_must_clear_remaining_balance(self):
        self.create_confirmed_payment("30000", "ENTRY-FIRST")
        self.create_confirmed_payment("30000", "ENTRY-SECOND")

        response = self.client.post(
            self.payment_form_url(),
            {"amount": "29999"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 2)

    def test_final_installment_exact_balance_is_accepted(self):
        self.create_confirmed_payment("30000", "ENTRY-FIRST")
        self.create_confirmed_payment("30000", "ENTRY-SECOND")

        response = self.client.post(
            self.payment_form_url(),
            {"amount": "30000"},
        )

        self.assertEqual(response.status_code, 302)
        pending = self.client.session.get("pending_payment")

        self.assertIsNotNone(pending)
        self.assertEqual(pending["amount"], "30000")

    def test_fully_paid_student_cannot_start_another_payment(self):
        self.create_confirmed_payment("30000", "FULL-1")
        self.create_confirmed_payment("30000", "FULL-2")
        self.create_confirmed_payment("30000", "FULL-3")

        response = self.client.post(
            self.payment_form_url(),
            {"amount": "30000"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 3)

    def test_fourth_installment_slot_is_blocked(self):
        self.create_confirmed_payment("10000", "SLOT-1")
        self.create_confirmed_payment("10000", "SLOT-2")
        self.create_confirmed_payment("10000", "SLOT-3")

        response = self.client.post(
            self.payment_form_url(),
            {"amount": "60000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_payment", self.client.session)
        self.assertEqual(StudentPayment.objects.count(), 3)


    @patch("portal.views.requests.get")
    def test_valid_payment_is_recorded_once(self, mock_get):
        self.set_pending_payment()
        mock_get.return_value = self.successful_paystack_response()

        first = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(
            StudentPayment.objects.filter(reference=self.reference).count(),
            1,
        )

        payment = StudentPayment.objects.get(reference=self.reference)

        self.assertEqual(payment.student, self.student)
        self.assertEqual(payment.fee_structure, self.fee_structure)
        self.assertEqual(payment.amount, self.amount)
        self.assertTrue(payment.is_confirmed)

        # Simulate Paystack/browser sending the same callback again.
        self.set_pending_payment()
        mock_get.return_value = self.successful_paystack_response()

        second = self.client.get(
            self.callback_url,
            {"reference": self.reference},
        )

        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            StudentPayment.objects.filter(reference=self.reference).count(),
            1,
        )

class AcademicLifecycleTortureTests(PaymentTortureTests):

    def test_full_three_term_lifecycle(self):
        # Start from First Term
        session = self.session_obj
        first = self.term

        self.assertTrue(session.is_current)
        self.assertTrue(first.is_current)
        self.assertEqual(first.name, "FIRST")

        # FIRST -> SECOND
        first.is_current = False
        first.save(update_fields=["is_current"])

        second = AcademicTerm.objects.create(
            school=self.school,
            session=session,
            name="SECOND",
            is_current=True,
            is_active=True,
        )

        self.assertFalse(
            AcademicTerm.objects.get(pk=first.pk).is_current
        )
        self.assertTrue(second.is_current)

        # SECOND -> THIRD
        second.is_current = False
        second.save(update_fields=["is_current"])

        third = AcademicTerm.objects.create(
            school=self.school,
            session=session,
            name="THIRD",
            is_current=True,
            is_active=True,
        )

        self.assertFalse(
            AcademicTerm.objects.get(pk=second.pk).is_current
        )
        self.assertTrue(third.is_current)

        # THIRD -> NEW SESSION
        third.is_current = False
        third.save(update_fields=["is_current"])

        session.is_current = False
        session.save(update_fields=["is_current"])

        new_session = AcademicSession.objects.create(
            school=self.school,
            name="2027/2028",
            is_current=True,
        )

        for term_name in ("FIRST", "SECOND", "THIRD"):
            AcademicTerm.objects.create(
                school=self.school,
                session=new_session,
                name=term_name,
                is_current=(term_name == "FIRST"),
                is_active=True,
            )

        self.assertFalse(
            AcademicSession.objects.get(pk=session.pk).is_current
        )
        self.assertTrue(new_session.is_current)

        new_terms = AcademicTerm.objects.filter(
            school=self.school,
            session=new_session,
        )

        self.assertEqual(new_terms.count(), 3)
        self.assertEqual(
            new_terms.filter(is_current=True).count(),
            1,
        )
        self.assertEqual(
            new_terms.get(is_current=True).name,
            "FIRST",
        )

from accounts.models import SchoolMembership
from results.models import StudentAnnualSummary, ClassResultPublication


class AcademicLifecycleProtectionTests(PaymentTortureTests):

    def login_admin(self):
        admin = User.objects.create_user(
            phone="08090000001",
            password="TestPass123!",
            role=User.Role.SCHOOL_ADMIN,
        )
        SchoolMembership.objects.create(
            user=admin,
            school=self.school,
            staff_role=SchoolMembership.StaffRole.SCHOOL_ADMIN,
            is_active=True,
        )
        self.client.force_login(admin)

    def start_session(self):
        return self.client.post(
            reverse("school_admin_start_new_session"),
            {"new_session_name": "2027/2028"},
        )

    def make_third_current(self):
        AcademicTerm.objects.filter(
            school=self.school,
            session=self.session_obj,
        ).update(is_current=False)

        return AcademicTerm.objects.create(
            school=self.school,
            session=self.session_obj,
            name="THIRD",
            is_current=True,
            is_active=True,
        )

    def test_new_session_blocked_before_third_term(self):
        self.login_admin()

        response = self.start_session()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            AcademicSession.objects.filter(
                school=self.school,
                name="2027/2028",
            ).exists()
        )

    def test_unpublished_third_term_blocks_new_session(self):
        self.login_admin()
        self.make_third_current()

        response = self.start_session()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            AcademicSession.objects.filter(
                school=self.school,
                name="2027/2028",
            ).exists()
        )


def _prepare_third_term(self):
    AcademicTerm.objects.filter(
        school=self.school,
        session=self.session_obj,
    ).update(is_current=False)

    third, _ = AcademicTerm.objects.get_or_create(
        school=self.school,
        session=self.session_obj,
        name="THIRD",
        defaults={"is_active": True},
    )
    third.is_current = True
    third.is_active = True
    third.save()

    ClassResultPublication.objects.get_or_create(
        school=self.school,
        school_class=self.school_class,
        session=self.session_obj,
        term=third,
        defaults={"is_published": True},
    )

    return third


def test_pending_promotion_blocks_new_session(self):
    self.login_admin()
    self._prepare_third_term()

    StudentAnnualSummary.objects.create(
        school=self.school,
        student=self.student,
        school_class=self.school_class,
        session=self.session_obj,
        promotion_status="PENDING",
    )

    response = self.start_session()

    self.assertEqual(response.status_code, 302)
    self.assertFalse(
        AcademicSession.objects.filter(
            school=self.school,
            name="2027/2028",
        ).exists()
    )


def test_repeat_student_stays_in_same_class(self):
    self.login_admin()
    self._prepare_third_term()

    StudentAnnualSummary.objects.create(
        school=self.school,
        student=self.student,
        school_class=self.school_class,
        session=self.session_obj,
        promotion_status="REPEAT",
    )

    old_class = self.student.current_class
    self.start_session()

    self.student.refresh_from_db()

    self.assertEqual(self.student.current_class, old_class)
    self.assertEqual(self.student.status, self.student.Status.ACTIVE)


def test_promoted_student_moves_to_next_class(self):
    self.login_admin()
    self._prepare_third_term()

    next_class = SchoolClass.objects.create(
        section=self.section,
        name="Primary 6",
        order=6,
    )

    ClassResultPublication.objects.create(
        school=self.school,
        school_class=next_class,
        session=self.session_obj,
        term=AcademicTerm.objects.get(
            school=self.school,
            session=self.session_obj,
            name="THIRD",
        ),
        is_published=True,
    )

    StudentAnnualSummary.objects.create(
        school=self.school,
        student=self.student,
        school_class=self.school_class,
        session=self.session_obj,
        promotion_status="PROMOTED",
    )

    self.start_session()

    self.student.refresh_from_db()

    self.assertEqual(self.student.current_class, next_class)
    self.assertEqual(self.student.status, self.student.Status.ACTIVE)


def test_graduated_student_becomes_graduated(self):
    self.login_admin()
    self._prepare_third_term()

    StudentAnnualSummary.objects.create(
        school=self.school,
        student=self.student,
        school_class=self.school_class,
        session=self.session_obj,
        promotion_status="GRADUATED",
    )

    self.start_session()

    self.student.refresh_from_db()

    self.assertEqual(
        self.student.status,
        self.student.Status.GRADUATED,
    )


AcademicLifecycleProtectionTests._prepare_third_term = _prepare_third_term
AcademicLifecycleProtectionTests.test_pending_promotion_blocks_new_session = test_pending_promotion_blocks_new_session
AcademicLifecycleProtectionTests.test_repeat_student_stays_in_same_class = test_repeat_student_stays_in_same_class
AcademicLifecycleProtectionTests.test_promoted_student_moves_to_next_class = test_promoted_student_moves_to_next_class
AcademicLifecycleProtectionTests.test_graduated_student_becomes_graduated = test_graduated_student_becomes_graduated

