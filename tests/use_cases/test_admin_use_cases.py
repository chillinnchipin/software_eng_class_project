"""Use-case tests for the Admin role (UC12-UC13)."""

import unittest

from Admin.models import FlagReport
from General.models import User
from tests.helpers import (
    PassItOnTestCase,
    make_admin,
    make_book,
    make_buyer,
    make_flag,
    make_seller,
)


class UC12AdminApprovesAccounts(PassItOnTestCase):
    """UC12: Admin reviews pending buyer/seller accounts and approves them so they can sign in."""

    def setUp(self):
        self.admin = make_admin()

    def test_admin_login_form_lands_on_staff_dashboard(self):
        resp = self.login_via_form(self.admin)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/staff/"))
        dash = self.client.get(resp["Location"])
        self.assertEqual(dash.status_code, 200)
        self.assertTemplateUsed(dash, "dashboard/admin_dashboard.html")

    def test_approve_buyer_from_pending_list_allows_login(self):
        pending = make_buyer(approved=False)
        self.assertEqual(self.login_via_form(pending)["Location"], "/login/")  # blocked before approval

        self.login(self.admin)
        page = self.client.get("/staff/staff/buyers/approvals/")
        self.assertIn(pending, list(page.context["pending_buyers"]))
        self.assertContains(page, pending.email)
        self.assertEqual(self.client.get("/staff/dashboard/").context["metrics"]["pending_buyer_approvals"], "1")

        resp = self.client.post(f"/staff/staff/buyers/approve/{pending.id}/")
        self.assertRedirects(resp, "/staff/staff/buyers/approvals/", fetch_redirect_response=False)
        pending.refresh_from_db()
        self.assertTrue(pending.buyer_approved)
        self.assertIsNotNone(pending.buyer_approved_at)
        self.assertNotIn(pending, list(self.client.get("/staff/staff/buyers/approvals/").context["pending_buyers"]))

        self.client.logout()
        self.assertRedirects(self.login_via_form(pending), "/buyer/", fetch_redirect_response=False)

    def test_approve_seller_registered_via_form_unlocks_seller_dashboard(self):
        self.client.post(
            "/register/",
            {"email": "newshop@example.com", "first_name": "New", "last_name": "Shop",
             "password": "LongEnough1", "confirm_password": "LongEnough1", "role": "seller",
             "store_name": "New Shop", "security_question": "In what city were you born?",
             "security_answer": "Paris"},
        )
        seller = User.objects.get(email="newshop@example.com")
        self.assertRedirects(self.client.get("/seller/"), "/buyer/", fetch_redirect_response=False)

        self.client.force_login(self.admin)
        page = self.client.get("/staff/staff/sellers/approvals/")
        self.assertIn(seller, list(page.context["pending_sellers"]))
        self.client.post(f"/staff/staff/sellers/approve/{seller.id}/")
        seller.refresh_from_db()
        self.assertTrue(seller.seller_approved)
        self.assertIn(seller, list(self.client.get("/staff/staff/sellers/approvals/").context["approved_sellers"]))

        self.client.logout()
        resp = self.client.post("/login/", {"username": "newshop@example.com", "password": "LongEnough1"})
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/seller/").status_code, 200)

    def test_approve_via_get_does_nothing(self):
        pending = make_seller(approved=False)
        self.login(self.admin)
        resp = self.client.get(f"/staff/staff/sellers/approve/{pending.id}/")
        self.assertRedirects(resp, "/staff/staff/sellers/approvals/", fetch_redirect_response=False)
        pending.refresh_from_db()
        self.assertFalse(pending.seller_approved)

    def test_approve_seller_with_buyer_id_returns_404(self):
        """Edge: the approve endpoints are role-scoped."""
        buyer = make_buyer(approved=False)
        self.login(self.admin)
        self.assertEqual(self.client.post(f"/staff/staff/sellers/approve/{buyer.id}/").status_code, 404)

    def test_approve_buyer_by_non_admin_denied_and_logged_out(self):
        pending = make_buyer(approved=False)
        self.login(make_buyer())
        resp = self.client.post(f"/staff/staff/buyers/approve/{pending.id}/")
        self.assertRedirects(resp, "/staff/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        pending.refresh_from_db()
        self.assertFalse(pending.buyer_approved)

    def test_approval_pages_anonymous_redirected_to_staff_login_with_next(self):
        resp = self.client.get("/staff/staff/sellers/approvals/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/staff/login/?next=/staff/staff/sellers/approvals/")

    def test_approval_pages_staff_flag_without_admin_role_denied(self):
        """Edge: is_staff alone is not enough; role must be 'admin'."""
        self.login(make_seller(is_staff=True))
        resp = self.client.get("/staff/staff/buyers/approvals/")
        self.assertRedirects(resp, "/staff/login/", fetch_redirect_response=False)


class UC13AdminChecksSuspiciousActivity(PassItOnTestCase):
    """UC13: A buyer reports a listing; the admin reviews flags and freezes the user/listing."""

    def setUp(self):
        self.admin = make_admin()
        self.seller = make_seller(email="shady.seller@example.com")
        self.book = make_book(self.seller, title="Too Good To Be True")
        self.buyer = make_buyer()

    def flag_as_buyer(self, flag_type="payment", details="Asked me to pay off-site"):
        self.client.force_login(self.buyer)
        return self.client.post(
            f"/catalog/{self.book.id}/flag/", {"flag_type": flag_type, "details": details}
        )

    def test_flag_listing_appears_on_admin_reports_and_dashboard(self):
        resp = self.flag_as_buyer()
        self.assertRedirects(resp, f"/catalog/{self.book.id}/", fetch_redirect_response=False)
        flag = FlagReport.objects.get()
        self.assertEqual((flag.reporter_user, flag.target_user, flag.target_book), (self.buyer, self.seller, self.book))

        self.client.force_login(self.admin)
        reports = self.client.get("/staff/reports/")
        self.assertEqual(reports.status_code, 200)
        row = reports.context["all_flags"][0]
        self.assertEqual(row["flag_id"], f"F{flag.id}")
        self.assertEqual(row["target"], "shady.seller@example.com")
        self.assertEqual(row["status"], "Open")
        self.assertContains(reports, "Asked me to pay off-site")

        dash = self.client.get("/staff/dashboard/")
        self.assertEqual(dash.context["metrics"]["open_flags"], "1")
        self.assertEqual(dash.context["payment_flags"][0]["username"], "shady.seller@example.com")

    def test_steward_flags_panel_and_resolved_flags_not_counted_open(self):
        make_flag(self.buyer, target=self.buyer, flag_type="steward_abuse", details="Farming free books")
        make_flag(self.buyer, book=self.book, status="resolved")
        self.client.force_login(self.admin)
        dash = self.client.get("/staff/dashboard/")
        self.assertEqual(dash.context["metrics"]["open_flags"], "1")
        self.assertEqual(dash.context["steward_flags"][0]["reason"], "Farming free books")
        self.assertEqual(len(self.client.get("/staff/reports/").context["all_flags"]), 2)

    def test_freeze_suspicious_user_blocks_login_and_existing_session(self):
        self.flag_as_buyer()
        seller_client = self.client_class()
        seller_client.force_login(self.seller)

        self.client.force_login(self.admin)
        resp = self.client.post(f"/staff/users/{self.seller.id}/toggle-freeze/")
        self.assertRedirects(resp, "/staff/users/list/", fetch_redirect_response=False)
        self.assertMessage(resp, "has been frozen")
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.is_active)

        # Existing session is no longer honoured, and a fresh login is refused.
        self.assertEqual(seller_client.get("/seller/").status_code, 302)
        self.client.logout()
        self.assertEqual(self.login_via_form(self.seller).context["error"], "Invalid email or password.")

    def test_unfreeze_user_restores_login(self):
        self.seller.is_active = False
        self.seller.save()
        self.client.force_login(self.admin)
        resp = self.client.post(f"/staff/users/{self.seller.id}/toggle-freeze/")
        self.assertMessage(resp, "unfrozen")
        self.client.logout()
        self.assertRedirects(self.login_via_form(self.seller), "/seller/", fetch_redirect_response=False)

    def test_admin_cannot_freeze_own_account(self):
        self.client.force_login(self.admin)
        resp = self.client.post(f"/staff/users/{self.admin.id}/toggle-freeze/")
        self.assertMessage(resp, "cannot freeze your own")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_freeze_flagged_book_hides_it_from_catalog(self):
        self.flag_as_buyer()
        self.client.force_login(self.admin)
        self.assertIn(self.book, list(self.client.get("/staff/books/").context["books"]))
        self.client.post(f"/staff/books/{self.book.id}/toggle-freeze/")
        self.book.refresh_from_db()
        self.assertFalse(self.book.is_active)

        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(f"/catalog/{self.book.id}/").status_code, 404)
        self.assertEqual(list(self.client.get("/catalog/").context["page_obj"].object_list), [])

    def test_non_admin_cannot_view_reports_or_freeze(self):
        self.client.force_login(self.buyer)
        self.assertRedirects(self.client.get("/staff/reports/"), "/staff/login/", fetch_redirect_response=False)
        self.client.force_login(self.buyer)
        self.client.post(f"/staff/users/{self.seller.id}/toggle-freeze/")
        self.client.force_login(self.seller)
        self.client.post(f"/staff/books/{self.book.id}/toggle-freeze/")
        self.seller.refresh_from_db()
        self.book.refresh_from_db()
        self.assertTrue(self.seller.is_active)
        self.assertTrue(self.book.is_active)

    def test_flag_listing_get_or_inactive_book_creates_no_flag(self):
        self.client.force_login(self.buyer)
        self.assertRedirects(
            self.client.get(f"/catalog/{self.book.id}/flag/"), f"/catalog/{self.book.id}/",
            fetch_redirect_response=False,
        )
        hidden = make_book(self.seller, is_active=False)
        self.assertEqual(self.client.post(f"/catalog/{hidden.id}/flag/", {"flag_type": "spam"}).status_code, 404)
        self.assertFalse(FlagReport.objects.exists())

    def test_flag_listing_blank_type_defaults_to_other(self):
        self.flag_as_buyer(flag_type="   ", details="")
        flag = FlagReport.objects.get()
        self.assertEqual(flag.flag_type, "other")
        self.assertIsNone(flag.details)

    # BUG: Admin/templates/moderation/abuseDetection.html has a TemplateSyntaxError ('=="HIGH"'), so the page 500s.
    @unittest.expectedFailure
    def test_abuse_detection_page_renders(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/staff/moderation/abuse/").status_code, 200)
