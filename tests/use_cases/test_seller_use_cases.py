"""Use case tests for Seller role (Use Cases 7 through 11)."""

from django.test import TestCase
from django.urls import reverse

from General.models import Book, Inventory, User
from Seller.models import SellerProfile
from tests.helpers import (
    DEFAULT_PASSWORD,
    create_book,
    create_buyer,
    create_order_with_item,
    create_seller,
)


class SellerUseCasesTests(TestCase):
    """End-to-end use case tests for Seller flows (UC7–UC11)."""

    def setUp(self):
        self.seller = create_seller(
            email="uc_approved_seller@example.com",
            seller_approved=True,
            store_name="Campus Textbooks",
        )
        self.buyer = create_buyer(email="uc_seller_buyer@example.com", buyer_approved=True)

    # --- Use Case 7: Seller creates account ---
    def test_uc7_seller_creates_account_valid_data_creates_unapproved_seller_and_profile(self):
        """UC7: Seller registers with role='seller' and store_name, creating User and SellerProfile."""
        resp = self.client.post(
            reverse("register"),
            {
                "email": "uc7_seller@example.com",
                "first_name": "Sam",
                "last_name": "Merchant",
                "phone": "555-0202",
                "role": "seller",
                "store_name": "Sam's Bookshop",
                "password": DEFAULT_PASSWORD,
                "confirm_password": DEFAULT_PASSWORD,
                "security_question": "In what city were you born?",
                "security_answer": "Dallas",
            },
        )
        self.assertRedirects(resp, reverse("seller_home"), fetch_redirect_response=False)
        user = User.objects.get(email="uc7_seller@example.com")
        self.assertEqual(user.role, "seller")
        self.assertFalse(user.seller_approved)
        profile = SellerProfile.objects.get(user=user)
        self.assertEqual(profile.store_name, "Sam's Bookshop")

    def test_uc7_seller_creates_account_missing_fields_or_mismatched_password_rejected(self):
        """UC7 (Negative): Seller registration with missing required fields or password mismatch fails."""
        resp = self.client.post(
            reverse("register"),
            {
                "email": "",
                "first_name": "Sam",
                "last_name": "",
                "role": "seller",
                "password": DEFAULT_PASSWORD,
                "confirm_password": "MismatchPassword123!",
                "security_question": "",
                "security_answer": "",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Email is required.", resp.context["form_errors"])
        self.assertIn("Passwords do not match.", resp.context["form_errors"])

    # --- Use Case 8: Seller signs into account ---
    def test_uc8_seller_signs_into_account_approved_seller_redirects_to_seller_home(self):
        """UC8: Approved seller logs in with valid credentials and lands on seller_home dashboard."""
        resp = self.client.post(
            reverse("login"),
            {"username": "uc_approved_seller@example.com", "password": DEFAULT_PASSWORD},
        )
        self.assertRedirects(resp, reverse("seller_home"))
        self.assertIn("access_token", resp.cookies)

    def test_uc8_seller_signs_into_account_unapproved_seller_redirected_to_buyer_home(self):
        """UC8 (Negative/Edge): Unapproved seller logging in is redirected to buyer_home with pending message."""
        create_seller(email="uc8_pending@example.com", seller_approved=False)
        resp = self.client.post(
            reverse("login"),
            {"username": "uc8_pending@example.com", "password": DEFAULT_PASSWORD},
        )
        self.assertRedirects(resp, reverse("buyer_home"))

    # --- Use Case 9: Seller adds books to storefront ---
    def test_uc9_seller_adds_books_to_storefront_creates_book_and_inventory_and_lists_in_catalog(self):
        """UC9: Approved seller adds a new book with price and stock; book appears in storefront and catalog."""
        self.client.force_login(self.seller)
        resp = self.client.post(
            reverse("add_books"),
            {
                "title": "Structure and Interpretation of Computer Programs",
                "author": "Harold Abelson",
                "description": "Wizard Book",
                "isbn": "9780262510875",
                "language": "English",
                "publisher": "MIT Press",
                "publication_year": "1996",
                "condition": "Very Good",
                "price": "39.95",
                "quantity_available": "6",
                "reorder_threshold": "2",
                "is_published": "1",
            },
        )
        self.assertRedirects(resp, reverse("manage_inventory"))
        book = Book.objects.get(title="Structure and Interpretation of Computer Programs")
        self.assertEqual(book.seller_user, self.seller)
        self.assertEqual(book.base_price_cents, 3995)
        self.assertTrue(book.is_active)
        self.assertEqual(book.stock_quantity, 6)

        # Verify book is visible in buyer catalog
        self.client.force_login(self.buyer)
        cat_resp = self.client.get(reverse("catalog"), {"q": "Abelson"})
        self.assertEqual(len(cat_resp.context["page_obj"].object_list), 1)

    def test_uc9_seller_adds_books_to_storefront_invalid_price_or_missing_title_rejected(self):
        """UC9 (Negative): Adding a book with missing title/author/condition or non-positive price fails."""
        self.client.force_login(self.seller)
        resp = self.client.post(
            reverse("add_books"),
            {
                "title": "",
                "author": "",
                "condition": "",
                "price": "-10.00",
                "quantity_available": "5",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Title is required.", resp.context["form_error"])
        self.assertIn("Enter a price greater than zero.", resp.context["form_error"])

    # --- Use Case 10: Seller removes books from storefront ---
    def test_uc10_seller_removes_books_from_storefront_unpublishes_and_hides_from_catalog(self):
        """
        UC10: Seller unpublishes/removes a book from their active storefront via manage_inventory
        (omitting `is_active` sets `book.is_active = False` and `stock_quantity = 0`), hiding it from catalog.
        """
        book = create_book(
            seller=self.seller,
            title="Artificial Intelligence A Modern Approach",
            base_price_cents=5500,
            quantity_available=4,
            is_active=True,
        )
        self.client.force_login(self.seller)

        # POST to manage_inventory without 'is_active' checkbox unpublishes the listing
        resp = self.client.post(
            reverse("manage_inventory"),
            {
                "book_id": book.id,
                "price": "55.00",
                "stock_quantity": "0",
                # 'is_active' omitted -> False
            },
        )
        self.assertRedirects(resp, reverse("manage_inventory"))
        book.refresh_from_db()
        self.assertFalse(book.is_active)
        self.assertEqual(book.stock_quantity, 0)

        # Book is no longer visible in catalog or book_detail
        self.client.force_login(self.buyer)
        cat_resp = self.client.get(reverse("catalog"), {"q": "Artificial Intelligence"})
        self.assertEqual(len(cat_resp.context["page_obj"].object_list), 0)
        self.assertEqual(self.client.get(reverse("book_detail", kwargs={"pk": book.id})).status_code, 404)

    def test_uc10_seller_removes_books_from_storefront_cannot_modify_another_sellers_book(self):
        """UC10 (Negative): Seller attempting to unpublish/edit another seller's book receives 404."""
        other_seller = create_seller(email="other_s@example.com", seller_approved=True)
        other_book = create_book(seller=other_seller, title="Other Seller Book")

        self.client.force_login(self.seller)
        resp = self.client.post(
            reverse("manage_inventory"),
            {"book_id": other_book.id, "price": "10.00", "stock_quantity": "0"},
        )
        self.assertEqual(resp.status_code, 404)
        other_book.refresh_from_db()
        self.assertTrue(other_book.is_active)

    # --- Use Case 11: Seller views orders ---
    def test_uc11_seller_views_orders_lists_orders_and_views_order_detail_and_updates_status(self):
        """UC11: Seller views orders containing their books, inspects order details, and updates status."""
        book = create_book(seller=self.seller, title="The Pragmatic Programmer", base_price_cents=3200)
        order, _ = create_order_with_item(self.buyer, book, quantity=2, status="paid")

        self.client.force_login(self.seller)
        orders_resp = self.client.get(reverse("seller_orders"))
        self.assertEqual(orders_resp.status_code, 200)
        self.assertEqual(len(orders_resp.context["orders"]), 1)
        self.assertEqual(orders_resp.context["orders"][0].seller_item_count, 2)
        self.assertEqual(orders_resp.context["orders"][0].seller_line_total_cents, 6400)

        detail_resp = self.client.get(reverse("seller_order_details", kwargs={"order_id": order.id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.context["seller_line_total_display"], "64.00")

        # Update status to shipped
        status_resp = self.client.post(
            reverse("update_order_status", kwargs={"order_id": order.id}),
            {"status": "shipped"},
        )
        self.assertRedirects(status_resp, reverse("seller_order_details", kwargs={"order_id": order.id}))
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")

    def test_uc11_seller_views_orders_cannot_view_or_update_order_without_their_books(self):
        """UC11 (Negative): Seller cannot view details or update status of an order with only another seller's books."""
        other_seller = create_seller(email="other_s_ord@example.com", seller_approved=True)
        other_book = create_book(seller=other_seller, title="Other Order Book")
        other_order, _ = create_order_with_item(self.buyer, other_book, status="paid")

        self.client.force_login(self.seller)
        self.assertEqual(
            self.client.get(reverse("seller_order_details", kwargs={"order_id": other_order.id})).status_code,
            404,
        )
        resp_upd = self.client.post(
            reverse("update_order_status", kwargs={"order_id": other_order.id}),
            {"status": "cancelled"},
        )
        self.assertRedirects(resp_upd, reverse("seller_orders"))
        other_order.refresh_from_db()
        self.assertEqual(other_order.status, "paid")
