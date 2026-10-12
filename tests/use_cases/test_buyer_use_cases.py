"""Use-case tests for the Buyer role (UC01-UC06).

Each class walks one user story through the real URLs with the Django test client,
posting the same field names the templates submit.
"""

import unittest

from django.contrib.auth.hashers import check_password

from Buyer.models import CartItem, Order, ReturnRequest, Review
from General.models import Inventory, User
from tests.helpers import (
    PASSWORD,
    PassItOnTestCase,
    make_book,
    make_buyer,
    make_order,
    make_seller,
)


def register_payload(**overrides):
    data = {
        "email": "new.buyer@example.com",
        "first_name": "Nia",
        "last_name": "Reader",
        "phone": "555-0100",
        "password": "LongEnough1",
        "confirm_password": "LongEnough1",
        "role": "buyer",
        "security_question": "In what city were you born?",
        "security_answer": "Springfield",
    }
    data.update(overrides)
    return data


class UC01BuyerCreatesAccount(PassItOnTestCase):
    """UC01: A visitor registers a buyer account from /register/."""

    def test_register_buyer_valid_form_creates_buyer_and_redirects_home(self):
        resp = self.client.post("/register/", register_payload())
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        user = User.objects.get(email="new.buyer@example.com")
        self.assertEqual(user.role, "buyer")
        self.assertTrue(user.check_password("LongEnough1"))
        self.assertFalse(user.buyer_approved)
        self.assertIn("access_token", resp.cookies)

    def test_register_buyer_security_answer_stored_hashed_and_normalised(self):
        """The answer is lower-cased before hashing so later resets are case-insensitive."""
        self.client.post("/register/", register_payload(security_answer="  SpringField "))
        user = User.objects.get(email="new.buyer@example.com")
        self.assertNotEqual(user.security_answer_hash, "springfield")
        self.assertTrue(check_password("springfield", user.security_answer_hash))

    def test_register_buyer_duplicate_email_case_insensitive_rejected(self):
        make_buyer(email="taken@example.com")
        resp = self.client.post("/register/", register_payload(email="TAKEN@example.com"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already exists", resp.context["form_errors"])
        self.assertEqual(User.objects.filter(email__iexact="taken@example.com").count(), 1)

    def test_register_buyer_password_mismatch_rejected(self):
        resp = self.client.post("/register/", register_payload(confirm_password="Different1"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Passwords do not match.", resp.context["form_errors"])
        self.assertFalse(User.objects.filter(email="new.buyer@example.com").exists())

    def test_register_buyer_short_password_and_missing_fields_reports_all_errors(self):
        resp = self.client.post(
            "/register/",
            register_payload(password="short", confirm_password="short", first_name="", security_answer=""),
        )
        errors = resp.context["form_errors"]
        self.assertIn("at least 8 characters", errors)
        self.assertIn("First name is required.", errors)
        self.assertIn("Security answer is required.", errors)
        self.assertFalse(User.objects.filter(email="new.buyer@example.com").exists())

    def test_register_unknown_role_falls_back_to_buyer(self):
        """Edge: a tampered role value (e.g. 'admin') must never create an admin."""
        self.client.post("/register/", register_payload(role="admin"))
        self.assertEqual(User.objects.get(email="new.buyer@example.com").role, "buyer")

    # BUG: register() logs a new buyer straight in, bypassing the admin-approval gate that login enforces.
    @unittest.expectedFailure
    def test_register_buyer_pending_approval_not_logged_in(self):
        self.client.post("/register/", register_payload())
        resp = self.client.get("/buyer/")
        self.assertNotEqual(resp.status_code, 200)


class UC02BuyerSignsIn(PassItOnTestCase):
    """UC02: An approved buyer signs in at /login/ and lands on the buyer dashboard."""

    def test_login_approved_buyer_redirects_to_dashboard_with_jwt_cookies(self):
        buyer = make_buyer()
        resp = self.login_via_form(buyer)
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        self.assertIn("access_token", resp.cookies)
        self.assertIn("refresh_token", resp.cookies)
        dash = self.client.get("/buyer/")
        self.assertEqual(dash.status_code, 200)
        self.assertTemplateUsed(dash, "dashboard/buyer_dashboard.html")

    def test_login_honours_safe_next_url(self):
        buyer = make_buyer()
        resp = self.login_via_form(buyer, next="/buyer/orders/")
        self.assertRedirects(resp, "/buyer/orders/", fetch_redirect_response=False)

    def test_login_ignores_offsite_next_url(self):
        """Edge: an absolute off-site next URL is not followed (open-redirect guard)."""
        buyer = make_buyer()
        resp = self.login_via_form(buyer, next="https://evil.example/")
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)

    def test_login_wrong_password_shows_error_and_stays_anonymous(self):
        buyer = make_buyer()
        resp = self.login_via_form(buyer, password="wrong-password")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["error"], "Invalid email or password.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_unapproved_buyer_blocked_with_pending_message(self):
        buyer = make_buyer(approved=False)
        resp = self.login_via_form(buyer)
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertMessage(resp, "pending admin approval")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_already_authenticated_buyer_redirected_away_from_login(self):
        self.login(make_buyer())
        resp = self.client.get("/login/")
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)

    def test_logout_then_protected_page_requires_login(self):
        self.login(make_buyer())
        self.client.get("/logout/")
        resp = self.client.get("/buyer/orders/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/login/"))


class UC03BuyerBrowsesAndSearches(PassItOnTestCase):
    """UC03: A signed-in buyer browses the catalog, searches/filters, and opens a book."""

    def setUp(self):
        self.seller = make_seller()
        self.cheap = make_book(self.seller, title="Python Basics", author="Ada Lane", price_cents=800)
        self.mid = make_book(self.seller, title="Django Deep Dive", author="Grace Hope", price_cents=2500)
        self.pricey = make_book(self.seller, title="Advanced Algorithms", author="Ada Lane", price_cents=6000, stock=0)
        self.hidden = make_book(self.seller, title="Python Secrets", author="Hidden", is_active=False)
        self.login(make_buyer())

    def titles(self, resp):
        return [b.title for b in resp.context["page_obj"].object_list]

    def test_catalog_browse_lists_active_books_alphabetically(self):
        resp = self.client.get("/catalog/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.titles(resp), ["Advanced Algorithms", "Django Deep Dive", "Python Basics"])
        self.assertContains(resp, "Django Deep Dive")

    def test_catalog_search_matches_title_and_author_case_insensitively(self):
        self.assertEqual(self.titles(self.client.get("/catalog/?q=python")), ["Python Basics"])
        self.assertEqual(
            self.titles(self.client.get("/catalog/?q=ADA LANE")), ["Advanced Algorithms", "Python Basics"]
        )

    def test_catalog_search_no_results_returns_empty_page(self):
        resp = self.client.get("/catalog/?q=nonexistent-zebra")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.titles(resp), [])

    def test_catalog_price_filter_and_sort_combined(self):
        resp = self.client.get("/catalog/?min_price=5&max_price=30&sort=price_desc")
        self.assertEqual(self.titles(resp), ["Django Deep Dive", "Python Basics"])

    def test_catalog_invalid_price_filter_is_ignored(self):
        resp = self.client.get("/catalog/?min_price=abc")
        self.assertEqual(len(self.titles(resp)), 3)

    def test_catalog_in_stock_filter_excludes_sold_out(self):
        self.assertNotIn("Advanced Algorithms", self.titles(self.client.get("/catalog/?in_stock=1")))

    def test_catalog_hides_inactive_books(self):
        self.assertNotIn("Python Secrets", self.titles(self.client.get("/catalog/?q=python")))

    def test_book_detail_shows_title_author_price_and_seller(self):
        resp = self.client.get(f"/catalog/{self.mid.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Django Deep Dive")
        self.assertContains(resp, "Grace Hope")
        self.assertContains(resp, "25.00")
        self.assertEqual(
            resp.context["seller_display_name"], f"{self.seller.first_name} {self.seller.last_name}"
        )

    def test_book_detail_inactive_or_missing_book_returns_404(self):
        self.assertEqual(self.client.get(f"/catalog/{self.hidden.id}/").status_code, 404)
        self.assertEqual(self.client.get("/catalog/999999/").status_code, 404)

    def test_catalog_anonymous_visitor_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get("/catalog/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/login/"))


class UC04BuyerBuysBook(PassItOnTestCase):
    """UC04: Buyer adds a book to the cart, saves address + card, and checks out."""

    def setUp(self):
        self.seller = make_seller()
        self.book = make_book(self.seller, title="Linear Algebra", price_cents=1500, stock=5)
        self.buyer = self.login(make_buyer())

    def add_address_and_card(self):
        self.client.post(
            "/buyer/checkout/shipping/add/",
            {"label": "Dorm", "line1": "10 College Ave", "city": "Ithaca", "state": "NY",
             "postal_code": "14850", "country": "US", "is_default": "on"},
        )
        self.client.post(
            "/buyer/checkout/payments/add/",
            {"cardholder_name": "Nia Reader", "card_number": "4242 4242 4242 4242", "brand": "Visa",
             "cvv": "123", "exp_month": "12", "exp_year": "2099", "is_default": "on"},
        )
        return self.buyer.address_set.get(), self.buyer.payment_methods.get()

    def checkout(self, addr, pm, steward="0"):
        return self.client.post(
            "/buyer/checkout/",
            {"shipping_address_id": addr.id, "payment_method_id": pm.id, "steward_contribution": steward},
        )

    def test_buy_book_full_flow_creates_paid_order_and_decrements_stock(self):
        self.client.post(f"/buyer/cart/add/{self.book.id}/", {"quantity": "2"})
        cart = self.client.get("/buyer/cart/")
        self.assertEqual(cart.context["total_items"], 2)
        addr, pm = self.add_address_and_card()
        self.assertEqual(pm.last4, "4242")

        preview = self.client.get("/buyer/checkout/")
        self.assertEqual(preview.context["selected_address"], addr)
        self.assertEqual(preview.context["selected_payment"], pm)

        resp = self.checkout(addr, pm)
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(resp, f"/buyer/orders/confirmation/{order.id}/", fetch_redirect_response=False)
        self.assertEqual(order.status, "paid")
        # 2 x $15.00 + 7% tax + $2.99 fee, no steward contribution
        self.assertEqual(order.subtotal_cents, 3000)
        self.assertEqual(order.tax_cents, 210)
        self.assertEqual(order.fees_cents, 299)
        self.assertEqual(order.total_cents, 3509)
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 3)
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())

        confirmation = self.client.get(f"/buyer/orders/confirmation/{order.id}/")
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Linear Algebra")
        self.assertEqual(confirmation.context["order_total_display"], "35.09")
        self.assertEqual(confirmation.context["shipping"].shipping_city, "Ithaca")

    def test_buy_book_default_steward_contribution_added_to_total(self):
        """Edge: leaving the contribution blank adds the $2.00 default and 20 steward points."""
        self.client.post(f"/buyer/cart/add/{self.book.id}/", {"quantity": "1"})
        addr, pm = self.add_address_and_card()
        self.checkout(addr, pm, steward="")
        order = Order.objects.get(user=self.buyer)
        self.assertEqual(order.steward_contribution_cents, 200)
        self.assertEqual(order.total_cents, 1500 + 105 + 299 + 200)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.steward_progress, 20)

    def test_buy_book_out_of_stock_shows_error_and_creates_no_order(self):
        scarce = make_book(self.seller, title="Rare Topology", stock=1)
        self.client.post(f"/buyer/cart/add/{scarce.id}/", {"quantity": "3"})
        addr, pm = self.add_address_and_card()
        resp = self.checkout(addr, pm)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Not enough stock", resp.context["checkout_error"])
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Inventory.objects.get(book=scarce).quantity_available, 1)

    def test_buy_book_empty_cart_rejected(self):
        addr, pm = self.add_address_and_card()
        resp = self.checkout(addr, pm)
        self.assertEqual(resp.context["checkout_error"], "Your cart is empty.")
        self.assertFalse(Order.objects.exists())

    def test_buy_book_without_address_rejected(self):
        self.client.post(f"/buyer/cart/add/{self.book.id}/")
        resp = self.client.post("/buyer/checkout/", {"payment_method_id": "1"})
        self.assertEqual(resp.context["checkout_error"], "Please select a shipping address.")

    def test_buy_book_expired_card_not_saved(self):
        resp = self.client.post(
            "/buyer/checkout/payments/add/",
            {"cardholder_name": "Nia", "card_number": "4242424242424242", "brand": "Visa",
             "cvv": "123", "exp_month": "1", "exp_year": "2001"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertMessage(resp, "expired")
        self.assertFalse(self.buyer.payment_methods.exists())

    def test_buy_book_cannot_use_another_users_address(self):
        other = make_buyer()
        foreign = other.address_set.create(line1="x", city="x", state="x", postal_code="1", country="US")
        _, pm = self.add_address_and_card()
        self.client.post(f"/buyer/cart/add/{self.book.id}/")
        resp = self.checkout(foreign, pm)
        self.assertEqual(resp.context["checkout_error"], "Invalid shipping address.")
        self.assertFalse(Order.objects.exists())


class UC05BuyerReviewsBook(PassItOnTestCase):
    """UC05: Buyer rates a book from a delivered order; the review shows on the book page."""

    def setUp(self):
        self.seller = make_seller()
        self.book = make_book(self.seller, title="Organic Chemistry")
        self.buyer = self.login(make_buyer(first_name="Rita", last_name="Stone"))
        self.order = make_order(self.buyer, [(self.book, 1)], status="delivered")
        self.item = self.order.order_items.get()

    def test_review_delivered_item_creates_review_visible_on_book_detail(self):
        detail = self.client.get(f"/buyer/orders/{self.order.id}/")
        self.assertTrue(detail.context["can_leave_review"])

        resp = self.client.post(f"/buyer/reviews/submit/{self.item.id}/", {"rating": "5"})
        self.assertRedirects(resp, f"/buyer/orders/{self.order.id}/", fetch_redirect_response=False)
        review = Review.objects.get(order_item=self.item)
        self.assertEqual((review.rating, review.user, review.book), (5, self.buyer, self.book))

        page = self.client.get(f"/catalog/{self.book.id}/")
        self.assertEqual(list(page.context["reviews"]), [review])
        self.assertContains(page, "Rita")

    def test_review_resubmission_updates_existing_review(self):
        self.client.post(f"/buyer/reviews/submit/{self.item.id}/", {"rating": "2"})
        self.client.post(f"/buyer/reviews/submit/{self.item.id}/", {"rating": "4"})
        self.assertEqual(Review.objects.filter(order_item=self.item).count(), 1)
        self.assertEqual(Review.objects.get(order_item=self.item).rating, 4)
        detail = self.client.get(f"/buyer/orders/{self.order.id}/")
        self.assertEqual(detail.context["order_items"][0]["saved_rating"], 4)

    def test_review_out_of_range_rating_ignored(self):
        for bad in ("0", "6"):
            resp = self.client.post(f"/buyer/reviews/submit/{self.item.id}/", {"rating": bad})
            self.assertEqual(resp.status_code, 302)
        self.assertFalse(Review.objects.exists())

    def test_review_other_buyers_order_item_returns_404(self):
        stranger_order = make_order(make_buyer(), [(self.book, 1)], status="delivered")
        resp = self.client.post(
            f"/buyer/reviews/submit/{stranger_order.order_items.get().id}/", {"rating": "1"}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Review.objects.exists())

    def test_review_form_hidden_until_order_delivered(self):
        paid = make_order(self.buyer, [(self.book, 1)], status="paid")
        self.assertFalse(self.client.get(f"/buyer/orders/{paid.id}/").context["can_leave_review"])

    # BUG: review_submission does int(request.POST["rating"]) unguarded; non-numeric input raises ValueError (500).
    @unittest.expectedFailure
    def test_review_non_numeric_rating_redirects_without_error(self):
        resp = self.client.post(f"/buyer/reviews/submit/{self.item.id}/", {"rating": "five"})
        self.assertEqual(resp.status_code, 302)

    # BUG: review_submission does not enforce the "delivered" rule; reviews can be posted for undelivered orders.
    @unittest.expectedFailure
    def test_review_undelivered_order_rejected_server_side(self):
        paid = make_order(self.buyer, [(self.book, 1)], status="paid")
        self.client.post(f"/buyer/reviews/submit/{paid.order_items.get().id}/", {"rating": "5"})
        self.assertFalse(Review.objects.filter(order_item__order=paid).exists())


class UC06BuyerReturnsBook(PassItOnTestCase):
    """UC06: Buyer requests a return; the seller confirms receipt; buyer is credited."""

    def setUp(self):
        self.seller = make_seller()
        self.book = make_book(self.seller, title="Microeconomics", price_cents=2000, stock=2)
        self.buyer = self.login(make_buyer())
        self.order = make_order(self.buyer, [(self.book, 1)], status="delivered")

    def request_return(self, category="damaged", details="Cover torn"):
        return self.client.post(
            f"/buyer/orders/{self.order.id}/return/",
            {"reason_category": category, "reason_details": details},
        )

    def test_return_request_full_flow_credits_buyer_and_completes(self):
        detail = self.client.get(f"/buyer/orders/{self.order.id}/")
        self.assertTrue(detail.context["can_request_return"])
        self.assertEqual(self.client.get(f"/buyer/orders/{self.order.id}/return/").status_code, 200)

        resp = self.request_return()
        self.assertRedirects(resp, f"/buyer/orders/{self.order.id}/", fetch_redirect_response=False)
        rr = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(rr.reason, "[damaged]\nCover torn")
        detail = self.client.get(f"/buyer/orders/{self.order.id}/")
        self.assertEqual(detail.context["return_status_label"], "Return requested")
        self.assertFalse(detail.context["can_request_return"])

        # Seller side: the return is pending, seller confirms the book arrived.
        self.client.force_login(self.seller)
        self.assertIn(rr, list(self.client.get("/seller/returns/").context["return_requests"]))
        self.client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})

        rr.refresh_from_db()
        self.buyer.refresh_from_db()
        self.assertEqual(rr.status, "refunded")
        self.assertEqual(self.buyer.store_credit_cents, 2000)
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 3)

        self.client.force_login(self.buyer)
        detail = self.client.get(f"/buyer/orders/{self.order.id}/")
        self.assertEqual(detail.context["return_status_label"], "Return complete")
        self.assertEqual(self.client.get("/buyer/").context["credit_balance"], "20.00")

    def test_return_request_second_submission_blocked(self):
        self.request_return()
        resp = self.request_return(details="Again")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already been submitted", resp.context["return_error"])
        self.assertEqual(ReturnRequest.objects.filter(order=self.order).count(), 1)

    def test_return_request_blank_reason_shows_error(self):
        resp = self.request_return(category="", details="   ")
        self.assertEqual(resp.context["return_error"], "Please describe your return reason.")
        self.assertFalse(ReturnRequest.objects.exists())

    def test_return_request_other_buyers_order_returns_404(self):
        foreign = make_order(make_buyer(), [(self.book, 1)], status="delivered")
        resp = self.client.post(f"/buyer/orders/{foreign.id}/return/", {"reason_details": "mine now"})
        self.assertEqual(resp.status_code, 404)

    def test_seller_receipt_is_idempotent(self):
        """Edge: confirming receipt twice must not double-credit the buyer."""
        self.request_return()
        rr = ReturnRequest.objects.get(order=self.order)
        self.client.force_login(self.seller)
        self.client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        self.client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 2000)

    # BUG: return_request_view has no order-status check; returns can be filed for cancelled orders.
    @unittest.expectedFailure
    def test_return_request_cancelled_order_rejected(self):
        cancelled = make_order(self.buyer, [(self.book, 1)], status="cancelled")
        self.client.post(f"/buyer/orders/{cancelled.id}/return/", {"reason_details": "never arrived"})
        self.assertFalse(ReturnRequest.objects.filter(order=cancelled).exists())
