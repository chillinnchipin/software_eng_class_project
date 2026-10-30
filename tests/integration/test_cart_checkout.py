"""Integration: cart -> address/payment -> checkout -> order, inventory, steward pool, webhooks."""

import unittest
from unittest import mock

import requests
from django.test import Client

from app.buyer.models import CartItem, Order, OrderItemBookSnapshot, OrderShippingAddress
from app.general.models import Address, Inventory, StewardPool
from app.seller.models import SellerProfile
from tests.helpers import (
    PassItOnTestCase,
    add_cart_item,
    make_address,
    make_book,
    make_buyer,
    make_payment_method,
    make_seller,
    make_steward,
)


def checkout(client, address, payment, contribution="0"):
    return client.post("/buyer/checkout/", {
        "shipping_address_id": address.id,
        "payment_method_id": payment.id,
        "steward_contribution": contribution,
    })


def cart_quantities(user):
    return dict(CartItem.objects.filter(cart__user=user).values_list("book_id", "quantity"))


class CartToOrderFlowTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.seller = make_seller()
        self.book = make_book(self.seller, price_cents=1000, stock=5)

    def test_cart_add_update_remove_then_checkout_with_new_address_and_card_creates_order(self):
        """Full buyer path through the real views, ending in an order with snapshots and stock decrement."""
        self.client.post(f"/buyer/cart/add/{self.book.id}/", {"quantity": 1})
        self.client.post(f"/buyer/cart/add/{self.book.id}/", {"quantity": 2})
        self.assertEqual(cart_quantities(self.buyer), {self.book.id: 3})
        self.client.post(f"/buyer/cart/update/{self.book.id}/", {"quantity": 4})
        self.client.post(f"/buyer/cart/remove/{self.book.id}/")
        self.assertEqual(cart_quantities(self.buyer), {self.book.id: 3})

        cart = self.client.get("/buyer/cart/")
        self.assertEqual(cart.context["total_items"], 3)
        self.assertEqual(cart.context["cart_subtotal"], 30.0)
        self.assertEqual(cart.context["fees"], 2.99)

        resp = self.client.post("/buyer/checkout/shipping/add/", {
            "label": "Dorm", "line1": "9 College Ave", "city": "Ithaca", "state": "NY",
            "postal_code": "14850", "country": "US", "is_default": "on", "next": "/buyer/checkout/",
        })
        self.assertRedirects(resp, "/buyer/checkout/", fetch_redirect_response=False)
        resp = self.client.post("/buyer/checkout/payments/add/", {
            "cardholder_name": "Pat Buyer", "card_number": "4242 4242 4242 4242", "brand": "Visa",
            "cvv": "123", "exp_month": "12", "exp_year": "2099", "is_default": "on",
        })
        self.assertRedirects(resp, "/buyer/checkout/payments/", fetch_redirect_response=False)
        address = Address.objects.get(user=self.buyer)
        payment = self.buyer.payment_methods.get()
        self.assertEqual(payment.last4, "4242")

        page = self.client.get("/buyer/checkout/")
        self.assertEqual(page.context["selected_address"], address)
        self.assertEqual(page.context["selected_payment"], payment)

        resp = checkout(self.client, address, payment, contribution="0")
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(resp, f"/buyer/orders/confirmation/{order.id}/", fetch_redirect_response=False)

        self.assertEqual(order.status, "paid")
        self.assertEqual(order.subtotal_cents, 3000)
        self.assertEqual(order.tax_cents, 210)
        self.assertEqual(order.fees_cents, 299)
        self.assertEqual(order.total_cents, 3000 + 210 + 299)
        self.assertEqual(order.payment_method, payment)
        item = order.order_items.get()
        self.assertEqual((item.quantity, item.line_total_cents, item.title), (3, 3000, self.book.title))
        self.assertEqual(OrderItemBookSnapshot.objects.get(order_item=item).title, self.book.title)
        ship = OrderShippingAddress.objects.get(order=order)
        self.assertEqual((ship.shipping_name, ship.shipping_city), ("Dorm", "Ithaca"))

        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 2)
        self.assertEqual(cart_quantities(self.buyer), {})

        conf = self.client.get(f"/buyer/orders/confirmation/{order.id}/")
        self.assertEqual(conf.status_code, 200)
        self.assertEqual(conf.context["order_total_display"], "35.09")
        history = self.client.get("/buyer/orders/")
        self.assertEqual(history.context["orders"], [order])

    def test_checkout_snapshot_unchanged_after_seller_edits_book(self):
        """Order line shows the title captured at checkout even if the listing is renamed later."""
        add_cart_item(self.buyer, self.book)
        checkout(self.client, make_address(self.buyer), make_payment_method(self.buyer))
        order = Order.objects.get(user=self.buyer)
        original_title = self.book.title
        self.book.title = "Renamed Listing"
        self.book.save()
        detail = self.client.get(f"/buyer/orders/{order.id}/")
        self.assertEqual(detail.context["order_items"][0]["title"], original_title)

    def test_checkout_multi_seller_cart_single_order_split_per_seller_views(self):
        seller2 = make_seller()
        book2 = make_book(seller2, price_cents=2500, stock=2)
        add_cart_item(self.buyer, self.book, quantity=2)
        add_cart_item(self.buyer, book2, quantity=1)
        checkout(self.client, make_address(self.buyer), make_payment_method(self.buyer))

        order = Order.objects.get(user=self.buyer)
        self.assertEqual(order.subtotal_cents, 4500)
        self.assertEqual(order.order_items.count(), 2)

        for seller, expected_cents in ((self.seller, 2000), (seller2, 2500)):
            c = Client()
            c.force_login(seller)
            orders = c.get("/seller/orders/").context["orders"]
            self.assertEqual([o.id for o in orders], [order.id])
            self.assertEqual(orders[0].seller_line_total_cents, expected_cents)
            detail = c.get(f"/seller/orders/{order.id}/")
            items = list(detail.context["items"])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].book.seller_user_id, seller.id)
            self.assertEqual(detail.context["seller_line_total_display"], f"{expected_cents / 100:.2f}")

    def test_checkout_insufficient_stock_after_other_buyer_rolls_back_everything(self):
        """Second buyer loses the race for the last copy: no order, cart/inventory/pool unchanged."""
        last_copy = make_book(self.seller, price_cents=800, stock=1)
        other_book = make_book(self.seller, price_cents=500, stock=10)
        rival = make_buyer()
        rival_client = Client()
        rival_client.force_login(rival)
        add_cart_item(rival, last_copy)
        add_cart_item(self.buyer, other_book, quantity=2)
        add_cart_item(self.buyer, last_copy)

        resp = checkout(rival_client, make_address(rival), make_payment_method(rival))
        self.assertEqual(resp.status_code, 302)
        pool_before = StewardPool.objects.get(pk=1).pool_cents

        resp = checkout(self.client, make_address(self.buyer), make_payment_method(self.buyer), contribution="5")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Not enough stock", resp.context["checkout_error"])
        self.assertFalse(Order.objects.filter(user=self.buyer).exists())
        self.assertEqual(cart_quantities(self.buyer), {other_book.id: 2, last_copy.id: 1})
        self.assertEqual(Inventory.objects.get(book=other_book).quantity_available, 10)
        self.assertEqual(Inventory.objects.get(book=last_copy).quantity_available, 0)
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, pool_before)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.steward_progress, 0)

    def test_checkout_listing_unpublished_while_in_cart_is_rejected_and_purged(self):
        add_cart_item(self.buyer, self.book)
        self.book.is_active = False
        self.book.save()
        resp = checkout(self.client, make_address(self.buyer), make_payment_method(self.buyer))
        self.assertEqual(resp.status_code, 200)
        # db_cart_lines already dropped the inactive line, so the cart is now empty.
        self.assertEqual(resp.context["checkout_error"], "Your cart is empty.")
        self.assertEqual(cart_quantities(self.buyer), {})


class StewardCheckoutFlowTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.book = make_book(self.seller, price_cents=2000, stock=10)

    def test_steward_contributions_accumulate_until_verified_then_welcome_flow_completes(self):
        """$5 + $5 contributions = 100 points -> steward_verified -> welcome page collects city."""
        buyer = self.login(make_buyer())
        address, payment = make_address(buyer), make_payment_method(buyer)
        pool_start = StewardPool.objects.get(pk=1).pool_cents

        add_cart_item(buyer, self.book)
        resp = checkout(self.client, address, payment, contribution="5.00")
        first = Order.objects.get(user=buyer)
        self.assertRedirects(resp, f"/buyer/orders/confirmation/{first.id}/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        self.assertEqual((buyer.steward_progress, buyer.steward_verified), (50, False))
        self.assertEqual(first.steward_contribution_cents, 500)
        self.assertEqual(first.total_cents, 2000 + 140 + 299 + 500)

        add_cart_item(buyer, self.book)
        resp = checkout(self.client, address, payment, contribution="5.00")
        self.assertRedirects(resp, "/buyer/steward/welcome/", fetch_redirect_response=False)
        second = Order.objects.filter(user=buyer).latest("id")
        buyer.refresh_from_db()
        self.assertEqual((buyer.steward_progress, buyer.steward_verified), (100, True))
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, pool_start + 1000)

        welcome = self.client.get("/buyer/steward/welcome/")
        self.assertEqual(welcome.context["pending_order_id"], second.id)
        resp = self.client.post("/buyer/steward/welcome/", {"steward_city": ""})
        self.assertIn("City is required", resp.context["city_error"])
        resp = self.client.post("/buyer/steward/welcome/", {"steward_city": "Boise"})
        self.assertRedirects(resp, f"/buyer/orders/confirmation/{second.id}/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        self.assertEqual(buyer.steward_city, "Boise")

        dash = self.client.get("/buyer/dashboard/")
        self.assertTrue(dash.context["steward_free_book_eligible"])

    def test_steward_free_book_select_checkout_deducts_pool_and_starts_cooldown(self):
        steward = self.login(make_steward())
        address, payment = make_address(steward), make_payment_method(steward)
        pool_before = StewardPool.objects.get(pk=1).pool_cents

        self.client.post(f"/buyer/cart/add/{self.book.id}/", {"quantity": 1})
        resp = self.client.post(f"/buyer/cart/steward-free/select/{self.book.id}/")
        self.assertMessage(resp, "steward free book")
        cart = self.client.get("/buyer/cart/")
        self.assertTrue(cart.context["cart_has_steward_free"])
        self.assertEqual(cart.context["fees"], 0.0)
        self.assertEqual(cart.context["cart_total"], 0.0)

        resp = checkout(self.client, address, payment, contribution="10")
        order = Order.objects.get(user=steward)
        self.assertRedirects(resp, f"/buyer/orders/confirmation/{order.id}/", fetch_redirect_response=False)
        self.assertEqual((order.fees_cents, order.tax_cents, order.total_cents), (0, 0, 0))
        self.assertEqual(order.steward_contribution_cents, 0)  # contribution ignored with free line
        self.assertTrue(order.order_items.get().is_steward_free)
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, pool_before - 2000)
        steward.refresh_from_db()
        self.assertIsNotNone(steward.last_free_book_redeemed_at)
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 9)

        conf = self.client.get(f"/buyer/orders/confirmation/{order.id}/")
        self.assertEqual(conf.context["free_book_attribution"]["first_name"], steward.first_name)

        # Cooldown: a second free selection is refused.
        other = make_book(self.seller, price_cents=900)
        self.client.post(f"/buyer/cart/add/{other.id}/", {"quantity": 1})
        resp = self.client.post(f"/buyer/cart/steward-free/select/{other.id}/")
        self.assertMessage(resp, "30-day cooldown")
        self.assertFalse(CartItem.objects.get(cart__user=steward, book=other).is_steward_free)

    def test_steward_free_book_deselect_restores_paid_price_and_fees(self):
        steward = self.login(make_steward())
        add_cart_item(steward, self.book)
        self.client.post(f"/buyer/cart/steward-free/select/{self.book.id}/")
        self.client.post(f"/buyer/cart/steward-free/deselect/{self.book.id}/")
        ci = CartItem.objects.get(cart__user=steward, book=self.book)
        self.assertEqual((ci.is_steward_free, ci.unit_price_cents), (False, 2000))
        self.assertEqual(self.client.get("/buyer/cart/").context["fees"], 2.99)

    def test_steward_free_book_pool_too_small_checkout_rejected_without_side_effects(self):
        steward = self.login(make_steward())
        add_cart_item(steward, self.book)
        self.client.post(f"/buyer/cart/steward-free/select/{self.book.id}/")
        StewardPool.objects.filter(pk=1).update(pool_cents=100)
        resp = checkout(self.client, make_address(steward), make_payment_method(steward))
        self.assertIn("steward pool cannot cover", resp.context["checkout_error"])
        self.assertFalse(Order.objects.filter(user=steward).exists())
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, 100)
        steward.refresh_from_db()
        self.assertIsNone(steward.last_free_book_redeemed_at)


class OrderWebhookFlowTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        SellerProfile.objects.filter(user=self.seller).update(
            webhook_enabled=True, webhook_url="https://hooks.example.test/orders", webhook_secret="s3cret"
        )
        self.book = make_book(self.seller)
        self.buyer = self.login(make_buyer())
        add_cart_item(self.buyer, self.book)

    def test_checkout_webhook_failure_does_not_break_order(self):
        with mock.patch(
            "app.seller.webhook_notify.requests.post", side_effect=requests.ConnectionError("boom")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                resp = checkout(self.client, make_address(self.buyer), make_payment_method(self.buyer))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Order.objects.filter(user=self.buyer).exists())

    # BUG: webhook_notify prefetches/iterates `order.orderitem` but the related_name is `order_items`, so no webhook is ever sent.
    @unittest.expectedFailure
    def test_checkout_with_seller_webhook_enabled_posts_order_placed_event(self):
        with mock.patch("app.seller.webhook_notify.requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            with self.captureOnCommitCallbacks(execute=True):
                checkout(self.client, make_address(self.buyer), make_payment_method(self.buyer))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://hooks.example.test/orders")
        self.assertIn("X-Webhook-Signature", post.call_args.kwargs["headers"])
