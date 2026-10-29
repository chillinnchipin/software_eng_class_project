"""Use-case tests for the Seller role (UC07-UC11)."""

import unittest

from app.general.models import Book, Inventory, User
from app.seller.models import SellerProfile
from tests.helpers import (
    PassItOnTestCase,
    make_book,
    make_buyer,
    make_order,
    make_seller,
)


def seller_register_payload(**overrides):
    data = {
        "email": "shop.owner@example.com",
        "first_name": "Sam",
        "last_name": "Shelf",
        "password": "LongEnough1",
        "confirm_password": "LongEnough1",
        "role": "seller",
        "store_name": "Sam's Used Texts",
        "security_question": "What was the name of your first pet?",
        "security_answer": "Rex",
    }
    data.update(overrides)
    return data


def book_payload(**overrides):
    data = {
        "title": "Intro to Statistics",
        "author": "Pat Mean",
        "condition": "Like New",
        "price": "12.50",
        "quantity_available": "4",
        "reorder_threshold": "1",
        "isbn": "9780000000001",
        "is_published": "1",
    }
    data.update(overrides)
    return data


class UC07SellerCreatesAccount(PassItOnTestCase):
    """UC07: A visitor registers a seller account; it stays pending until an admin approves it."""

    def test_register_seller_creates_profile_pending_approval(self):
        resp = self.client.post("/register/", seller_register_payload())
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)
        user = User.objects.get(email="shop.owner@example.com")
        self.assertEqual(user.role, "seller")
        self.assertFalse(user.seller_approved)
        self.assertEqual(SellerProfile.objects.get(user=user).store_name, "Sam's Used Texts")

    def test_register_seller_pending_seller_pages_redirect_to_buyer_home(self):
        resp = self.client.post("/register/", seller_register_payload(), follow=True)
        self.assertEqual(resp.redirect_chain[-1][0], "/buyer/")
        self.assertMessage(resp, "pending approval")
        add = self.client.get("/seller/inventory/add/")
        self.assertRedirects(add, "/buyer/", fetch_redirect_response=False)

    def test_register_seller_duplicate_email_rejected(self):
        make_seller(email="shop.owner@example.com")
        resp = self.client.post("/register/", seller_register_payload())
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already exists", resp.context["form_errors"])
        self.assertEqual(SellerProfile.objects.count(), 1)

    def test_register_seller_missing_names_and_question_rejected(self):
        resp = self.client.post(
            "/register/", seller_register_payload(first_name="", last_name="", security_question="")
        )
        errors = resp.context["form_errors"]
        self.assertIn("First name is required.", errors)
        self.assertIn("Last name is required.", errors)
        self.assertIn("Security question is required.", errors)
        self.assertFalse(SellerProfile.objects.exists())

    def test_register_seller_blank_store_name_stored_as_null(self):
        """Edge: store name is optional at sign-up."""
        self.client.post("/register/", seller_register_payload(store_name="  "))
        self.assertIsNone(SellerProfile.objects.get().store_name)


class UC08SellerSignsIn(PassItOnTestCase):
    """UC08: A seller signs in; approved sellers reach the storefront dashboard."""

    def test_login_approved_seller_reaches_seller_dashboard(self):
        seller = make_seller()
        resp = self.login_via_form(seller)
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)
        dash = self.client.get("/seller/")
        self.assertEqual(dash.status_code, 200)
        self.assertTemplateUsed(dash, "dashboard/dashboard.html")
        self.assertIn("stats", dash.context)

    def test_login_unapproved_seller_sent_to_buyer_home_with_pending_message(self):
        seller = make_seller(approved=False)
        resp = self.login_via_form(seller)
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        self.assertMessage(resp, "pending approval")
        self.assertRedirects(self.client.get("/seller/"), "/buyer/", fetch_redirect_response=False)

    def test_login_seller_wrong_password_rejected(self):
        seller = make_seller()
        resp = self.login_via_form(seller, password="nope-nope")
        self.assertEqual(resp.context["error"], "Invalid email or password.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_seller_email_is_case_insensitive_for_domain_only(self):
        """Edge: normalize_email lower-cases the domain, so a mixed-case domain still logs in."""
        seller = make_seller(email="mixed@Example.COM")
        resp = self.client.post("/login/", {"username": "mixed@example.com", "password": "Sup3r-secret-pw"})
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)
        self.assertEqual(seller.email, "mixed@example.com")

    def test_buyer_cannot_open_seller_dashboard(self):
        self.login(make_buyer())
        resp = self.client.get("/seller/dashboard/")
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)


class UC09SellerAddsBooks(PassItOnTestCase):
    """UC09: An approved seller lists a new book; it appears in inventory and the buyer catalog."""

    def setUp(self):
        self.seller = self.login(make_seller())

    def test_add_book_creates_listing_and_inventory_visible_to_buyers(self):
        self.assertEqual(self.client.get("/seller/inventory/add/").status_code, 200)
        resp = self.client.post("/seller/inventory/add/", book_payload())
        self.assertRedirects(resp, "/seller/inventory/", fetch_redirect_response=False)

        book = Book.objects.get(title="Intro to Statistics")
        self.assertEqual(book.seller_user, self.seller)
        self.assertEqual(book.base_price_cents, 1250)
        self.assertTrue(book.is_active)
        self.assertEqual(book.inventory.quantity_available, 4)
        self.assertEqual(book.inventory.reorder_threshold, 1)

        inv_page = self.client.get("/seller/inventory/")
        self.assertIn(book, list(inv_page.context["page_obj"].object_list))

        self.client.force_login(make_buyer())
        catalog = self.client.get("/catalog/?q=Statistics")
        self.assertEqual([b.id for b in catalog.context["page_obj"].object_list], [book.id])

    def test_add_book_as_draft_hidden_from_catalog(self):
        self.client.post("/seller/inventory/add/", book_payload(is_published="0"))
        book = Book.objects.get(title="Intro to Statistics")
        self.assertFalse(book.is_active)
        self.client.force_login(make_buyer())
        self.assertEqual(self.client.get(f"/catalog/{book.id}/").status_code, 404)

    def test_add_book_missing_title_and_price_shows_errors(self):
        resp = self.client.post("/seller/inventory/add/", book_payload(title="", price=""))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Title is required.", resp.context["form_error"])
        self.assertIn("price greater than zero", resp.context["form_error"])
        self.assertEqual(resp.context["form_values"]["author"], "Pat Mean")
        self.assertFalse(Book.objects.exists())

    def test_add_book_invalid_price_and_negative_stock_rejected(self):
        resp = self.client.post("/seller/inventory/add/", book_payload(price="abc", quantity_available="-2"))
        self.assertIn("Enter a valid price.", resp.context["form_error"])
        self.assertIn("Stock cannot be negative.", resp.context["form_error"])
        self.assertFalse(Book.objects.exists())

    def test_add_book_unapproved_seller_blocked(self):
        self.login(make_seller(approved=False))
        resp = self.client.post("/seller/inventory/add/", book_payload())
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        self.assertFalse(Book.objects.exists())

    def test_add_book_buyer_blocked(self):
        self.login(make_buyer())
        self.client.post("/seller/inventory/add/", book_payload())
        self.assertFalse(Book.objects.exists())


class UC10SellerRemovesBooks(PassItOnTestCase):
    """UC10: Seller removes a book from the storefront.

    There is no hard-delete in the app: "removing" a listing means unpublishing it
    (unchecking is_active on Manage Inventory), which hides it from buyers.
    """

    def setUp(self):
        self.seller = self.login(make_seller())
        self.book = make_book(self.seller, title="Old Physics", price_cents=1000, stock=3)

    def update_listing(self, book, **fields):
        data = {"book_id": book.id, "price": "10.00", "stock_quantity": "3"}
        data.update(fields)
        return self.client.post("/seller/inventory/", data)

    def test_remove_book_unpublish_hides_from_catalog_and_detail(self):
        resp = self.update_listing(self.book)  # no is_active -> unpublished
        self.assertRedirects(resp, "/seller/inventory/", fetch_redirect_response=False)
        self.book.refresh_from_db()
        self.assertFalse(self.book.is_active)

        unpublished = self.client.get("/seller/inventory/?status=UNPUBLISHED")
        self.assertIn(self.book, list(unpublished.context["page_obj"].object_list))

        self.client.force_login(make_buyer())
        self.assertEqual(list(self.client.get("/catalog/?q=Old Physics").context["page_obj"].object_list), [])
        self.assertEqual(self.client.get(f"/catalog/{self.book.id}/").status_code, 404)

    def test_remove_book_drops_it_from_existing_buyer_carts(self):
        """Edge: a buyer who had the book in their cart no longer sees it after unpublish."""
        buyer = make_buyer()
        self.client.force_login(buyer)
        self.client.post(f"/buyer/cart/add/{self.book.id}/")
        self.client.force_login(self.seller)
        self.update_listing(self.book)
        self.client.force_login(buyer)
        self.assertEqual(self.client.get("/buyer/cart/").context["cart_items"], [])

    def test_remove_book_republish_restores_listing(self):
        self.update_listing(self.book)
        self.update_listing(self.book, is_active="on")
        self.book.refresh_from_db()
        self.assertTrue(self.book.is_active)

    def test_remove_book_owned_by_another_seller_returns_404(self):
        foreign = make_book(make_seller(), title="Not Mine")
        resp = self.update_listing(foreign)
        self.assertEqual(resp.status_code, 404)
        foreign.refresh_from_db()
        self.assertTrue(foreign.is_active)

    def test_set_stock_zero_listed_under_out_of_stock_filter(self):
        self.update_listing(self.book, stock_quantity="0", is_active="on")
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 0)
        oos = self.client.get("/seller/inventory/?status=OUT_OF_STOCK")
        self.assertEqual(list(oos.context["page_obj"].object_list), [self.book])
        self.client.force_login(make_buyer())
        self.assertEqual(list(self.client.get("/catalog/?in_stock=1").context["page_obj"].object_list), [])

    def test_update_listing_invalid_price_leaves_book_unchanged(self):
        resp = self.update_listing(self.book, price="0", is_active="on")
        self.assertMessage(resp, "Invalid price.")
        self.book.refresh_from_db()
        self.assertEqual(self.book.base_price_cents, 1000)
        self.assertTrue(self.book.is_active)


class UC11SellerViewsOrders(PassItOnTestCase):
    """UC11: Seller views orders containing their books and advances the order status."""

    def setUp(self):
        self.seller = make_seller()
        self.other_seller = make_seller()
        self.mine = make_book(self.seller, title="My Calculus", price_cents=1000)
        self.theirs = make_book(self.other_seller, title="Their Biology", price_cents=4000)
        self.buyer = make_buyer()
        self.shared_order = make_order(self.buyer, [(self.mine, 2), (self.theirs, 1)])
        self.foreign_order = make_order(self.buyer, [(self.theirs, 1)])
        self.login(self.seller)

    def test_seller_orders_list_shows_only_orders_with_own_books_and_own_totals(self):
        resp = self.client.get("/seller/orders/")
        self.assertEqual(resp.status_code, 200)
        orders = resp.context["orders"]
        self.assertEqual([o.id for o in orders], [self.shared_order.id])
        self.assertEqual(orders[0].seller_item_count, 2)
        self.assertEqual(orders[0].seller_line_total_display, "20.00")

    def test_seller_order_details_lists_only_own_lines(self):
        resp = self.client.get(f"/seller/orders/{self.shared_order.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([i.book_id for i in resp.context["items"]], [self.mine.id])
        self.assertEqual(resp.context["seller_line_total_display"], "20.00")
        self.assertContains(resp, "My Calculus")
        self.assertNotContains(resp, "Their Biology")

    def test_seller_order_details_other_sellers_order_returns_404(self):
        self.assertEqual(self.client.get(f"/seller/orders/{self.foreign_order.id}/").status_code, 404)

    def test_seller_updates_order_status_to_shipped_visible_to_buyer(self):
        resp = self.client.post(f"/seller/orders/{self.shared_order.id}/status/update/", {"status": "Shipped"})
        self.assertRedirects(
            resp, f"/seller/orders/{self.shared_order.id}/", fetch_redirect_response=False
        )
        self.shared_order.refresh_from_db()
        self.assertEqual(self.shared_order.status, "shipped")
        self.client.force_login(self.buyer)
        detail = self.client.get(f"/buyer/orders/{self.shared_order.id}/")
        self.assertEqual(detail.context["order"].status, "shipped")

    def test_seller_cannot_update_status_of_foreign_order(self):
        resp = self.client.post(f"/seller/orders/{self.foreign_order.id}/status/update/", {"status": "shipped"})
        self.assertRedirects(resp, "/seller/orders/", fetch_redirect_response=False)
        self.foreign_order.refresh_from_db()
        self.assertEqual(self.foreign_order.status, "paid")

    def test_seller_status_update_requires_post(self):
        resp = self.client.get(f"/seller/orders/{self.shared_order.id}/status/update/")
        self.assertEqual(resp.status_code, 405)

    def test_seller_orders_unapproved_seller_blocked(self):
        self.login(make_seller(approved=False))
        self.assertRedirects(self.client.get("/seller/orders/"), "/buyer/", fetch_redirect_response=False)

    # BUG: orders() rebuilds the queryset after filtering, so ?status=OPEN is silently ignored.
    @unittest.expectedFailure
    def test_seller_orders_open_filter_excludes_delivered(self):
        self.shared_order.status = "delivered"
        self.shared_order.save()
        resp = self.client.get("/seller/orders/?status=OPEN")
        self.assertEqual(resp.context["orders"], [])

    # BUG: update_order_status redirects to " seller_order_details" (leading space) -> NoReverseMatch on invalid status.
    @unittest.expectedFailure
    def test_seller_invalid_status_redirects_back_with_error(self):
        resp = self.client.post(f"/seller/orders/{self.shared_order.id}/status/update/", {"status": "teleported"})
        self.assertRedirects(
            resp, f"/seller/orders/{self.shared_order.id}/", fetch_redirect_response=False
        )
