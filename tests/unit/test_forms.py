"""Unit tests for forms in General and Seller apps."""

from django.contrib.auth.hashers import check_password
from django.test import TestCase

from General.forms import (
    ForgotPasswordEmailForm,
    RegisterForm,
    SecurityQuestionForm,
    SecurityQuestionResetForm,
)
from General.views import (
    ForgotPasswordEmailForm as ViewForgotPasswordEmailForm,
    SecurityQuestionResetForm as ViewSecurityQuestionResetForm,
)
from Seller.forms import SellerWebhookForm
from Seller.models import SellerProfile
from tests.helpers import create_seller


class GeneralFormsUnitTests(TestCase):
    """Unit tests for General/forms.py and form classes in General/views.py."""

    def test_forgot_password_email_form_valid_and_invalid_email(self):
        valid_form = ForgotPasswordEmailForm(data={"email": "user@example.com"})
        self.assertTrue(valid_form.is_valid())

        invalid_form = ForgotPasswordEmailForm(data={"email": "not-an-email"})
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("email", invalid_form.errors)

        view_form = ViewForgotPasswordEmailForm(data={"email": "user@example.com"})
        self.assertTrue(view_form.is_valid())

    def test_register_form_valid_saves_hashed_password_and_security_answer(self):
        """RegisterForm hashes password and lowercases+hashes security_answer on save."""
        form = RegisterForm(
            data={
                "email": "regform@example.com",
                "first_name": "Reg",
                "last_name": "User",
                "phone": "555-0100",
                "role": "buyer",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
                "security_question": "In what city were you born?",
                "security_answer": "  Austin  ",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        user_uncommitted = form.save(commit=False)
        self.assertIsNone(user_uncommitted.pk)

        user = form.save(commit=True)
        self.assertIsNotNone(user.pk)
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertTrue(check_password("austin", user.security_answer_hash))

    def test_register_form_password_mismatch_adds_error_on_password2(self):
        form = RegisterForm(
            data={
                "email": "mismatch@example.com",
                "first_name": "Reg",
                "last_name": "User",
                "phone": "",
                "role": "buyer",
                "password1": "StrongPass123!",
                "password2": "DifferentPass123!",
                "security_question": "In what city were you born?",
                "security_answer": "Boston",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Passwords do not match.", form.errors.get("password2", []))

    def test_security_question_form_normalizes_answer_and_rejects_whitespace(self):
        """SecurityQuestionForm strips and lowercases answer, rejecting blank/whitespace."""
        valid = SecurityQuestionForm(
            data={
                "security_question": "What was the name of your first pet?",
                "security_answer": "  Fluffy  ",
            }
        )
        self.assertTrue(valid.is_valid())
        self.assertEqual(valid.cleaned_data["security_answer"], "fluffy")

        whitespace_only = SecurityQuestionForm(
            data={
                "security_question": "What was the name of your first pet?",
                "security_answer": "   ",
            }
        )
        whitespace_only.fields["security_answer"].strip = False
        self.assertFalse(whitespace_only.is_valid())
        self.assertIn("security_answer", whitespace_only.errors)

    def test_security_question_reset_form_valid_and_mismatch(self):
        """SecurityQuestionResetForm validates matching passwords and password strength."""
        for form_cls in (SecurityQuestionResetForm, ViewSecurityQuestionResetForm):
            valid = form_cls(
                data={
                    "answer": "austin",
                    "new_password1": "ValidNewPass123!",
                    "new_password2": "ValidNewPass123!",
                }
            )
            self.assertTrue(valid.is_valid(), valid.errors)

            mismatch = form_cls(
                data={
                    "answer": "austin",
                    "new_password1": "ValidNewPass123!",
                    "new_password2": "OtherNewPass123!",
                }
            )
            self.assertFalse(mismatch.is_valid())
            self.assertIn("Passwords do not match.", mismatch.errors.get("new_password2", []))


class SellerFormsUnitTests(TestCase):
    """Unit tests for SellerWebhookForm in Seller/forms.py."""

    def setUp(self):
        self.seller = create_seller(email="webhook_form@example.com")
        self.profile = SellerProfile.objects.get(user=self.seller)

    def test_seller_webhook_form_disabled_without_url_is_valid(self):
        form = SellerWebhookForm(
            data={"webhook_url": "", "webhook_secret": "", "webhook_enabled": False},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_seller_webhook_form_enabled_with_valid_url_saves(self):
        form = SellerWebhookForm(
            data={
                "webhook_url": "https://example.com/hooks/orders",
                "webhook_secret": "s3cr3t",
                "webhook_enabled": True,
            },
            instance=self.profile,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertTrue(saved.webhook_enabled)
        self.assertEqual(saved.webhook_url, "https://example.com/hooks/orders")

    def test_seller_webhook_form_enabled_without_url_adds_error(self):
        """Enabling webhooks without providing a webhook_url fails validation."""
        form = SellerWebhookForm(
            data={
                "webhook_url": "",
                "webhook_secret": "s3cr3t",
                "webhook_enabled": True,
            },
            instance=self.profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn(
            "Webhook URL is required when webhooks are enabled.",
            form.errors.get("webhook_url", []),
        )
