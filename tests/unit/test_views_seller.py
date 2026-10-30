"""Unit tests for every view in Seller/views.py (one test class per view)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from app.buyer.models import SellerReturnReceipt
from app.general.models import Book, Inventory
from app.seller.models import SellerProfile
from tests.helpers import (
    PASSWORD,
    PassItOnTestCase,
    make_book,
    make_buyer,
    make_order,
    make_return,
    make_review,
    make_seller,
    make_user,
)


class SellerViewTestCase(PassItOnTestCase):
    def setUp(self):
        self.seller = self.login(make_seller())
        self.buyer = make_buyer()

    def assertRedirectsToBuyerHome(self, resp):
        self.assertRedirects(resp, reverse("buyer_home"), fetch_redirect_response=False)


class SellerHomeViewTests(SellerViewTestCase):
    def test_home_unapproved_seller_redirected_with_pending_message(self):
        self.login(make_seller(approved=False))
        resp = self.client.get(reverse("seller_home"))
        self.assertRedirectsToBuyerHome(resp)
        self.assertMessage(resp, "pending approval")

    def test_home_buyer_redirected_with_seller_required_message(self):
        self.login(self.buyer)
        resp = self.client.get(reverse("seller_home"))
        self.assertRedirectsToBuyerHome(resp)
        self.assertMessage(resp, "A seller account is required.")

    def test_home_approved_seller_sees_stats_sales_and_reviews(self):
        book = make_book(self.seller, price_cents=2000, stock=2)
        make_book(self.seller, stock=0)
        make_book(self.seller, is_active=False)
        order = make_order(self.buyer, [(book, 1)], status="paid")
        make_review(self.buyer, order.order_items.get(), rating=4)
        resp = self.client.get(reverse("seller_home"))
        self.assertEqual(resp.status_code, 200)
        stats = resp.context["stats"]
        self.assertEqual((stats["published_books"], stats["unpublished_books"]), (2, 1))
        self.assertEqual((stats["low_stock_count"], stats["out_of_stock_count"]), (1, 1))
        self.assertEqual(resp.context["total_sales"], "20.00")
        self.assertEqual(resp.context["seller_avg_rating"], 4.0)
        self.assertEqual(resp.context["seller_review_count"], 1)
        self.assertEqual(list(resp.context["recent_orders"]), [order])

    def test_home_anonymous_redirects_to_login(self):
        self.client.logout()
        self.assertIn("/login/", self.client.get(reverse("seller_home"))["Location"])


class SellerDashboardViewTests(SellerViewTestCase):
    def test_dashboard_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(reverse("seller_dashboard")))

    def test_dashboard_orders_numbered_and_pending_returns_counted(self):
        book = make_book(self.seller)
        first = make_order(self.buyer, [(book, 1)])
        second = make_order(self.buyer, [(book, 1)])
        make_return(first)
        resp = self.client.get(reverse("seller_dashboard"))
        self.assertEqual(resp.status_code, 200)
        numbers = {o.id: o.display_order_number for o in resp.context["recent_orders"]}
        self.assertEqual(numbers, {second.id: 2, first.id: 1})
        self.assertEqual(resp.context["stats"]["pending_returns_count"], 1)
        self.assertEqual(resp.context["seller_avg_rating"], 0)


class SellerProfileViewTests(SellerViewTestCase):
    def _post(self, **data):
        return self.client.post(reverse("seller_profile"), data)

    def test_profile_non_seller_redirected_to_general_home(self):
        self.login(self.buyer)
        resp = self.client.get(reverse("seller_profile"))
        self.assertRedirects(resp, reverse("general_home"), fetch_redirect_response=False)

    def test_profile_get_creates_missing_seller_profile(self):
        seller = self.login(make_user("seller"))
        self.assertFalse(SellerProfile.objects.filter(user=seller).exists())
        resp = self.client.get(reverse("seller_profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(SellerProfile.objects.filter(user=seller).exists())

    def test_profile_update_missing_names_shows_error(self):
        resp = self._post(action="profile", first_name="", last_name="")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("First name is required.", resp.context["profile_error"])

    # BUG: success path calls messages.sucess (typo) -> AttributeError / HTTP 500.
    @unittest.expectedFailure
    def test_profile_update_success_saves_and_redirects(self):
        resp = self._post(action="profile", first_name="Sam", last_name="Seller",
                          phone="555-0101", store_name="Sam's Books")
        self.assertRedirects(resp, reverse("seller_profile"), fetch_redirect_response=False)
        self.assertEqual(SellerProfile.objects.get(user=self.seller).store_name, "Sam's Books")

    # BUG: blank phone becomes None and `phone[:50]` raises TypeError / HTTP 500.
    @unittest.expectedFailure
    def test_profile_update_blank_phone_saves_none(self):
        self._post(action="profile", first_name="Sam", last_name="Seller", phone="", store_name="S")
        self.seller.refresh_from_db()
        self.assertIsNone(self.seller.phone)

    def test_profile_password_errors_reported(self):
        resp = self._post(action="password", current_password="bad", new_password="short", confirm_password="other")
        err = resp.context["password_error"]
        self.assertIn("Current password is incorrect.", err)
        self.assertIn("at least 8 characters", err)
        self.assertIn("do not match", err)

    def test_profile_password_success_updates_hash_and_keeps_session(self):
        resp = self._post(action="password", current_password=PASSWORD,
                          new_password="Another-pass9", confirm_password="Another-pass9")
        self.assertRedirects(resp, reverse("seller_profile"), fetch_redirect_response=False)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.check_password("Another-pass9"))
        self.assertEqual(self.client.get(reverse("seller_profile")).status_code, 200)

    def test_profile_security_valid_saves_lowercased_answer(self):
        resp = self._post(action="security", security_question="What was the name of your first pet?",
                          security_answer="Rex")
        self.assertRedirects(resp, reverse("seller_profile"), fetch_redirect_response=False)
        self.seller.refresh_from_db()
        self.assertTrue(check_password("rex", self.seller.security_answer_hash))

    def test_profile_security_invalid_shows_error(self):
        resp = self._post(action="security", security_question="", security_answer="")
        self.assertEqual(resp.context["security_error"], "Please correct the security question form.")


class _ImagesDirMixin:
    """Redirect cover uploads to a temp dir so tests never write into the repo's media/book_images/."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "media" / "book_images").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        override = override_settings(BASE_DIR=self.tmp)
        override.enable()
        self.addCleanup(override.disable)


class AddBooksViewTests(_ImagesDirMixin, SellerViewTestCase):
    def _data(self, **overrides):
        data = {
            "title": "Calculus", "author": "Stewart", "condition": "Like New",
            "price": "42.50", "quantity_available": "4", "isbn": "123",
            "publication_year": "2015", "reorder_threshold": "2",
            "cover_image_url": "https://img.example/c.jpg",
        }
        data.update(overrides)
        return data

    def _post(self, **overrides):
        return self.client.post(reverse("add_books"), self._data(**overrides))

    def assertFormError(self, resp, fragment):
        self.assertEqual(resp.status_code, 200)
        self.assertIn(fragment, resp.context["form_error"])
        self.assertFalse(Book.objects.exists())

    def test_add_books_non_seller_redirected(self):
        self.login(make_seller(approved=False))
        self.assertRedirectsToBuyerHome(self.client.get(reverse("add_books")))

    def test_add_books_get_renders_empty_form(self):
        resp = self.client.get(reverse("add_books"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["form_values"], {})

    def test_add_books_missing_required_fields_rejected(self):
        resp = self._post(title="", author="", condition="")
        for msg in ("Title is required.", "Author is required.", "Condition is required."):
            self.assertIn(msg, resp.context["form_error"])
        self.assertFalse(Book.objects.exists())

    def test_add_books_zero_price_rejected(self):
        self.assertFormError(self._post(price="0"), "price greater than zero")

    def test_add_books_invalid_price_rejected(self):
        self.assertFormError(self._post(price="cheap"), "Enter a valid price.")

    def test_add_books_negative_stock_rejected(self):
        self.assertFormError(self._post(quantity_available="-1"), "Stock cannot be negative.")

    def test_add_books_invalid_stock_rejected(self):
        self.assertFormError(self._post(quantity_available="many"), "valid stock quantity")

    def test_add_books_error_keeps_form_values(self):
        resp = self._post(price="0")
        self.assertEqual(resp.context["form_values"]["title"], "Calculus")

    def test_add_books_oversized_cover_rejected(self):
        big = SimpleUploadedFile("big.jpg", b"x" * (5 * 1024 * 1024 + 1), content_type="image/jpeg")
        self.assertFormError(self._post(cover_image=big), "too large")

    def test_add_books_unknown_cover_type_rejected(self):
        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        self.assertFormError(self._post(cover_image=bad), "valid cover image file type")

    def test_add_books_success_creates_book_and_inventory(self):
        resp = self._post()
        self.assertRedirects(resp, reverse("manage_inventory"), fetch_redirect_response=False)
        book = Book.objects.get()
        self.assertEqual(book.seller_user, self.seller)
        self.assertEqual(book.base_price_cents, 4250)
        self.assertEqual(book.publication_year, 2015)
        self.assertTrue(book.is_active)
        self.assertEqual(book.cover_image_url, "https://img.example/c.jpg")
        inv = Inventory.objects.get(book=book)
        self.assertEqual((inv.quantity_available, inv.reorder_threshold), (4, 2))

    def test_add_books_unpublished_and_bad_optionals_defaulted(self):
        self._post(is_published="0", publication_year="nineteen", reorder_threshold="x", isbn="")
        book = Book.objects.get()
        self.assertFalse(book.is_active)
        self.assertIsNone(book.publication_year)
        self.assertIsNone(book.isbn)
        self.assertEqual(book.inventory.reorder_threshold, 0)

    def test_add_books_cover_upload_written_and_url_cleared(self):
        cover = SimpleUploadedFile("cover.jpg", b"\xff\xd8jpegdata", content_type="image/jpeg")
        self._post(title="My: Book?", cover_image=cover)
        book = Book.objects.get()
        self.assertIsNone(book.cover_image_url)
        self.assertEqual((self.tmp / "media" / "book_images" / "My_ Book_.jpg").read_bytes(), b"\xff\xd8jpegdata")

    def test_add_books_cover_extension_inferred_from_content_type(self):
        """A file with no usable extension gets one from its content type."""
        for ctype, ext in (("image/png", ".png"), ("image/jpeg", ".jpg"), ("image/gif", ".gif")):
            with self.subTest(ctype=ctype):
                cover = SimpleUploadedFile("upload.bin", b"img", content_type=ctype)
                self._post(title=f"Inferred {ext[1:]}", cover_image=cover)
                self.assertTrue((self.tmp / "media" / "book_images" / f"Inferred {ext[1:]}{ext}").exists())


class ManageInventoryGetTests(SellerViewTestCase):
    def setUp(self):
        super().setUp()
        self.alpha = make_book(self.seller, title="Alpha", author="Zed", price_cents=300, stock=10)
        self.beta = make_book(self.seller, title="Beta", author="Young", price_cents=100, stock=2)
        self.gamma = make_book(self.seller, title="Gamma", author="Xavier", price_cents=200, stock=0, is_active=False)
        make_book(make_seller(), title="Foreign")

    def _titles(self, **params):
        resp = self.client.get(reverse("manage_inventory"), params)
        self.assertEqual(resp.status_code, 200)
        return [b.title for b in resp.context["page_obj"].object_list]

    def test_inventory_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(reverse("manage_inventory")))

    def test_inventory_lists_only_own_books_by_title(self):
        self.assertEqual(self._titles(), ["Alpha", "Beta", "Gamma"])

    def test_inventory_search_matches_title_or_author(self):
        self.assertEqual(self._titles(q="bet"), ["Beta"])
        self.assertEqual(self._titles(q="xavier"), ["Gamma"])

    def test_inventory_status_filters(self):
        cases = {
            "PUBLISHED": ["Alpha", "Beta"],
            "UNPUBLISHED": ["Gamma"],
            "OUT_OF_STOCK": ["Gamma"],
            "LOW_STOCK": ["Beta"],
            "BOGUS": ["Alpha", "Beta", "Gamma"],
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.assertEqual(self._titles(status=status), expected)

    def test_inventory_sort_options(self):
        cases = {
            "newest": ["Gamma", "Beta", "Alpha"],
            "oldest": ["Alpha", "Beta", "Gamma"],
            "stock_asc": ["Gamma", "Beta", "Alpha"],
            "stock_desc": ["Alpha", "Beta", "Gamma"],
            "price_asc": ["Beta", "Gamma", "Alpha"],
            "price_desc": ["Alpha", "Gamma", "Beta"],
            "unknown": ["Alpha", "Beta", "Gamma"],
        }
        for sort, expected in cases.items():
            with self.subTest(sort=sort):
                self.assertEqual(self._titles(sort=sort), expected)

    def test_inventory_paginates_ten_per_page(self):
        for i in range(9):
            make_book(self.seller, title=f"Zeta {i}")
        self.assertEqual(len(self._titles()), 10)
        self.assertEqual(len(self._titles(page=2)), 2)


class ManageInventoryPostTests(SellerViewTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.seller, price_cents=1000, stock=3)

    def _post(self, **overrides):
        data = {"book_id": self.book.id, "price": "12.00", "stock_quantity": "8", "is_active": "on"}
        data.update(overrides)
        return self.client.post(reverse("manage_inventory"), data)

    def test_inventory_update_saves_price_stock_and_active(self):
        resp = self._post()
        self.assertRedirects(resp, reverse("manage_inventory"), fetch_redirect_response=False)
        self.book.refresh_from_db()
        self.assertEqual(self.book.base_price_cents, 1200)
        self.assertEqual(self.book.inventory.quantity_available, 8)
        self.assertTrue(self.book.is_active)

    def test_inventory_update_without_active_checkbox_unpublishes(self):
        data = {"book_id": self.book.id, "price": "10.00", "stock_quantity": "3"}
        self.client.post(reverse("manage_inventory"), data)
        self.book.refresh_from_db()
        self.assertFalse(self.book.is_active)

    def test_inventory_update_invalid_price_rejected(self):
        resp = self._post(price="abc")
        self.assertMessage(resp, "Invalid price.")
        self.book.refresh_from_db()
        self.assertEqual(self.book.base_price_cents, 1000)

    def test_inventory_update_zero_price_rejected(self):
        self.assertMessage(self._post(price="0"), "Invalid price.")
        self.book.refresh_from_db()
        self.assertEqual(self.book.base_price_cents, 1000)

    def test_inventory_update_invalid_stock_rejected(self):
        self.assertMessage(self._post(stock_quantity="lots"), "Invalid stock quantity.")
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 3)

    def test_inventory_update_negative_stock_clamped_to_zero(self):
        self._post(stock_quantity="-5")
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 0)

    def test_inventory_update_foreign_book_returns_404(self):
        foreign = make_book(make_seller())
        self.assertEqual(self._post(book_id=foreign.id).status_code, 404)

    def test_inventory_update_creates_missing_inventory_row(self):
        book = make_book(self.seller, with_inventory=False)
        self._post(book_id=book.id, stock_quantity="6")
        self.assertEqual(Inventory.objects.get(book=book).quantity_available, 6)


class SalesOverviewViewTests(SellerViewTestCase):
    def test_sales_overview_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(reverse("seller_sales_overview")))

    def test_sales_overview_totals_count_only_recognised_statuses(self):
        book = make_book(self.seller, price_cents=1000)
        other_seller_book = make_book(make_seller(), price_cents=5000)
        make_order(self.buyer, [(book, 2), (other_seller_book, 1)], status="paid")
        make_order(self.buyer, [(book, 1)], status="delivered")
        make_order(self.buyer, [(book, 1)], status="cancelled")
        resp = self.client.get(reverse("seller_sales_overview"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_sales_display"], "30.00")
        orders = list(resp.context["recent_sales_orders"])
        self.assertEqual(len(orders), 3)
        paid = next(o for o in orders if o.status == "paid")
        self.assertEqual(paid.seller_revenue_display, "20.00")
        self.assertEqual(paid.order_total_display, "70.00")

    def test_sales_overview_book_sales_aggregated_per_title(self):
        a = make_book(self.seller, title="A", price_cents=1000)
        b = make_book(self.seller, title="B", price_cents=500)
        make_order(self.buyer, [(a, 2), (b, 1)])
        make_order(self.buyer, [(a, 1)])
        rows = self.client.get(reverse("seller_sales_overview")).context["book_sales"]
        self.assertEqual([(r["book__title"], r["units_sold"], r["revenue_display"]) for r in rows],
                         [("A", 3, "30.00"), ("B", 1, "5.00")])

    # BUG: excluded_item_ids correlates OuterRef("id") (the OrderItem id) with the order id,
    # so lines from a received return are not excluded from book_sales.
    @unittest.expectedFailure
    def test_sales_overview_excludes_lines_from_received_returns(self):
        filler = make_book(make_seller())
        make_order(self.buyer, [(filler, 1), (filler, 1), (filler, 1)])
        returned_book = make_book(self.seller, title="Returned")
        order = make_order(self.buyer, [(returned_book, 1)])
        item = order.order_items.get()
        self.assertNotEqual(item.id, order.id)  # precondition: ids diverge
        rr = make_return(order)
        SellerReturnReceipt.objects.create(return_request=rr, seller=self.seller, amount_credited_cents=1500)
        rows = self.client.get(reverse("seller_sales_overview")).context["book_sales"]
        self.assertNotIn("Returned", [r["book__title"] for r in rows])


class SellerOrdersViewTests(SellerViewTestCase):
    def test_orders_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(reverse("seller_orders")))

    def test_orders_lists_only_orders_with_own_books_and_seller_totals(self):
        mine = make_book(self.seller, price_cents=1000)
        theirs = make_book(make_seller(), price_cents=3000)
        mixed = make_order(self.buyer, [(mine, 2), (theirs, 1)])
        make_order(self.buyer, [(theirs, 1)])
        resp = self.client.get(reverse("seller_orders"))
        orders = resp.context["orders"]
        self.assertEqual(orders, [mixed])
        self.assertEqual(orders[0].seller_item_count, 2)
        self.assertEqual(orders[0].seller_line_total_display, "20.00")
        self.assertEqual(orders[0].display_order_number, 1)

    # BUG: the ?status=OPEN filter is applied to `qs` but `orders` is then rebuilt from an unfiltered query.
    @unittest.expectedFailure
    def test_orders_open_filter_excludes_delivered(self):
        book = make_book(self.seller)
        open_order = make_order(self.buyer, [(book, 1)], status="paid")
        make_order(self.buyer, [(book, 1)], status="delivered")
        resp = self.client.get(reverse("seller_orders"), {"status": "OPEN"})
        self.assertEqual(resp.context["orders"], [open_order])


class SellerOrderDetailsViewTests(SellerViewTestCase):
    def test_order_details_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(reverse("seller_order_details", args=[1])))

    def test_order_details_order_without_own_items_returns_404(self):
        order = make_order(self.buyer, [(make_book(make_seller()), 1)])
        self.assertEqual(self.client.get(reverse("seller_order_details", args=[order.id])).status_code, 404)

    def test_order_details_shows_only_own_items_and_totals(self):
        mine = make_book(self.seller, price_cents=1000)
        theirs = make_book(make_seller(), price_cents=3000)
        earlier = make_order(self.buyer, [(mine, 1)])
        order = make_order(self.buyer, [(mine, 2), (theirs, 1)])
        make_review(self.buyer, order.order_items.get(book=mine), rating=5)
        resp = self.client.get(reverse("seller_order_details", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        items = list(resp.context["items"])
        self.assertEqual([i.book for i in items], [mine])
        self.assertEqual(resp.context["seller_line_total_display"], "20.00")
        self.assertEqual(resp.context["order_total_display"], "50.00")
        self.assertEqual(resp.context["display_order_number"], 2)
        self.assertEqual(
            self.client.get(reverse("seller_order_details", args=[earlier.id])).context["display_order_number"], 1
        )


class ReturnRequestsListViewTests(SellerViewTestCase):
    def test_returns_list_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(reverse("seller_return_requests")))

    def test_returns_list_shows_only_pending_returns_for_own_books(self):
        book = make_book(self.seller)
        pending = make_return(make_order(self.buyer, [(book, 1)]))
        make_return(make_order(self.buyer, [(book, 1)]), status="rejected")
        received = make_return(make_order(self.buyer, [(book, 1)]))
        SellerReturnReceipt.objects.create(return_request=received, seller=self.seller, amount_credited_cents=1)
        make_return(make_order(self.buyer, [(make_book(make_seller()), 1)]))
        resp = self.client.get(reverse("seller_return_requests"))
        self.assertEqual(list(resp.context["return_requests"]), [pending])


class ReturnRequestDetailViewTests(SellerViewTestCase):
    def setUp(self):
        super().setUp()
        self.book = make_book(self.seller, price_cents=1500, stock=1)
        self.order = make_order(self.buyer, [(self.book, 2)], status="delivered")
        self.rr = make_return(self.order)
        self.url = reverse("seller_return_request_detail", args=[self.rr.id])

    def test_return_detail_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(self.url))

    def test_return_detail_missing_return_returns_404(self):
        self.assertEqual(self.client.get(reverse("seller_return_request_detail", args=[999_999])).status_code, 404)

    def test_return_detail_return_without_own_lines_redirects_to_list(self):
        other_rr = make_return(make_order(self.buyer, [(make_book(make_seller()), 1)]))
        resp = self.client.get(reverse("seller_return_request_detail", args=[other_rr.id]))
        self.assertRedirects(resp, reverse("seller_return_requests"), fetch_redirect_response=False)
        self.assertMessage(resp, "does not include any of your listings")

    def test_return_detail_get_shows_seller_total(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["seller_lines_total_display"], "30.00")
        self.assertIsNone(resp.context["already_received"])

    def test_return_detail_book_received_credits_buyer_and_restocks(self):
        resp = self.client.post(self.url, {"action": "book_received"})
        self.assertRedirects(resp, self.url, fetch_redirect_response=False)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 3000)
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 3)
        receipt = SellerReturnReceipt.objects.get(return_request=self.rr)
        self.assertEqual((receipt.seller, receipt.amount_credited_cents), (self.seller, 3000))
        self.rr.refresh_from_db()
        self.assertEqual(self.rr.status, "refunded")

    def test_return_detail_book_received_creates_inventory_if_missing(self):
        book = make_book(self.seller, with_inventory=False)
        rr = make_return(make_order(self.buyer, [(book, 1)]))
        self.client.post(reverse("seller_return_request_detail", args=[rr.id]), {"action": "book_received"})
        self.assertEqual(Inventory.objects.get(book=book).quantity_available, 1)

    def test_return_detail_multi_seller_finalizes_only_after_all_sellers(self):
        other_seller = make_seller()
        other_book = make_book(other_seller, price_cents=700)
        order = make_order(self.buyer, [(self.book, 1), (other_book, 1)])
        rr = make_return(order)
        url = reverse("seller_return_request_detail", args=[rr.id])
        self.client.post(url, {"action": "book_received"})
        rr.refresh_from_db()
        self.assertEqual(rr.status, "requested")
        self.login(other_seller)
        self.client.post(url, {"action": "book_received"})
        rr.refresh_from_db()
        self.assertEqual(rr.status, "refunded")
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 1500 + 700)

    def test_return_detail_second_receipt_is_rejected(self):
        self.client.post(self.url, {"action": "book_received"})
        resp = self.client.post(self.url, {"action": "book_received"})
        self.assertMessage(resp, "already marked this return as received")
        self.assertEqual(SellerReturnReceipt.objects.count(), 1)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 3000)

    def test_return_detail_get_after_receipt_shows_credited_amount(self):
        self.client.post(self.url, {"action": "book_received"})
        resp = self.client.get(self.url)
        self.assertIsNotNone(resp.context["already_received"])
        self.assertEqual(resp.context["amount_credited_display"], "30.00")

    def test_return_detail_rejected_return_not_finalized(self):
        """A rejected return keeps its status even after a receipt is recorded."""
        self.rr.status = "rejected"
        self.rr.save()
        self.client.post(self.url, {"action": "book_received"})
        self.rr.refresh_from_db()
        self.assertEqual(self.rr.status, "rejected")

    def test_return_detail_unknown_action_renders_without_changes(self):
        resp = self.client.post(self.url, {"action": "something"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SellerReturnReceipt.objects.exists())

    # BUG: approve/deny branches are nested under `action == "book_received"`, so they can never run.
    @unittest.expectedFailure
    def test_return_detail_approve_sets_status_approved(self):
        self.client.post(self.url, {"action": "approve"})
        self.rr.refresh_from_db()
        self.assertEqual(self.rr.status, "approved")

    # BUG: approve/deny branches are nested under `action == "book_received"`, so they can never run.
    @unittest.expectedFailure
    def test_return_detail_deny_sets_status_rejected(self):
        self.client.post(self.url, {"action": "deny"})
        self.rr.refresh_from_db()
        self.assertEqual(self.rr.status, "rejected")


class UpdateOrderStatusViewTests(SellerViewTestCase):
    def setUp(self):
        super().setUp()
        self.order = make_order(self.buyer, [(make_book(self.seller), 1)], status="paid")
        self.url = reverse("update_order_status", args=[self.order.id])

    def test_update_status_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_update_status_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.post(self.url, {"status": "shipped"}))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")

    def test_update_status_missing_order_returns_404(self):
        resp = self.client.post(reverse("update_order_status", args=[999_999]), {"status": "shipped"})
        self.assertEqual(resp.status_code, 404)

    def test_update_status_foreign_order_rejected(self):
        foreign = make_order(self.buyer, [(make_book(make_seller()), 1)])
        resp = self.client.post(reverse("update_order_status", args=[foreign.id]), {"status": "shipped"})
        self.assertRedirects(resp, reverse("seller_orders"), fetch_redirect_response=False)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, "paid")

    def test_update_status_valid_status_saved_case_insensitively(self):
        resp = self.client.post(self.url, {"status": " Shipped "})
        self.assertRedirects(resp, reverse("seller_order_details", args=[self.order.id]), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "shipped")
        self.assertMessage(resp, "Order status updated to Shipped.")

    # BUG: invalid-status branch redirects to " seller_order_details" (leading space) -> NoReverseMatch / HTTP 500.
    @unittest.expectedFailure
    def test_update_status_invalid_status_redirects_with_error(self):
        resp = self.client.post(self.url, {"status": "refunded"})
        self.assertRedirects(resp, reverse("seller_order_details", args=[self.order.id]), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")


class SellerWebhooksViewTests(SellerViewTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("seller_webhooks")

    def test_webhooks_non_seller_redirected(self):
        self.login(self.buyer)
        self.assertRedirectsToBuyerHome(self.client.get(self.url))

    def test_webhooks_get_renders_form_and_creates_profile(self):
        seller = self.login(make_user("seller"))
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("form", resp.context)
        self.assertTrue(SellerProfile.objects.filter(user=seller).exists())

    def test_webhooks_save_valid_settings(self):
        resp = self.client.post(self.url, {"webhook_url": "https://hooks.example/o",
                                           "webhook_secret": "s3", "webhook_enabled": "on"})
        self.assertRedirects(resp, self.url, fetch_redirect_response=False)
        profile = SellerProfile.objects.get(user=self.seller)
        self.assertEqual((profile.webhook_url, profile.webhook_secret, profile.webhook_enabled),
                         ("https://hooks.example/o", "s3", True))

    def test_webhooks_enabled_without_url_rejected(self):
        resp = self.client.post(self.url, {"webhook_url": "", "webhook_enabled": "on"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("webhook_url", resp.context["form"].errors)
        self.assertFalse(SellerProfile.objects.get(user=self.seller).webhook_enabled)

    def test_webhooks_test_action_without_url_shows_error(self):
        resp = self.client.post(self.url, {"action": "test", "webhook_url": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertMessage(resp, "Enter a webhook URL to test.")

    @mock.patch("app.seller.views.post_seller_webhook", return_value=(True, None))
    def test_webhooks_test_action_success_reports_delivery(self, post):
        resp = self.client.post(self.url, {"action": "test", "webhook_url": "https://hooks.example/t",
                                           "webhook_secret": "abc"})
        self.assertMessage(resp, "delivered successfully")
        url, secret, payload = post.call_args.args
        self.assertEqual((url, secret, payload["event"]), ("https://hooks.example/t", "abc", "webhook.test"))

    @mock.patch("app.seller.views.post_seller_webhook", return_value=(False, "HTTP 500"))
    def test_webhooks_test_action_failure_reports_error(self, post):
        resp = self.client.post(self.url, {"action": "test", "webhook_url": "https://hooks.example/t"})
        self.assertMessage(resp, "Test webhook failed: HTTP 500")
        self.assertFalse(SellerProfile.objects.get(user=self.seller).webhook_enabled)
