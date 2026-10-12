"""Integration: seller listing lifecycle as seen by buyers, admins, and the checkout stock check."""

from django.test import Client

from Buyer.models import CartItem
from General.models import Book, Inventory
from tests.helpers import (
    PassItOnTestCase,
    add_cart_item,
    make_address,
    make_admin,
    make_book,
    make_buyer,
    make_payment_method,
    make_seller,
)


def catalog_ids(client, **params):
    resp = client.get("/catalog/", params)
    return [b.id for b in resp.context["page_obj"].object_list]


def edit_listing(client, book, *, price="15.00", stock=5, active=True):
    data = {"book_id": book.id, "price": price, "stock_quantity": stock}
    if active:
        data["is_active"] = "on"
    return client.post("/seller/inventory/", data)


class InventoryLifecycleTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.login(self.seller)
        self.buyer = make_buyer()
        self.buyer_client = Client()
        self.buyer_client.force_login(self.buyer)

    def test_seller_adds_book_then_buyer_finds_it_in_catalog_search_and_detail(self):
        resp = self.client.post("/seller/inventory/add/", {
            "title": "Organic Chemistry Vol 2", "author": "Klein", "condition": "Like New",
            "price": "42.50", "quantity_available": "3", "isbn": "9781118", "is_published": "1",
            "reorder_threshold": "1",
        })
        self.assertRedirects(resp, "/seller/inventory/", fetch_redirect_response=False)
        book = Book.objects.get(title="Organic Chemistry Vol 2")
        self.assertEqual((book.seller_user, book.base_price_cents, book.stock_quantity), (self.seller, 4250, 3))

        self.assertIn(book.id, catalog_ids(self.buyer_client, q="organic chem"))
        self.assertIn(book.id, catalog_ids(self.buyer_client, q="klein"))
        self.assertNotIn(book.id, catalog_ids(self.buyer_client, q="no such title"))
        detail = self.buyer_client.get(f"/catalog/{book.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["seller_display_name"],
                         f"{self.seller.first_name} {self.seller.last_name}")

        listing = self.client.get("/seller/inventory/")
        self.assertIn(book, list(listing.context["page_obj"].object_list))

    def test_seller_adds_unpublished_book_hidden_from_catalog(self):
        self.client.post("/seller/inventory/add/", {
            "title": "Draft Listing", "author": "A", "condition": "Good", "price": "5",
            "quantity_available": "1", "is_published": "0",
        })
        book = Book.objects.get(title="Draft Listing")
        self.assertFalse(book.is_active)
        self.assertNotIn(book.id, catalog_ids(self.buyer_client))

    def test_seller_unpublishes_book_hides_it_and_purges_buyer_cart_line(self):
        book = make_book(self.seller)
        add_cart_item(self.buyer, book)
        self.assertIn(book.id, catalog_ids(self.buyer_client))

        resp = edit_listing(self.client, book, active=False)
        self.assertMessage(resp, "Listing updated.")
        self.assertNotIn(book.id, catalog_ids(self.buyer_client))
        self.assertEqual(self.buyer_client.get(f"/catalog/{book.id}/").status_code, 404)

        cart = self.buyer_client.get("/buyer/cart/")
        self.assertEqual(cart.context["cart_items"], [])
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())

        # Republishing makes it visible again.
        edit_listing(self.client, book, active=True)
        self.assertIn(book.id, catalog_ids(self.buyer_client))

    def test_seller_price_change_refreshes_cart_price_on_next_add_or_update(self):
        book = make_book(self.seller, price_cents=1000)
        self.buyer_client.post(f"/buyer/cart/add/{book.id}/", {"quantity": 1})
        edit_listing(self.client, book, price="12.00")

        ci = CartItem.objects.get(cart__user=self.buyer, book=book)
        self.assertEqual(ci.unit_price_cents, 1000)  # stored until the line is touched
        self.buyer_client.post(f"/buyer/cart/update/{book.id}/", {"quantity": 2})
        ci.refresh_from_db()
        self.assertEqual((ci.unit_price_cents, ci.quantity), (1200, 2))

        edit_listing(self.client, book, price="13.00")
        self.buyer_client.post(f"/buyer/cart/add/{book.id}/", {"quantity": 1})
        ci.refresh_from_db()
        self.assertEqual((ci.unit_price_cents, ci.quantity), (1300, 3))
        self.assertEqual(self.buyer_client.get("/buyer/cart/").context["cart_subtotal"], 39.0)

    def test_seller_invalid_price_edit_rejected_and_listing_unchanged(self):
        book = make_book(self.seller, price_cents=1000, stock=4)
        for bad in ("0", "-3", "abc"):
            resp = edit_listing(self.client, book, price=bad, stock=99)
            self.assertMessage(resp, "Invalid price.")
        book.refresh_from_db()
        self.assertEqual((book.base_price_cents, book.stock_quantity), (1000, 4))

    def test_stock_changes_reflected_in_catalog_in_stock_filter(self):
        book = make_book(self.seller, stock=0)
        self.assertIn(book.id, catalog_ids(self.buyer_client))
        self.assertNotIn(book.id, catalog_ids(self.buyer_client, in_stock="1"))
        edit_listing(self.client, book, stock=2)
        self.assertIn(book.id, catalog_ids(self.buyer_client, in_stock="1"))
        listing = self.client.get("/seller/inventory/", {"status": "LOW_STOCK"})
        self.assertEqual([b.id for b in listing.context["page_obj"].object_list], [book.id])

    def test_admin_freeze_book_hides_from_catalog_and_unfreeze_restores(self):
        book = make_book(self.seller)
        admin_client = Client()
        admin_client.force_login(make_admin())

        resp = admin_client.post(f"/staff/books/{book.id}/toggle-freeze/")
        self.assertRedirects(resp, "/staff/books/", fetch_redirect_response=False)
        self.assertNotIn(book.id, catalog_ids(self.buyer_client))
        self.assertEqual(self.buyer_client.get(f"/catalog/{book.id}/").status_code, 404)
        # Seller still sees the frozen listing in their inventory, as unpublished.
        unpublished = self.client.get("/seller/inventory/", {"status": "UNPUBLISHED"})
        self.assertIn(book, list(unpublished.context["page_obj"].object_list))

        admin_client.post(f"/staff/books/{book.id}/toggle-freeze/")
        self.assertIn(book.id, catalog_ids(self.buyer_client))

    def test_purchases_decrement_inventory_and_update_seller_stock_stats(self):
        book = make_book(self.seller, price_cents=1000, stock=4)
        addr, pm = make_address(self.buyer), make_payment_method(self.buyer)

        stats = self.client.get("/seller/dashboard/").context["stats"]
        self.assertEqual((stats["low_stock_count"], stats["out_of_stock_count"]), (0, 0))

        add_cart_item(self.buyer, book, quantity=1)
        self.buyer_client.post("/buyer/checkout/", {"shipping_address_id": addr.id,
                                                    "payment_method_id": pm.id, "steward_contribution": "0"})
        self.assertEqual(Inventory.objects.get(book=book).quantity_available, 3)
        stats = self.client.get("/seller/dashboard/").context["stats"]
        self.assertEqual((stats["low_stock_count"], stats["out_of_stock_count"]), (1, 0))

        add_cart_item(self.buyer, book, quantity=3)
        self.buyer_client.post("/buyer/checkout/", {"shipping_address_id": addr.id,
                                                    "payment_method_id": pm.id, "steward_contribution": "0"})
        self.assertEqual(Inventory.objects.get(book=book).quantity_available, 0)
        stats = self.client.get("/seller/dashboard/").context["stats"]
        self.assertEqual((stats["low_stock_count"], stats["out_of_stock_count"]), (0, 1))
        self.assertNotIn(book.id, catalog_ids(self.buyer_client, in_stock="1"))

        admin_client = Client()
        admin_client.force_login(make_admin())
        inv_rows = admin_client.get("/staff/inventory/").context["inventory"]
        self.assertEqual([i.quantity_available for i in inv_rows if i.book_id == book.id], [0])

    def test_seller_cannot_edit_another_sellers_listing(self):
        other_seller = make_seller()
        foreign = make_book(other_seller, price_cents=1000, stock=2)
        resp = edit_listing(self.client, foreign, price="1.00", stock=0, active=False)
        self.assertEqual(resp.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual((foreign.base_price_cents, foreign.stock_quantity, foreign.is_active), (1000, 2, True))
        listing = self.client.get("/seller/inventory/")
        self.assertNotIn(foreign, list(listing.context["page_obj"].object_list))
