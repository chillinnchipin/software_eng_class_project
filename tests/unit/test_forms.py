"""Unit tests for forms in General/forms.py, General/views.py, and Seller/forms.py."""

from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.test import TestCase

from app.general import views as general_views
from app.general.forms import (
    ForgotPasswordEmailForm,
    RegisterForm,
    SecurityQuestionForm,
    SecurityQuestionResetForm,
)
from app.general.models import User
from app.seller.forms import SellerWebhookForm
from app.seller.models import SellerProfile
from tests.helpers import make_seller

QUESTION = "In what city were you born?"


def _register_data(**overrides):
    data = {
        "email": "new@example.com",
        "first_name": "New",
        "last_name": "User",
        "phone": "555-0100",
        "role": "buyer",
        "password1": "Str0ng-pass-1",
        "password2": "Str0ng-pass-1",
        "security_question": QUESTION,
        "security_answer": "  Springfield ",
    }
    data.update(overrides)
    return data


class ForgotPasswordEmailFormTests(TestCase):
    def test_forgot_password_email_form_valid_email_is_valid(self):
        self.assertTrue(ForgotPasswordEmailForm({"email": "a@example.com"}).is_valid())

    def test_forgot_password_email_form_invalid_email_has_error(self):
        form = ForgotPasswordEmailForm({"email": "not-an-email"})
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_forgot_password_email_form_blank_is_invalid(self):
        self.assertFalse(ForgotPasswordEmailForm({"email": ""}).is_valid())

    def test_views_forgot_password_email_form_duplicate_class_validates_email(self):
        """General/views.py redefines this form; the view uses the redefined copy."""
        self.assertTrue(general_views.ForgotPasswordEmailForm({"email": "a@example.com"}).is_valid())
        self.assertFalse(general_views.ForgotPasswordEmailForm({"email": "bad"}).is_valid())


class RegisterFormTests(TestCase):
    def test_register_form_valid_data_is_valid(self):
        form = RegisterForm(_register_data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_register_form_password_mismatch_adds_password2_error(self):
        form = RegisterForm(_register_data(password2="different-pass"))
        self.assertFalse(form.is_valid())
        self.assertIn("Passwords do not match.", form.errors["password2"])

    def test_register_form_invalid_security_question_choice_is_invalid(self):
        form = RegisterForm(_register_data(security_question="Favourite colour?"))
        self.assertFalse(form.is_valid())
        self.assertIn("security_question", form.errors)

    def test_register_form_invalid_role_is_invalid(self):
        form = RegisterForm(_register_data(role="superhero"))
        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)

    def test_register_form_duplicate_email_is_invalid(self):
        make_seller(email="new@example.com")
        form = RegisterForm(_register_data())
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_register_form_save_hashes_password_and_lowercases_answer(self):
        form = RegisterForm(_register_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password("Str0ng-pass-1"))
        self.assertEqual(user.security_question, QUESTION)
        self.assertTrue(check_password("springfield", user.security_answer_hash))
        self.assertFalse(check_password("  Springfield ", user.security_answer_hash))

    def test_register_form_save_commit_false_does_not_persist(self):
        form = RegisterForm(_register_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save(commit=False)
        self.assertIsNone(user.pk)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())
        self.assertTrue(user.check_password("Str0ng-pass-1"))


class SecurityQuestionFormTests(TestCase):
    def test_security_question_form_valid_answer_is_lowercased_and_stripped(self):
        form = SecurityQuestionForm({"security_question": QUESTION, "security_answer": "  Fluffy "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["security_answer"], "fluffy")

    def test_security_question_form_whitespace_answer_is_invalid(self):
        form = SecurityQuestionForm({"security_question": QUESTION, "security_answer": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("security_answer", form.errors)

    def test_security_question_form_unknown_question_is_invalid(self):
        form = SecurityQuestionForm({"security_question": "city", "security_answer": "x"})
        self.assertFalse(form.is_valid())
        self.assertIn("security_question", form.errors)

    def test_security_question_form_blank_placeholder_choice_is_invalid(self):
        form = SecurityQuestionForm({"security_question": "", "security_answer": "x"})
        self.assertFalse(form.is_valid())


class SecurityQuestionResetFormTests(TestCase):
    form_classes = (SecurityQuestionResetForm, general_views.SecurityQuestionResetForm)

    def test_reset_form_matching_strong_passwords_is_valid(self):
        for cls in self.form_classes:
            with self.subTest(cls=cls.__module__):
                form = cls({"answer": "x", "new_password1": "Str0ng-pass-1", "new_password2": "Str0ng-pass-1"})
                self.assertTrue(form.is_valid(), form.errors)

    def test_reset_form_mismatch_adds_new_password2_error(self):
        for cls in self.form_classes:
            with self.subTest(cls=cls.__module__):
                form = cls({"answer": "x", "new_password1": "Str0ng-pass-1", "new_password2": "Str0ng-pass-2"})
                self.assertFalse(form.is_valid())
                self.assertIn("Passwords do not match.", form.errors["new_password2"])

    def test_reset_form_missing_answer_is_invalid(self):
        form = SecurityQuestionResetForm({"answer": "", "new_password1": "Str0ng-pass-1", "new_password2": "Str0ng-pass-1"})
        self.assertFalse(form.is_valid())
        self.assertIn("answer", form.errors)

    def test_reset_form_short_password_without_validators_is_valid(self):
        """No AUTH_PASSWORD_VALIDATORS are configured, so validate_password accepts anything."""
        for cls in self.form_classes:
            with self.subTest(cls=cls.__module__):
                form = cls({"answer": "x", "new_password1": "a", "new_password2": "a"})
                self.assertTrue(form.is_valid())

    def test_reset_form_validator_error_becomes_non_field_error(self):
        with self.settings(AUTH_PASSWORD_VALIDATORS=[
            {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}
        ]):
            form = SecurityQuestionResetForm({"answer": "x", "new_password1": "short", "new_password2": "short"})
            self.assertFalse(form.is_valid())
            self.assertTrue(form.non_field_errors())


class SellerWebhookFormTests(TestCase):
    def setUp(self):
        self.profile = SellerProfile.objects.get(user=make_seller())

    def test_webhook_form_enabled_without_url_has_url_error(self):
        form = SellerWebhookForm({"webhook_url": "", "webhook_secret": "", "webhook_enabled": "on"}, instance=self.profile)
        self.assertFalse(form.is_valid())
        self.assertIn("Webhook URL is required when webhooks are enabled.", form.errors["webhook_url"])

    def test_webhook_form_disabled_without_url_is_valid(self):
        form = SellerWebhookForm({"webhook_url": "", "webhook_secret": ""}, instance=self.profile)
        self.assertTrue(form.is_valid(), form.errors)

    def test_webhook_form_invalid_url_is_invalid(self):
        form = SellerWebhookForm({"webhook_url": "not a url", "webhook_enabled": "on"}, instance=self.profile)
        self.assertFalse(form.is_valid())
        self.assertIn("webhook_url", form.errors)

    def test_webhook_form_valid_save_persists_settings(self):
        form = SellerWebhookForm(
            {"webhook_url": "https://hooks.example.com/x", "webhook_secret": "s3", "webhook_enabled": "on"},
            instance=self.profile,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.webhook_enabled)
        self.assertEqual(self.profile.webhook_url, "https://hooks.example.com/x")
        self.assertEqual(self.profile.webhook_secret, "s3")
