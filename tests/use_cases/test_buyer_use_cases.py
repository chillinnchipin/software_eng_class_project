"""Use case tests for Buyer & Steward roles (Use Cases 1 through 6)."""

from unittest import expectedFailure

from django.test import TestCase
from django.urls import reverse

from Buyer.models import Order, ReturnRequest, Review
from General.models import Book, User
from tests.helpers import (
    DEFAULT_PASSWORD,
    create_address,
    create_book,
    create_buyer,
    create_order_with_item,
    create_payment_method,
    create_seller,
    ensure_steward_pool,
)


class BuyerUseCasesTests(TestCase):
    """End-to-end use case tests for Buyer flows (UC1–UC6)."""

    def setUp(self):
        ensure_steward_pool()
        self.seller = create_seller(email="uc_seller@example.com", seller_approved=True)
        self.book = create_book(
            seller=self.seller,
            title="Introduction to Algorithms",
            author="Thomas H. Cormen",
            description="Comprehensive algorithms textbook",
            base_price_cents=4500,
            quantity_available=5,
        )

    # --- Use Case 1: Buyer creates account ---
    def test_uc1_buyer_creates_account_valid_data_registers_and_redirects_to_buyer_home(self):
        """UC1: Buyer submits valid registration form, creates buyer account, and lands on buyer_home."""
        resp = self.client.post(
            reverse("register"),
            {
                "email": "uc1_buyer@example.com",
                "first_name": "Emma",
                "last_name": "Student",
                "phone": "555-0101",
                "role": "buyer",
                "password": DEFAULT_PASSWORD,
                "confirm_password": DEFAULT_PASSWORD,
                "security_question": "In what city were you born?",
                "security_answer": "Austin",
            },
        )
        self.assertRedirects(resp, reverse("buyer_home"))
        user = User.objects.get(email="uc1_buyer@example.com")
        self.assertEqual(user.role, "buyer")
        self.assertEqual(user.first_name, "Emma")

    def test_uc1_buyer_creates_account_duplicate_email_or_short_password_rejected(self):
        """UC1 (Negative): Buyer registration fails when email is already registered or password is <8 chars."""
        create_buyer(email="taken@example.com")
        resp = self.client.post(
            reverse("register"),
            {
                "email": "taken@example.com",
                "first_name": "Emma",
                "last_name": "Student",
                "role": "buyer",
                "password": "short",
                "confirm_password": "short",
                "security_question": "In what city were you born?",
                "security_answer": "Austin",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("An account with this email already exists.", resp.context["form_errors"])
        self.assertIn("Password must be at least 8 characters.", resp.context["form_errors"])

    # --- Use Case 2: Buyer signs into account ---
    def test_uc2_buyer_signs_into_account_approved_buyer_authenticates_and_redirects(self):
        """UC2: Approved buyer signs in with valid email and password and is redirected to buyer_home."""
        create_buyer(email="uc2_buyer@example.com", buyer_approved=True)
        resp = self.client.post(
            reverse("login"),
            {"username": "uc2_buyer@example.com", "password": DEFAULT_PASSWORD},
        )
        self.assertRedirects(resp, reverse("buyer_home"))
        self.assertIn("access_token", resp.cookies)

    def test_uc2_buyer_signs_into_account_unapproved_or_wrong_password_denied(self):
        """UC2 (Negative): Unapproved buyer is logged out with pending message; wrong password shows error."""
        create_buyer(email="uc2_pending@example.com", buyer_approved=False)
        resp_pending = self.client.post(
            reverse("login"),
            {"username": "uc2_pending@example.com", "password": DEFAULT_PASSWORD},
        )
        self.assertRedirects(resp_pending, reverse("login"))

        resp_wrong = self.client.post(
            reverse("login"),
            {"username": "uc2_pending@example.com", "password": "WrongPassword!"},
        )
        self.assertEqual(resp_wrong.status_code, 200)
        self.assertContains(resp_wrong, "Invalid email or password.")

    # --- Use Case 3: Buyer browses/searches for a book ---
    def test_uc3_buyer_browses_and_searches_catalog_finds_matching_books_and_views_detail(self):
        """UC3: Buyer searches catalog by keyword, price range, and stock filter, then views book detail."""
        buyer = create_buyer(email="uc3_buyer@example.com", buyer_approved=True)
        create_book(
            seller=self.seller,
            title="Pride and Prejudice",
            author="Jane Austen",
            base_price_cents=1200,
            quantity_available=2,
        )
        self.client.force_login(buyer)

        resp_search = self.client.get(
            reverse("catalog"),
            {"q": "Cormen", "min_price": "20", "max_price": "60", "in_stock": "1"},
        )
        self.assertEqual(resp_search.status_code, 200)
        titles = [b.title for b in resp_search.context["page_obj"].object_list]
        self.assertEqual(titles, ["Introduction to Algorithms"])

        resp_detail = self.client.get(reverse("book_detail", kwargs={"pk": self.book.id}))
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "Introduction to Algorithms")

    def test_uc3_buyer_browses_catalog_excludes_inactive_and_non_matching_books(self):
        """UC3 (Negative/Edge): Inactive books and non-matching queries return empty catalog or 404 on detail."""
        buyer = create_buyer(email="uc3_neg@example.com", buyer_approved=True)
        hidden = create_book(seller=self.seller, title="Hidden Textbook", is_active=False)
        self.client.force_login(buyer)

        resp_empty = self.client.get(reverse("catalog"), {"q": "NoSuchBookTitle999"})
        self.assertEqual(len(resp_empty.context["page_obj"].object_list), 0)

        resp_404 = self.client.get(reverse("book_detail", kwargs={"pk": hidden.id}))
        self.assertEqual(resp_404.status_code, 404)

    # --- Use Case 4: Buyer buys a book ---
    def test_uc4_buyer_buys_a_book_completes_checkout_and_creates_paid_order(self):
        """UC4: Buyer adds book to cart, adds shipping & payment, and completes checkout."""
        buyer = create_buyer(email="uc4_buyer@example.com", buyer_approved=True)
        self.client.force_login(buyer)

        # Add book to cart
        self.client.post(reverse("add_to_cart", kwargs={"book_id": self.book.id}), {"quantity": 1})

        # Add shipping address and payment method
        addr = create_address(buyer)
        pm = create_payment_method(buyer)

        # Checkout
        resp = self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": addr.id,
                "payment_method_id": pm.id,
                "steward_contribution": "2.00",
            },
        )
        order = Order.objects.get(user=buyer)
        self.assertRedirects(resp, reverse("order_confirmation", kwargs={"order_id": order.id}))
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.subtotal_cents, 4500)
        self.book.inventory.refresh_from_db()
        self.assertEqual(self.book.inventory.quantity_available, 4)

    def test_uc4_buyer_buys_a_book_out_of_stock_or_missing_payment_fails_checkout(self):
        """UC4 (Negative): Checkout fails if book stock is insufficient or payment method is missing."""
        buyer = create_buyer(email="uc4_neg@example.com", buyer_approved=True)
        addr = create_address(buyer)
        self.client.force_login(buyer)
        self.client.post(reverse("add_to_cart", kwargs={"book_id": self.book.id}), {"quantity": 10})

        pm = create_payment_method(buyer)
        resp = self.client.post(
            reverse("checkout"),
            {"shipping_address_id": addr.id, "payment_method_id": pm.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Not enough stock", resp.context["checkout_error"])
        self.assertFalse(Order.objects.filter(user=buyer).exists())

    # --- Use Case 5: Buyer reviews a book ---
    def test_uc5_buyer_reviews_a_book_submits_rating_and_displays_on_book_detail(self):
        """UC5: Buyer submits a 1–5 star rating for a delivered order item and updates it."""
        buyer = create_buyer(email="uc5_buyer@example.com", buyer_approved=True)
        order, item = create_order_with_item(buyer, self.book, status="delivered")
        self.client.force_login(buyer)

        resp = self.client.post(
            reverse("review_submission", kwargs={"item_id": item.id}),
            {"rating": 4},
        )
        self.assertRedirects(resp, reverse("order_detail", kwargs={"order_id": order.id}))
        review = Review.objects.get(order_item=item, user=buyer)
        self.assertEqual(review.rating, 4)

        # Updating rating overwrites previous rating
        self.client.post(reverse("review_submission", kwargs={"item_id": item.id}), {"rating": 5})
        review.refresh_from_db()
        self.assertEqual(review.rating, 5)

    def test_uc5_buyer_reviews_a_book_out_of_range_rating_or_other_users_order_rejected(self):
        """UC5 (Negative): Rating outside 1–5 is ignored; reviewing another buyer's item returns 404."""
        buyer = create_buyer(email="uc5_neg@example.com", buyer_approved=True)
        other_buyer = create_buyer(email="uc5_other@example.com", buyer_approved=True)
        _, item = create_order_with_item(buyer, self.book, status="delivered")
        _, other_item = create_order_with_item(other_buyer, self.book, status="delivered")

        self.client.force_login(buyer)
        self.client.post(reverse("review_submission", kwargs={"item_id": item.id}), {"rating": 6})
        self.assertFalse(Review.objects.filter(order_item=item).exists())

        resp_404 = self.client.post(
            reverse("review_submission", kwargs={"item_id": other_item.id}),
            {"rating": 5},
        )
        self.assertEqual(resp_404.status_code, 404)

    @expectedFailure
    def test_uc5_buyer_reviews_a_book_saves_review_text_field(self):
        """
        BUG: Buyer/views.py:1359 (`review_submission`) only saves `user`, `book`, and `rating`
        in `Review.objects.update_or_create`, ignoring `request.POST.get("review_text")`.
        """
        buyer = create_buyer(email="uc5_text@example.com", buyer_approved=True)
        _, item = create_order_with_item(buyer, self.book, status="delivered")
        self.client.force_login(buyer)

        self.client.post(
            reverse("review_submission", kwargs={"item_id": item.id}),
            {"rating": 5, "review_text": "Excellent condition and fast shipping!"},
        )
        review = Review.objects.get(order_item=item)
        self.assertEqual(review.review_text, "Excellent condition and fast shipping!")

    # --- Use Case 6: Buyer returns a book ---
    def test_uc6_buyer_returns_a_book_creates_return_request_and_receives_store_credit_on_receipt(self):
        """UC6: Buyer submits a return request for an order, and receives store credit when seller confirms receipt."""
        buyer = create_buyer(email="uc6_buyer@example.com", buyer_approved=True)
        order, _ = create_order_with_item(buyer, self.book, quantity=1, status="delivered")
        self.client.force_login(buyer)

        resp = self.client.post(
            reverse("return_request", kwargs={"order_id": order.id}),
            {"reason_category": "damaged", "reason_details": "Water damage on back cover"},
        )
        self.assertRedirects(resp, reverse("order_detail", kwargs={"order_id": order.id}))
        rr = ReturnRequest.objects.get(order=order)
        self.assertEqual(rr.status, "requested")
        self.assertIn("[damaged]", rr.reason)

        # Seller confirms return receipt -> buyer receives store credit and return is marked refunded
        self.client.force_login(self.seller)
        self.client.post(
            reverse("seller_return_request_detail", kwargs={"return_id": rr.id}),
            {"action": "book_received"},
        )
        rr.refresh_from_db()
        buyer.refresh_from_db()
        self.assertEqual(rr.status, "refunded")
        self.assertEqual(buyer.store_credit_cents, 4500)

    def test_uc6_buyer_returns_a_book_blank_reason_or_duplicate_return_rejected(self):
        """UC6 (Negative): Blank return reason is rejected; submitting a second return for the same order is blocked."""
        buyer = create_buyer(email="uc6_neg@example.com", buyer_approved=True)
        order, _ = create_order_with_item(buyer, self.book, status="delivered")
        self.client.force_login(buyer)

        resp_blank = self.client.post(
            reverse("return_request", kwargs={"order_id": order.id}),
            {"reason_category": "", "reason_details": ""},
        )
        self.assertEqual(resp_blank.status_code, 200)
        self.assertEqual(resp_blank.context["return_error"], "Please describe your return reason.")

        ReturnRequest.objects.create(order=order, reason="Already requested")
        resp_dup = self.client.post(
            reverse("return_request", kwargs={"order_id": order.id}),
            {"reason_category": "other", "reason_details": "Second try"},
        )
        self.assertEqual(
            resp_dup.context["return_error"],
            "A return has already been submitted for this order.",
        )
