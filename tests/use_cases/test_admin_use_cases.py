"""Use case tests for Admin role (Use Cases 12 and 13)."""

from django.test import TestCase
from django.urls import reverse

from Admin.models import FlagReport
from General.models import Book, User
from tests.helpers import (
    create_admin_user,
    create_book,
    create_buyer,
    create_seller,
)


class AdminUseCasesTests(TestCase):
    """End-to-end use case tests for Admin flows (UC12–UC13)."""

    def setUp(self):
        self.admin = create_admin_user(email="uc_admin@example.com")
        self.pending_buyer = create_buyer(email="uc12_buyer@example.com", buyer_approved=False)
        self.pending_seller = create_seller(email="uc12_seller@example.com", seller_approved=False)

    # --- Use Case 12: Admin approves new user accounts ---
    def test_uc12_admin_approves_new_user_accounts_approves_pending_buyer_and_seller(self):
        """UC12: Admin views pending buyer and seller approval queues and approves both accounts."""
        self.client.force_login(self.admin)

        # View pending queues
        b_queue = self.client.get(reverse("buyer_approvals"))
        self.assertEqual(b_queue.status_code, 200)
        self.assertIn(self.pending_buyer, b_queue.context["pending_buyers"])

        s_queue = self.client.get(reverse("seller_approvals"))
        self.assertEqual(s_queue.status_code, 200)
        self.assertIn(self.pending_seller, s_queue.context["pending_sellers"])

        # Approve buyer
        resp_b = self.client.post(reverse("approve_buyer", kwargs={"user_id": self.pending_buyer.id}))
        self.assertRedirects(resp_b, reverse("buyer_approvals"))
        self.pending_buyer.refresh_from_db()
        self.assertTrue(self.pending_buyer.buyer_approved)
        self.assertIsNotNone(self.pending_buyer.buyer_approved_at)

        # Approve seller
        resp_s = self.client.post(reverse("approve_seller", kwargs={"user_id": self.pending_seller.id}))
        self.assertRedirects(resp_s, reverse("seller_approvals"))
        self.pending_seller.refresh_from_db()
        self.assertTrue(self.pending_seller.seller_approved)
        self.assertIsNotNone(self.pending_seller.seller_approved_at)

    def test_uc12_admin_approves_new_user_accounts_wrong_role_id_or_unauthorized_user_rejected(self):
        """UC12 (Negative): Approving a seller ID via approve_buyer returns 404; non-admin is redirected."""
        self.client.force_login(self.admin)
        resp_wrong_role = self.client.post(
            reverse("approve_buyer", kwargs={"user_id": self.pending_seller.id})
        )
        self.assertEqual(resp_wrong_role.status_code, 404)

        self.client.force_login(self.pending_buyer)
        resp_unauth = self.client.post(
            reverse("approve_seller", kwargs={"user_id": self.pending_seller.id})
        )
        self.assertRedirects(resp_unauth, reverse("staff_login"), fetch_redirect_response=False)
        self.pending_seller.refresh_from_db()
        self.assertFalse(self.pending_seller.seller_approved)

    # --- Use Case 13: Admin checks for suspicious activity ---
    def test_uc13_admin_checks_for_suspicious_activity_reviews_flags_audit_logs_and_freezes_entities(self):
        """
        UC13: Admin inspects dashboard flags (steward/payment), reports_flags, abuse_detection,
        user_monitoring, and audit_logs, then freezes a flagged book and a suspicious user account.
        """
        suspect_seller = create_seller(email="suspect@example.com", seller_approved=True)
        suspect_book = create_book(seller=suspect_seller, title="Suspicious Listing")
        reporter = create_buyer(email="whistleblower@example.com", buyer_approved=True)

        FlagReport.objects.create(
            reporter_user=reporter,
            target_user=suspect_seller,
            flag_type="payment_anomaly",
            details="Repeated chargeback pattern",
            status="open",
        )
        FlagReport.objects.create(
            reporter_user=reporter,
            target_book=suspect_book,
            flag_type="counterfeit_book",
            details="Fake ISBN",
            status="reviewing",
        )

        self.client.force_login(self.admin)

        # 1. Check Admin Dashboard metrics & payment flags
        dash_resp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertEqual(dash_resp.context["metrics"]["open_flags"], "2")
        self.assertEqual(len(dash_resp.context["payment_flags"]), 1)

        # 2. Check Reports & Flags page and Audit Logs
        flags_resp = self.client.get(reverse("reports_flags"))
        self.assertEqual(flags_resp.status_code, 200)
        self.assertEqual(len(flags_resp.context["all_flags"]), 2)

        audit_resp = self.client.get(reverse("admin_audit_logs"))
        self.assertEqual(audit_resp.status_code, 200)
        self.assertEqual(len(audit_resp.context["audit_logs"]), 2)

        # 3. Check moderation / monitoring views
        self.assertEqual(self.client.get(reverse("abuse_detection")).status_code, 200)
        self.assertEqual(self.client.get(reverse("user_monitoring")).status_code, 200)

        # 4. Freeze the suspicious book and user
        self.client.post(reverse("toggle_book_freeze", kwargs={"book_id": suspect_book.id}))
        self.client.post(reverse("toggle_user_freeze", kwargs={"user_id": suspect_seller.id}))

        suspect_book.refresh_from_db()
        suspect_seller.refresh_from_db()
        self.assertFalse(suspect_book.is_active)
        self.assertFalse(suspect_seller.is_active)

    def test_uc13_admin_checks_for_suspicious_activity_cannot_freeze_own_admin_account(self):
        """UC13 (Negative/Edge): Admin attempting to freeze their own account is blocked."""
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("toggle_user_freeze", kwargs={"user_id": self.admin.id}))
        self.assertRedirects(resp, reverse("admin_users"))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
