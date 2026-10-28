"""Unit tests for every view in Admin/views.py (one test class per view)."""

import unittest

from django.contrib.auth.hashers import check_password

from General.models import Notification, User
from tests.helpers import (
    PASSWORD,
    PassItOnTestCase,
    make_admin,
    make_book,
    make_buyer,
    make_flag,
    make_order,
    make_return,
    make_seller,
)

# Every staff_required URL that renders successfully on GET.
STAFF_GET_URLS = [
    "/staff/",
    "/staff/dashboard/",
    "/staff/reports/",
    "/staff/staff/sellers/approvals/",
    "/staff/staff/buyers/approvals/",
    "/staff/users/list/",
    "/staff/books/",
    "/staff/inventory/",
    "/staff/returns/",
    "/staff/payments/",
    "/staff/notifications/",
    "/staff/audit/",
    "/staff/settings/",
    "/staff/stewards/",
    "/staff/audit/legacy/",
]


class AdminViewTestCase(PassItOnTestCase):
    def setUp(self):
        self.admin = self.login(make_admin(first_name="Ada", last_name="Admin"))


class StaffLoginViewTests(PassItOnTestCase):
    def test_staff_login_redirects_to_shared_login_page(self):
        response = self.client.get("/staff/login/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)


class StaffLogoutViewTests(PassItOnTestCase):
    def test_staff_logout_logs_out_and_redirects_to_staff_login(self):
        self.login(make_admin())
        response = self.client.get("/staff/logout/")
        self.assertRedirects(response, "/staff/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_logout_anonymous_is_harmless(self):
        response = self.client.get("/staff/logout/")
        self.assertRedirects(response, "/staff/login/", fetch_redirect_response=False)


class StaffRequiredDecoratorTests(PassItOnTestCase):
    def test_staff_required_anonymous_redirects_to_staff_login_with_next(self):
        for url in STAFF_GET_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"/staff/login/?next={url}")

    def test_staff_required_next_param_is_url_quoted(self):
        response = self.client.get("/staff/books/?page=2")
        self.assertEqual(response["Location"], "/staff/login/?next=/staff/books/%3Fpage%3D2")

    def test_staff_required_buyer_is_logged_out_and_redirected(self):
        self.login(make_buyer())
        response = self.client.get("/staff/dashboard/")
        self.assertRedirects(response, "/staff/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_required_seller_is_rejected(self):
        self.login(make_seller())
        self.assertRedirects(self.client.get("/staff/books/"), "/staff/login/", fetch_redirect_response=False)

    def test_staff_required_admin_role_without_is_staff_is_rejected(self):
        self.login(make_admin(is_staff=False))
        response = self.client.get("/staff/dashboard/")
        self.assertRedirects(response, "/staff/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_required_is_staff_without_admin_role_is_rejected(self):
        self.login(make_buyer(is_staff=True))
        self.assertRedirects(self.client.get("/staff/dashboard/"), "/staff/login/", fetch_redirect_response=False)

    def test_staff_required_admin_can_open_every_staff_page(self):
        self.login(make_admin())
        for url in STAFF_GET_URLS:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_staff_required_post_only_endpoints_reject_anonymous(self):
        target = make_buyer(approved=False)
        for url in (
            f"/staff/staff/buyers/approve/{target.id}/",
            f"/staff/users/{target.id}/toggle-freeze/",
        ):
            with self.subTest(url=url):
                response = self.client.post(url)
                self.assertTrue(response["Location"].startswith("/staff/login/?next="))
        target.refresh_from_db()
        self.assertFalse(target.buyer_approved)
        self.assertTrue(target.is_active)


class AdminDashboardViewTests(AdminViewTestCase):
    def test_admin_dashboard_empty_site_has_zero_metrics(self):
        response = self.client.get("/staff/dashboard/")
        metrics = response.context["metrics"]
        self.assertEqual(metrics["total_users"], "1")
        self.assertEqual(metrics["orders_month"], "0")
        self.assertEqual(metrics["revenue_month"], "$0")
        self.assertEqual(metrics["returns_month_note"], "0.0% return rate")
        self.assertEqual(response.context["steward_flags"], [])
        # User (AbstractBaseUser) has no get_full_name(), so the view always falls back to email.
        self.assertEqual(response.context["admin_name"], self.admin.email)

    def test_admin_dashboard_metrics_count_users_approvals_flags_and_revenue(self):
        seller = make_seller()
        pending_seller_1 = make_seller(approved=False)
        make_seller(approved=False)
        buyer = make_buyer()
        pending_buyer = make_buyer(approved=False)

        make_book(seller, stock=5)
        make_book(seller, stock=0)
        make_book(seller, stock=3, is_active=False)

        paid = make_order(buyer, [], status="paid", subtotal_cents=123456, total_cents=123456)
        make_order(buyer, [], status="pending", subtotal_cents=50000, total_cents=50000)
        make_return(paid)

        make_flag(buyer, target=seller, flag_type="other", status="open")
        make_flag(buyer, target=seller, flag_type="other", status="reviewing")
        make_flag(buyer, target=seller, flag_type="other", status="resolved")

        response = self.client.get("/staff/dashboard/")
        metrics = response.context["metrics"]
        self.assertEqual(metrics["total_users"], "6")
        self.assertEqual(metrics["active_users_30d"], "1")  # only the logged-in admin has last_login
        self.assertEqual(metrics["active_users_note"], "17% of total")
        self.assertEqual(metrics["total_users_note"], "+6 this week")
        self.assertEqual(metrics["pending_seller_approvals"], "2")
        self.assertEqual(metrics["pending_buyer_approvals"], "1")
        self.assertEqual(metrics["open_flags"], "2")
        self.assertEqual(metrics["books_listed"], "2")
        self.assertEqual(metrics["books_listed_note"], "+2 today")
        self.assertEqual(metrics["books_available"], "2")
        self.assertEqual(metrics["orders_month"], "2")
        self.assertEqual(metrics["orders_month_note"], "2 today")
        self.assertEqual(metrics["returns_month"], "1")
        self.assertEqual(metrics["returns_month_note"], "50.0% return rate")
        self.assertEqual(metrics["revenue_month"], "$1,235")
        self.assertIn(pending_seller_1, list(response.context["pending_sellers_preview"]))
        self.assertEqual(list(response.context["pending_buyers_preview"]), [pending_buyer])

    def test_admin_dashboard_steward_and_payment_flag_rows(self):
        seller = make_seller()
        reporter = make_buyer()
        make_flag(reporter, target=seller, flag_type="steward_abuse", details="too many free books")
        make_flag(reporter, flag_type="Payment fraud", status="reviewing", details="")
        response = self.client.get("/staff/dashboard/")

        steward_rows = response.context["steward_flags"]
        self.assertEqual(len(steward_rows), 1)
        self.assertEqual(steward_rows[0]["username"], seller.email)
        self.assertEqual(steward_rows[0]["user_id"], seller.id)
        self.assertEqual(steward_rows[0]["reason"], "too many free books")
        self.assertEqual(steward_rows[0]["severity"], "Medium")
        self.assertEqual(steward_rows[0]["status"], "Open")

        payment_rows = response.context["payment_flags"]
        self.assertEqual(len(payment_rows), 1)
        self.assertEqual(payment_rows[0]["username"], "—")
        self.assertEqual(payment_rows[0]["user_id"], "—")
        self.assertEqual(payment_rows[0]["signal"], "—")
        self.assertEqual(payment_rows[0]["status"], "Triage")

    def test_admin_dashboard_recent_listings_show_seller_or_dash(self):
        seller = make_seller()
        make_book(seller, title="Owned")
        make_book(None, title="Orphan")
        make_book(seller, title="Hidden", is_active=False)
        listings = {row["title"]: row for row in self.client.get("/staff/dashboard/").context["recent_listings"]}
        self.assertEqual(set(listings), {"Owned", "Orphan"})
        self.assertEqual(listings["Owned"]["seller"], seller.email)
        self.assertEqual(listings["Orphan"]["seller"], "—")
        self.assertEqual(listings["Owned"]["status"], "Live")

    def test_admin_dashboard_root_url_is_alias(self):
        self.assertTemplateUsed(self.client.get("/staff/"), "dashboard/admin_dashboard.html")

    def test_admin_dashboard_admin_without_name_falls_back_to_email(self):
        nameless = self.login(make_admin(first_name="", last_name=""))
        self.assertEqual(self.client.get("/staff/dashboard/").context["admin_name"], nameless.email)


class SellerApprovalsViewTests(AdminViewTestCase):
    def test_seller_approvals_splits_pending_and_approved(self):
        pending = make_seller(approved=False)
        approved = make_seller()
        make_buyer(approved=False)
        response = self.client.get("/staff/staff/sellers/approvals/")
        self.assertEqual(list(response.context["pending_sellers"]), [pending])
        self.assertEqual(list(response.context["approved_sellers"]), [approved])


class BuyerApprovalsViewTests(AdminViewTestCase):
    def test_buyer_approvals_lists_only_pending_buyers(self):
        pending = make_buyer(approved=False)
        make_buyer()
        make_seller(approved=False)
        response = self.client.get("/staff/staff/buyers/approvals/")
        self.assertEqual(list(response.context["pending_buyers"]), [pending])


class ApproveSellerViewTests(AdminViewTestCase):
    def test_approve_seller_get_redirects_without_change(self):
        seller = make_seller(approved=False)
        response = self.client.get(f"/staff/staff/sellers/approve/{seller.id}/")
        self.assertRedirects(response, "/staff/staff/sellers/approvals/", fetch_redirect_response=False)
        seller.refresh_from_db()
        self.assertFalse(seller.seller_approved)

    def test_approve_seller_post_approves_and_timestamps(self):
        seller = make_seller(approved=False)
        response = self.client.post(f"/staff/staff/sellers/approve/{seller.id}/")
        self.assertRedirects(response, "/staff/staff/sellers/approvals/", fetch_redirect_response=False)
        seller.refresh_from_db()
        self.assertTrue(seller.seller_approved)
        self.assertIsNotNone(seller.seller_approved_at)
        self.assertMessage(response, f"Seller account for {seller.email} has been approved.")

    def test_approve_seller_post_buyer_id_returns_404(self):
        buyer = make_buyer(approved=False)
        self.assertEqual(self.client.post(f"/staff/staff/sellers/approve/{buyer.id}/").status_code, 404)
        buyer.refresh_from_db()
        self.assertFalse(buyer.seller_approved)

    def test_approve_seller_post_missing_user_returns_404(self):
        self.assertEqual(self.client.post("/staff/staff/sellers/approve/999999/").status_code, 404)


class ApproveBuyerViewTests(AdminViewTestCase):
    def test_approve_buyer_get_redirects_without_change(self):
        buyer = make_buyer(approved=False)
        response = self.client.get(f"/staff/staff/buyers/approve/{buyer.id}/")
        self.assertRedirects(response, "/staff/staff/buyers/approvals/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        self.assertFalse(buyer.buyer_approved)

    def test_approve_buyer_post_approves_and_timestamps(self):
        buyer = make_buyer(approved=False)
        response = self.client.post(f"/staff/staff/buyers/approve/{buyer.id}/")
        self.assertRedirects(response, "/staff/staff/buyers/approvals/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        self.assertTrue(buyer.buyer_approved)
        self.assertIsNotNone(buyer.buyer_approved_at)
        self.assertMessage(response, "has been approved")

    def test_approve_buyer_post_seller_id_returns_404(self):
        seller = make_seller(approved=False)
        self.assertEqual(self.client.post(f"/staff/staff/buyers/approve/{seller.id}/").status_code, 404)

    def test_approve_buyer_post_missing_user_returns_404(self):
        self.assertEqual(self.client.post("/staff/staff/buyers/approve/999999/").status_code, 404)


class ReportsFlagsViewTests(AdminViewTestCase):
    def test_reports_flags_rows_describe_target_and_status(self):
        reporter = make_buyer()
        seller = make_seller()
        user_flag = make_flag(reporter, target=seller, flag_type="harassment", status="reviewing")
        orphan_book = make_book(None)
        book_flag = make_flag(reporter, book=orphan_book, flag_type="", status="", details="")
        bare_flag = make_flag(reporter, flag_type="other", status="weird")

        rows = {row["flag_id"]: row for row in self.client.get("/staff/reports/").context["all_flags"]}
        self.assertEqual(rows[f"F{user_flag.id}"]["target"], seller.email)
        self.assertEqual(rows[f"F{user_flag.id}"]["status"], "Triage")
        self.assertEqual(rows[f"F{user_flag.id}"]["type"], "harassment")
        self.assertEqual(rows[f"F{book_flag.id}"]["target"], f"Book ID {orphan_book.id}")
        self.assertEqual(rows[f"F{book_flag.id}"]["type"], "—")
        self.assertEqual(rows[f"F{book_flag.id}"]["reason"], "—")
        self.assertEqual(rows[f"F{book_flag.id}"]["status"], "Open")
        self.assertEqual(rows[f"F{bare_flag.id}"]["target"], "—")
        self.assertEqual(rows[f"F{bare_flag.id}"]["status"], "weird")

    def test_reports_flags_newest_first(self):
        reporter = make_buyer()
        first = make_flag(reporter)
        second = make_flag(reporter)
        ids = [row["flag_id"] for row in self.client.get("/staff/reports/").context["all_flags"]]
        self.assertEqual(ids[0], f"F{second.id}")
        self.assertIn(f"F{first.id}", ids)


class AdminUsersViewTests(AdminViewTestCase):
    def test_admin_users_lists_all_users(self):
        buyer = make_buyer()
        seller = make_seller(approved=False)
        users = list(self.client.get("/staff/users/list/").context["users"])
        self.assertEqual(set(users), {self.admin, buyer, seller})


class ToggleUserFreezeViewTests(AdminViewTestCase):
    def test_toggle_user_freeze_get_is_noop(self):
        buyer = make_buyer()
        response = self.client.get(f"/staff/users/{buyer.id}/toggle-freeze/")
        self.assertRedirects(response, "/staff/users/list/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        self.assertTrue(buyer.is_active)

    def test_toggle_user_freeze_own_account_refused(self):
        response = self.client.post(f"/staff/users/{self.admin.id}/toggle-freeze/")
        self.assertRedirects(response, "/staff/users/list/", fetch_redirect_response=False)
        self.assertMessage(response, "You cannot freeze your own admin account.")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_toggle_user_freeze_twice_freezes_then_unfreezes(self):
        buyer = make_buyer()
        response = self.client.post(f"/staff/users/{buyer.id}/toggle-freeze/")
        self.assertMessage(response, "has been frozen")
        buyer.refresh_from_db()
        self.assertFalse(buyer.is_active)
        response = self.client.post(f"/staff/users/{buyer.id}/toggle-freeze/")
        self.assertMessage(response, "has been unfrozen")
        buyer.refresh_from_db()
        self.assertTrue(buyer.is_active)

    def test_toggle_user_freeze_missing_user_returns_404(self):
        self.assertEqual(self.client.post("/staff/users/999999/toggle-freeze/").status_code, 404)


class AdminBooksViewTests(AdminViewTestCase):
    def test_admin_books_lists_active_and_frozen_books(self):
        live = make_book(make_seller())
        frozen = make_book(None, is_active=False)
        books = list(self.client.get("/staff/books/").context["books"])
        self.assertEqual(set(books), {live, frozen})


class ToggleBookFreezeViewTests(AdminViewTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(make_seller(), title="Suspicious Listing")

    def test_toggle_book_freeze_get_is_noop(self):
        response = self.client.get(f"/staff/books/{self.book.id}/toggle-freeze/")
        self.assertRedirects(response, "/staff/books/", fetch_redirect_response=False)
        self.book.refresh_from_db()
        self.assertTrue(self.book.is_active)

    def test_toggle_book_freeze_twice_freezes_then_unfreezes(self):
        response = self.client.post(f"/staff/books/{self.book.id}/toggle-freeze/")
        self.assertMessage(response, "Book 'Suspicious Listing' has been frozen.")
        self.book.refresh_from_db()
        self.assertFalse(self.book.is_active)
        response = self.client.post(f"/staff/books/{self.book.id}/toggle-freeze/")
        self.assertMessage(response, "now unfrozen and visible")
        self.book.refresh_from_db()
        self.assertTrue(self.book.is_active)

    def test_toggle_book_freeze_missing_book_returns_404(self):
        self.assertEqual(self.client.post("/staff/books/999999/toggle-freeze/").status_code, 404)


class AdminInventoryViewTests(AdminViewTestCase):
    def test_admin_inventory_lists_rows_ordered_by_title(self):
        seller = make_seller()
        make_book(seller, title="Zoology")
        make_book(seller, title="Anatomy")
        make_book(seller, title="No Inventory", with_inventory=False)
        rows = list(self.client.get("/staff/inventory/").context["inventory"])
        self.assertEqual([inv.book.title for inv in rows], ["Anatomy", "Zoology"])


class AdminReturnsViewTests(AdminViewTestCase):
    def test_admin_returns_lists_return_requests(self):
        order = make_order(make_buyer(), [(make_book(make_seller()), 1)])
        rr = make_return(order)
        self.assertEqual(list(self.client.get("/staff/returns/").context["returns"]), [rr])


class AdminPaymentsViewTests(AdminViewTestCase):
    def test_admin_payments_only_includes_paid_shipped_delivered(self):
        buyer = make_buyer()
        included = {make_order(buyer, [], status=s) for s in ("paid", "shipped", "delivered")}
        for status in ("pending", "cancelled", "refunded"):
            make_order(buyer, [], status=status)
        payments = set(self.client.get("/staff/payments/").context["payments"])
        self.assertEqual(payments, included)


class AdminNotificationsViewTests(AdminViewTestCase):
    def test_admin_notifications_lists_notifications(self):
        note = Notification.objects.create(user=make_buyer(), type="flag", message="Listing flagged")
        self.assertEqual(list(self.client.get("/staff/notifications/").context["notifications"]), [note])


class AdminAuditLogsViewTests(AdminViewTestCase):
    def test_admin_audit_logs_lists_flag_reports(self):
        flag = make_flag(make_buyer())
        self.assertEqual(list(self.client.get("/staff/audit/").context["audit_logs"]), [flag])


class AdminSettingsViewTests(AdminViewTestCase):
    def test_admin_settings_get_provides_question_choices(self):
        response = self.client.get("/staff/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["security_question_choices"]), 3)
        self.assertEqual(response.context["selected_security_question"], "")
        self.assertEqual(response.context["nav_active"], "settings")

    def test_admin_settings_security_missing_fields_shows_errors(self):
        response = self.client.post("/staff/settings/", {"action": "security", "security_question": "", "security_answer": " "})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Please choose a security question.", response.context["security_error"])
        self.assertIn("Please enter a security answer.", response.context["security_error"])

    def test_admin_settings_security_success_stores_lowercased_hash(self):
        response = self.client.post(
            "/staff/settings/", {"action": "security", "security_question": "city", "security_answer": " Paris "}
        )
        self.assertRedirects(response, "/staff/settings/", fetch_redirect_response=False)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.security_question, "city")
        self.assertTrue(check_password("paris", self.admin.security_answer_hash))
        self.assertEqual(self.client.get("/staff/settings/").context["selected_security_question"], "city")

    def test_admin_settings_password_success_keeps_session(self):
        response = self.client.post(
            "/staff/settings/",
            {"action": "password", "current_password": PASSWORD,
             "new_password": "N3w-admin-pass", "confirm_password": "N3w-admin-pass"},
        )
        self.assertRedirects(response, "/staff/settings/", fetch_redirect_response=False)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password("N3w-admin-pass"))
        self.assertEqual(self.client.get("/staff/settings/").status_code, 200)

    def test_admin_settings_unknown_action_rerenders(self):
        self.assertEqual(self.client.post("/staff/settings/", {"action": "bogus"}).status_code, 200)

    # BUG: the error branch does `ctx["passwrod_error"] - " ".join(errs)` (typo + minus), raising KeyError (500).
    @unittest.expectedFailure
    def test_admin_settings_password_wrong_current_shows_error(self):
        response = self.client.post(
            "/staff/settings/",
            {"action": "password", "current_password": "wrong",
             "new_password": "short", "confirm_password": "other"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Current password is incorrect.", response.context["password_error"])
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password(PASSWORD))


class StaticAdminPagesViewTests(AdminViewTestCase):
    def test_steward_application_renders(self):
        self.assertTemplateUsed(self.client.get("/staff/stewards/"), "stewards/stewardApplication.html")

    def test_activity_logs_renders(self):
        self.assertTemplateUsed(self.client.get("/staff/audit/legacy/"), "audit/activityLogs.html")

    # BUG: users/userMonitering.html types `{$ if ... %}` so its {% endif %} is orphaned (TemplateSyntaxError).
    @unittest.expectedFailure
    def test_user_monitoring_renders(self):
        self.assertEqual(self.client.get("/staff/users/").status_code, 200)

    # BUG: moderation/abuseDetection.html uses `=="HIGH"` without spaces (TemplateSyntaxError).
    @unittest.expectedFailure
    def test_abuse_detection_renders(self):
        self.assertEqual(self.client.get("/staff/moderation/abuse/").status_code, 200)

    # BUG: disputes/returnDisputes.html has an unterminated {% url 'admin_stewards %} (TemplateSyntaxError).
    @unittest.expectedFailure
    def test_return_disputes_renders(self):
        self.assertEqual(self.client.get("/staff/disputes/").status_code, 200)


class FrozenUserLoginTests(AdminViewTestCase):
    def test_frozen_user_cannot_log_in(self):
        """Freezing via the admin toggle sets is_active=False, which blocks authentication."""
        buyer = make_buyer()
        self.client.post(f"/staff/users/{buyer.id}/toggle-freeze/")
        self.client.logout()
        response = self.login_via_form(buyer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], "Invalid email or password.")
        self.assertFalse(User.objects.get(pk=buyer.pk).is_active)
