"""Integration: admin approval of new accounts, account/book freezing, and flag reports."""

from django.test import Client

from Admin.models import FlagReport
from General.models import Book, User
from tests.helpers import (
    PASSWORD,
    SECURITY_ANSWER,
    PassItOnTestCase,
    make_admin,
    make_book,
    make_buyer,
    make_seller,
)


def register(client, email, role="buyer", **extra):
    data = {
        "email": email, "first_name": "New", "last_name": "User", "password": PASSWORD,
        "confirm_password": PASSWORD, "role": role,
        "security_question": "In what city were you born?", "security_answer": SECURITY_ANSWER,
    }
    data.update(extra)
    client.post("/register/", data)
    client.get("/logout/")
    return User.objects.get(email=email)


class AccountApprovalFlowTests(PassItOnTestCase):
    def setUp(self):
        self.admin = make_admin()
        self.admin_client = Client()
        self.admin_client.force_login(self.admin)

    def test_buyer_registration_pending_in_admin_queue_then_approved_can_login(self):
        buyer = register(self.client, "pending.buyer@example.com")

        queue = self.admin_client.get("/staff/staff/buyers/approvals/")
        self.assertEqual(list(queue.context["pending_buyers"]), [buyer])
        dash = self.admin_client.get("/staff/dashboard/")
        self.assertEqual(dash.context["metrics"]["pending_buyer_approvals"], "1")
        self.assertEqual(list(dash.context["pending_buyers_preview"]), [buyer])

        self.assertRedirects(self.login_via_form(buyer), "/login/", fetch_redirect_response=False)

        resp = self.admin_client.post(f"/staff/staff/buyers/approve/{buyer.id}/")
        self.assertRedirects(resp, "/staff/staff/buyers/approvals/", fetch_redirect_response=False)
        self.assertMessage(resp, f"Buyer account for {buyer.email} has been approved.")
        buyer.refresh_from_db()
        self.assertTrue(buyer.buyer_approved)
        self.assertIsNotNone(buyer.buyer_approved_at)

        self.assertEqual(list(self.admin_client.get("/staff/staff/buyers/approvals/").context["pending_buyers"]), [])
        self.assertEqual(self.admin_client.get("/staff/dashboard/").context["metrics"]["pending_buyer_approvals"], "0")
        self.assertRedirects(self.login_via_form(buyer), "/buyer/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/catalog/").status_code, 200)

    def test_seller_registration_approved_can_reach_dashboard_and_add_books(self):
        seller = register(self.client, "pending.seller@example.com", role="seller", store_name="Pending Books")
        queue = self.admin_client.get("/staff/staff/sellers/approvals/")
        self.assertEqual(list(queue.context["pending_sellers"]), [seller])
        self.assertEqual(list(queue.context["approved_sellers"]), [])
        self.assertEqual(self.admin_client.get("/staff/dashboard/").context["metrics"]["pending_seller_approvals"], "1")

        self.login_via_form(seller)
        resp = self.client.post("/seller/inventory/add/", {"title": "Too Early", "author": "A",
                                                          "condition": "Good", "price": "5"})
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        self.assertFalse(Book.objects.filter(title="Too Early").exists())

        self.admin_client.post(f"/staff/staff/sellers/approve/{seller.id}/")
        seller.refresh_from_db()
        self.assertTrue(seller.seller_approved)
        self.assertIsNotNone(seller.seller_approved_at)
        queue = self.admin_client.get("/staff/staff/sellers/approvals/")
        self.assertEqual(list(queue.context["pending_sellers"]), [])
        self.assertEqual(list(queue.context["approved_sellers"]), [seller])

        # Existing session picks up the approval without re-login.
        self.assertEqual(self.client.get("/seller/dashboard/").status_code, 200)
        resp = self.client.post("/seller/inventory/add/", {"title": "Now Allowed", "author": "A",
                                                          "condition": "Good", "price": "5",
                                                          "quantity_available": "1"})
        self.assertRedirects(resp, "/seller/inventory/", fetch_redirect_response=False)
        self.assertEqual(Book.objects.get(title="Now Allowed").seller_user, seller)

    def test_approve_endpoints_get_request_does_not_approve(self):
        buyer, seller = make_buyer(approved=False), make_seller(approved=False)
        self.assertRedirects(self.admin_client.get(f"/staff/staff/buyers/approve/{buyer.id}/"),
                             "/staff/staff/buyers/approvals/", fetch_redirect_response=False)
        self.assertRedirects(self.admin_client.get(f"/staff/staff/sellers/approve/{seller.id}/"),
                             "/staff/staff/sellers/approvals/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        seller.refresh_from_db()
        self.assertFalse(buyer.buyer_approved)
        self.assertFalse(seller.seller_approved)

    def test_approve_endpoints_wrong_role_target_returns_404(self):
        buyer, seller = make_buyer(approved=False), make_seller(approved=False)
        self.assertEqual(self.admin_client.post(f"/staff/staff/buyers/approve/{seller.id}/").status_code, 404)
        self.assertEqual(self.admin_client.post(f"/staff/staff/sellers/approve/{buyer.id}/").status_code, 404)
        self.assertEqual(self.admin_client.post("/staff/staff/buyers/approve/999999/").status_code, 404)

    def test_approve_endpoints_non_admin_or_anonymous_cannot_approve(self):
        target = make_buyer(approved=False)
        seller_target = make_seller(approved=False)
        for actor in (make_buyer(), make_seller()):
            self.login(actor)
            resp = self.client.post(f"/staff/staff/buyers/approve/{target.id}/")
            self.assertRedirects(resp, "/staff/login/", fetch_redirect_response=False)
            self.client.force_login(actor)
            self.client.post(f"/staff/staff/sellers/approve/{seller_target.id}/")
        self.client.logout()
        resp = self.client.post(f"/staff/staff/buyers/approve/{target.id}/")
        self.assertTrue(resp["Location"].startswith("/staff/login/?next="))
        target.refresh_from_db()
        seller_target.refresh_from_db()
        self.assertFalse(target.buyer_approved)
        self.assertFalse(seller_target.seller_approved)


class AdminFreezeFlowTests(PassItOnTestCase):
    def setUp(self):
        self.admin = self.login(make_admin())

    def test_freeze_user_blocks_login_unfreeze_restores(self):
        seller = make_seller()
        other = Client()
        resp = self.client.post(f"/staff/users/{seller.id}/toggle-freeze/")
        self.assertMessage(resp, "has been frozen")
        resp = other.post("/login/", {"username": seller.email, "password": PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", other.session)
        listed = self.client.get("/staff/users/list/").context["users"]
        self.assertFalse(next(u for u in listed if u.id == seller.id).is_active)

        resp = self.client.post(f"/staff/users/{seller.id}/toggle-freeze/")
        self.assertMessage(resp, "has been unfrozen")
        resp = other.post("/login/", {"username": seller.email, "password": PASSWORD})
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)

    def test_freeze_own_admin_account_refused(self):
        resp = self.client.post(f"/staff/users/{self.admin.id}/toggle-freeze/")
        self.assertMessage(resp, "cannot freeze your own")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_freeze_endpoints_get_request_is_noop(self):
        buyer, book = make_buyer(), make_book()
        self.assertRedirects(self.client.get(f"/staff/users/{buyer.id}/toggle-freeze/"),
                             "/staff/users/list/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get(f"/staff/books/{book.id}/toggle-freeze/"),
                             "/staff/books/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        book.refresh_from_db()
        self.assertTrue(buyer.is_active)
        self.assertTrue(book.is_active)


class FlagReportFlowTests(PassItOnTestCase):
    def test_buyer_flags_book_then_admin_sees_it_in_reports_and_dashboard(self):
        seller = make_seller()
        book = make_book(seller)
        buyer = self.login(make_buyer())
        resp = self.client.post(f"/catalog/{book.id}/flag/", {"flag_type": "payment fraud",
                                                              "details": "Asked to pay off-site"})
        self.assertRedirects(resp, f"/catalog/{book.id}/", fetch_redirect_response=False)
        self.assertMessage(resp, "report was submitted")
        flag = FlagReport.objects.get()
        self.assertEqual((flag.reporter_user, flag.target_user, flag.target_book, flag.status),
                         (buyer, seller, book, "open"))

        admin_client = Client()
        admin_client.force_login(make_admin())
        rows = admin_client.get("/staff/reports/").context["all_flags"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["flag_id"], rows[0]["target"], rows[0]["status"]),
                         (f"F{flag.id}", seller.email, "Open"))
        dash = admin_client.get("/staff/dashboard/")
        self.assertEqual(dash.context["metrics"]["open_flags"], "1")
        self.assertEqual(dash.context["payment_flags"][0]["signal"], "Asked to pay off-site")
        self.assertEqual(list(admin_client.get("/staff/audit/").context["audit_logs"]), [flag])

        # Resolved flags drop out of the open count.
        flag.status = "resolved"
        flag.save()
        self.assertEqual(admin_client.get("/staff/dashboard/").context["metrics"]["open_flags"], "0")

    def test_flag_book_get_request_creates_nothing(self):
        book = make_book(make_seller())
        self.login(make_buyer())
        resp = self.client.get(f"/catalog/{book.id}/flag/")
        self.assertRedirects(resp, f"/catalog/{book.id}/", fetch_redirect_response=False)
        self.assertFalse(FlagReport.objects.exists())

    def test_flag_inactive_book_returns_404(self):
        book = make_book(make_seller(), is_active=False)
        self.login(make_buyer())
        self.assertEqual(self.client.post(f"/catalog/{book.id}/flag/", {"flag_type": "x"}).status_code, 404)
