"""Unit tests for every view in Buyer/views.py (one test class per view)."""

import unittest
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.urls import reverse
from django.utils import timezone

from app.buyer.models import (
    CartItem,
    Order,
    OrderItem,
    OrderItemBookSnapshot,
    OrderShippingAddress,
    PaymentMethod,
    ReturnRequest,
    Review,
)
from app.general.models import Address, Inventory, StewardPool
from tests.helpers import (
    PASSWORD,
    PassItOnTestCase,
    add_cart_item,
    ensure_pool,
    make_address,
    make_book,
    make_buyer,
    make_order,
    make_payment_method,
    make_return,
    make_review,
    make_seller,
    make_steward,
)


def _free_line(user, book, **extra):
    """Cart line already marked as the steward free book."""
    fields = {
        "quantity": 1,
        "unit_price_cents": 0,
        "is_steward_free": True,
        "steward_free_list_price_cents": book.base_price_cents,
    }
    fields.update(extra)
    return add_cart_item(user, book, **fields)


class BuyerHomeViewTests(PassItOnTestCase):
    def test_home_anonymous_redirects_to_login(self):
        resp = self.client.get(reverse("buyer_home"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_home_buyer_renders_dashboard_with_stats_and_credit(self):
        buyer = self.login(make_buyer(store_credit_cents=1234))
        book = make_book(make_seller())
        make_order(buyer, [(book, 1)], status="paid")
        make_order(buyer, [(book, 1)], status="delivered")
        resp = self.client.get(reverse("buyer_home"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["credit_balance"], "12.34")
        self.assertEqual(resp.context["stats"], {"total_orders": 2, "open_orders": 1})
        self.assertEqual(len(resp.context["recent_orders"]), 2)
        self.assertNotIn("steward_pool_dollars", resp.context)

    def test_home_response_is_not_cached(self):
        self.login(make_buyer())
        resp = self.client.get(reverse("buyer_home"))
        self.assertIn("no-cache", resp["Cache-Control"])


class BuyerDashboardViewTests(PassItOnTestCase):
    def test_dashboard_steward_includes_steward_pool_keys(self):
        self.login(make_steward())
        ensure_pool(123_456)
        resp = self.client.get(reverse("buyer_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["steward_pool_dollars"], "1,234.56")
        self.assertTrue(resp.context["steward_free_book_eligible"])
        self.assertEqual(resp.context["steward_free_cooldown_progress"], 100)
        self.assertIsNone(resp.context["steward_next_free_at"])

    def test_dashboard_recent_orders_limited_to_five(self):
        buyer = self.login(make_buyer())
        book = make_book()
        for _ in range(7):
            make_order(buyer, [(book, 1)], with_snapshots=False)
        resp = self.client.get(reverse("buyer_dashboard"))
        self.assertEqual(len(resp.context["recent_orders"]), 5)
        self.assertEqual(resp.context["stats"]["total_orders"], 7)


class AddToCartViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.book = make_book(make_seller())

    def _post(self, book_id=None, **data):
        return self.client.post(reverse("add_to_cart", args=[book_id or self.book.id]), data)

    def test_add_to_cart_get_does_not_add_and_redirects_to_cart(self):
        resp = self.client.get(reverse("add_to_cart", args=[self.book.id]))
        self.assertRedirects(resp, reverse("cart"), fetch_redirect_response=False)
        self.assertFalse(CartItem.objects.exists())

    def test_add_to_cart_valid_quantity_creates_line(self):
        resp = self._post(quantity="3")
        self.assertRedirects(resp, reverse("cart"), fetch_redirect_response=False)
        ci = CartItem.objects.get(cart__user=self.buyer, book=self.book)
        self.assertEqual(ci.quantity, 3)
        self.assertEqual(ci.unit_price_cents, self.book.base_price_cents)

    def test_add_to_cart_non_numeric_quantity_defaults_to_one(self):
        self._post(quantity="lots")
        self.assertEqual(CartItem.objects.get().quantity, 1)

    def test_add_to_cart_negative_quantity_clamped_to_one(self):
        self._post(quantity="-4")
        self.assertEqual(CartItem.objects.get().quantity, 1)

    def test_add_to_cart_existing_line_increments_quantity(self):
        self._post(quantity="1")
        self._post(quantity="2")
        self.assertEqual(CartItem.objects.get().quantity, 3)

    def test_add_to_cart_inactive_book_returns_404(self):
        inactive = make_book(is_active=False)
        self.assertEqual(self._post(book_id=inactive.id).status_code, 404)

    def test_add_to_cart_missing_book_returns_404(self):
        self.assertEqual(self._post(book_id=999_999).status_code, 404)

    def test_add_to_cart_steward_free_line_blocks_paid_copy_with_error(self):
        _free_line(self.buyer, self.book)
        resp = self._post(quantity="1")
        self.assertMessage(resp, "already in your cart as a steward free book")
        self.assertEqual(CartItem.objects.get().quantity, 1)


class BuyerCartViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def test_buyer_cart_empty_has_zero_totals_and_no_fee(self):
        resp = self.client.get(reverse("cart"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["cart_items"], [])
        self.assertEqual(resp.context["fees"], 0.0)
        self.assertEqual(resp.context["cart_total"], 0.0)
        self.assertEqual(resp.context["total_items"], 0)

    def test_buyer_cart_with_items_applies_tax_and_flat_fee(self):
        book = make_book(price_cents=1000)
        add_cart_item(self.buyer, book, quantity=2)
        resp = self.client.get(reverse("cart"))
        ctx = resp.context
        self.assertEqual(ctx["cart_subtotal"], 20.0)
        self.assertEqual(ctx["tax"], 1.4)
        self.assertEqual(ctx["fees"], 2.99)
        self.assertEqual(ctx["cart_total"], 24.39)
        self.assertEqual(ctx["total_items"], 2)
        self.assertFalse(ctx["cart_items"][0]["steward_priv"]["show"])

    def test_buyer_cart_steward_free_line_waives_fee(self):
        steward = self.login(make_steward())
        _free_line(steward, make_book(price_cents=1000))
        resp = self.client.get(reverse("cart"))
        self.assertTrue(resp.context["cart_has_steward_free"])
        self.assertEqual(resp.context["fees"], 0.0)
        priv = resp.context["cart_items"][0]["steward_priv"]
        self.assertTrue(priv["show"])
        self.assertTrue(priv["is_free_line"])

    def test_buyer_cart_steward_paid_line_offers_free_selection(self):
        steward = self.login(make_steward())
        add_cart_item(steward, make_book(price_cents=1000), quantity=1)
        priv = self.client.get(reverse("cart")).context["cart_items"][0]["steward_priv"]
        self.assertTrue(priv["show"])
        self.assertFalse(priv["select_disabled"])

    def test_buyer_cart_inactive_book_line_is_dropped(self):
        """db_cart_lines deletes lines whose book was deactivated after being added."""
        add_cart_item(self.buyer, make_book(is_active=False))
        resp = self.client.get(reverse("cart"))
        self.assertEqual(resp.context["cart_items"], [])
        self.assertFalse(CartItem.objects.exists())


class BuyerCheckoutGetTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.book = make_book(make_seller(), price_cents=1500)
        add_cart_item(self.buyer, self.book)

    def test_checkout_get_selects_default_address_and_payment(self):
        make_address(self.buyer, is_default=False, label="Other")
        default_addr = make_address(self.buyer, is_default=True)
        make_payment_method(self.buyer, is_default=False, last4="1111")
        default_pm = make_payment_method(self.buyer, is_default=True)
        resp = self.client.get(reverse("checkout"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["selected_address"], default_addr)
        self.assertEqual(resp.context["selected_payment"], default_pm)

    def test_checkout_get_without_defaults_selects_first(self):
        addr = make_address(self.buyer, is_default=False)
        pm = make_payment_method(self.buyer, is_default=False)
        resp = self.client.get(reverse("checkout"))
        self.assertEqual(resp.context["selected_address"], addr)
        self.assertEqual(resp.context["selected_payment"], pm)

    def test_checkout_get_query_params_select_specific_address_and_payment(self):
        make_address(self.buyer, is_default=True)
        other = make_address(self.buyer, is_default=False, label="Work")
        make_payment_method(self.buyer, is_default=True)
        other_pm = make_payment_method(self.buyer, is_default=False, last4="9999")
        resp = self.client.get(
            reverse("checkout"),
            {"shipping_address_id": other.id, "payment_method_id": other_pm.id},
        )
        self.assertEqual(resp.context["selected_address"], other)
        self.assertEqual(resp.context["selected_payment"], other_pm)

    def test_checkout_get_no_saved_details_selects_nothing(self):
        resp = self.client.get(reverse("checkout"))
        self.assertIsNone(resp.context["selected_address"])
        self.assertIsNone(resp.context["selected_payment"])

    def test_checkout_get_default_steward_contribution_is_two_dollars(self):
        resp = self.client.get(reverse("checkout"))
        self.assertEqual(resp.context["steward_contribution_value"], "2.00")
        self.assertEqual(resp.context["steward_points_preview"], 20)
        # 1500 + 105 tax + 299 fee + 200 steward
        self.assertEqual(resp.context["final_total"], 21.04)

    def test_checkout_get_steward_contribution_over_max_is_capped(self):
        resp = self.client.get(reverse("checkout"), {"steward_contribution": "150"})
        self.assertEqual(resp.context["steward_contribution_value"], "100.00")
        self.assertEqual(resp.context["steward_points_preview"], 1000)

    def test_checkout_get_invalid_steward_contribution_flagged(self):
        resp = self.client.get(reverse("checkout"), {"steward_contribution": "abc"})
        self.assertTrue(resp.context["steward_contribution_invalid"])
        self.assertEqual(resp.context["steward_points_preview"], 0)


class BuyerCheckoutPostTests(PassItOnTestCase):
    def setUp(self):
        ensure_pool()
        self.buyer = self.login(make_buyer())
        self.seller = make_seller()
        self.book = make_book(self.seller, price_cents=1500, stock=5)
        self.addr = make_address(self.buyer)
        self.pm = make_payment_method(self.buyer)

    def _post(self, **overrides):
        data = {
            "shipping_address_id": self.addr.id,
            "payment_method_id": self.pm.id,
            "steward_contribution": "0",
        }
        data.update(overrides)
        data = {k: v for k, v in data.items() if v is not None}
        return self.client.post(reverse("checkout"), data)

    def assertCheckoutError(self, resp, fragment):
        self.assertEqual(resp.status_code, 200)
        self.assertIn(fragment, resp.context["checkout_error"])
        self.assertFalse(Order.objects.exists())

    def test_checkout_post_empty_cart_shows_error(self):
        self.assertCheckoutError(self._post(), "Your cart is empty.")

    def test_checkout_post_missing_address_shows_error(self):
        add_cart_item(self.buyer, self.book)
        self.assertCheckoutError(self._post(shipping_address_id=None), "Please select a shipping address.")

    def test_checkout_post_foreign_address_shows_invalid_error(self):
        add_cart_item(self.buyer, self.book)
        foreign = make_address(make_buyer())
        self.assertCheckoutError(self._post(shipping_address_id=foreign.id), "Invalid shipping address.")

    def test_checkout_post_non_numeric_address_shows_invalid_error(self):
        add_cart_item(self.buyer, self.book)
        self.assertCheckoutError(self._post(shipping_address_id="abc"), "Invalid shipping address.")

    def test_checkout_post_missing_payment_shows_error(self):
        add_cart_item(self.buyer, self.book)
        self.assertCheckoutError(self._post(payment_method_id=None), "Please select a payment method.")

    def test_checkout_post_foreign_payment_shows_invalid_error(self):
        add_cart_item(self.buyer, self.book)
        foreign = make_payment_method(make_buyer())
        self.assertCheckoutError(self._post(payment_method_id=foreign.id), "Invalid payment method.")

    def test_checkout_post_invalid_steward_contribution_shows_error(self):
        add_cart_item(self.buyer, self.book)
        self.assertCheckoutError(self._post(steward_contribution="-5"), "valid steward contribution")

    # BUG: checkout calls request.user.get_full_name(), which the custom User (AbstractBaseUser) lacks.
    @unittest.expectedFailure
    def test_checkout_post_unlabelled_address_places_order(self):
        """Address label is optional in the add-address form; checkout falls back to the buyer's name."""
        add_cart_item(self.buyer, self.book)
        unlabelled = make_address(self.buyer, label=None)
        resp = self._post(shipping_address_id=unlabelled.id)
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(resp, reverse("order_confirmation", args=[order.id]))

    def test_checkout_post_inactive_book_in_locked_cart_shows_error(self):
        """Covers the in-transaction re-check when a listing goes inactive mid-checkout."""
        ci = add_cart_item(self.buyer, self.book)
        line = {"id": self.book.id, "book": self.book, "quantity": 1, "price": 15.0,
                "subtotal": 15.0, "line_subtotal_cents": 1500, "is_steward_free": False,
                "steward_free_list_price_cents": 0}
        self.book.is_active = False
        self.book.save()
        with mock.patch("app.buyer.views.db_cart_lines", return_value=([line], 1500)):
            resp = self._post()
        self.assertCheckoutError(resp, "is no longer available")
        self.assertTrue(CartItem.objects.filter(pk=ci.pk).exists())

    def test_checkout_post_book_without_inventory_shows_error(self):
        add_cart_item(self.buyer, make_book(self.seller, with_inventory=False))
        self.assertCheckoutError(self._post(), "No inventory record")

    def test_checkout_post_insufficient_stock_shows_error(self):
        add_cart_item(self.buyer, make_book(self.seller, stock=1), quantity=2)
        self.assertCheckoutError(self._post(), "Not enough stock")

    def test_checkout_post_steward_free_quantity_above_one_shows_error(self):
        steward = self.login(make_steward())
        self.addr = make_address(steward)
        self.pm = make_payment_method(steward)
        _free_line(steward, self.book, quantity=2)
        self.assertCheckoutError(self._post(), "Steward free books must be quantity 1.")

    def test_checkout_post_two_steward_free_lines_shows_error(self):
        steward = self.login(make_steward())
        self.addr = make_address(steward)
        self.pm = make_payment_method(steward)
        _free_line(steward, self.book)
        _free_line(steward, make_book(self.seller))
        self.assertCheckoutError(self._post(), "Only one steward free book per order.")

    def test_checkout_post_free_line_during_cooldown_shows_not_eligible(self):
        steward = self.login(make_steward(last_free_book_redeemed_at=timezone.now()))
        self.addr = make_address(steward)
        self.pm = make_payment_method(steward)
        _free_line(steward, self.book)
        self.assertCheckoutError(self._post(), "not eligible for a free book")

    def test_checkout_post_pool_cannot_cover_free_book_shows_error(self):
        ensure_pool(100)
        steward = self.login(make_steward())
        self.addr = make_address(steward)
        self.pm = make_payment_method(steward)
        _free_line(steward, self.book)
        self.assertCheckoutError(self._post(), "steward pool cannot cover")
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, 100)

    def test_checkout_post_success_creates_paid_order_with_correct_totals(self):
        add_cart_item(self.buyer, self.book, quantity=2)
        resp = self._post()
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(resp, reverse("order_confirmation", args=[order.id]), fetch_redirect_response=False)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.subtotal_cents, 3000)
        self.assertEqual(order.tax_cents, 210)
        self.assertEqual(order.fees_cents, 299)
        self.assertEqual(order.total_cents, 3000 + 210 + 299)
        self.assertEqual(order.shipping_address, self.addr)
        self.assertEqual(order.payment_method, self.pm)

    def test_checkout_post_success_decrements_inventory_and_snapshots(self):
        add_cart_item(self.buyer, self.book, quantity=2)
        self._post()
        order = Order.objects.get(user=self.buyer)
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 3)
        oi = OrderItem.objects.get(order=order)
        self.assertEqual((oi.quantity, oi.line_total_cents, oi.title), (2, 3000, self.book.title))
        self.assertTrue(OrderItemBookSnapshot.objects.filter(order_item=oi, title=self.book.title).exists())
        snap = OrderShippingAddress.objects.get(order=order)
        self.assertEqual(snap.shipping_line1, self.addr.line1)
        self.assertEqual(snap.shipping_name, self.addr.label)

    def test_checkout_post_success_clears_cart(self):
        add_cart_item(self.buyer, self.book)
        self._post()
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())

    def test_checkout_post_contribution_adds_points_and_pool(self):
        add_cart_item(self.buyer, self.book)
        pool_before = StewardPool.objects.get(pk=1).pool_cents
        self._post(steward_contribution="5.00")
        order = Order.objects.get(user=self.buyer)
        self.buyer.refresh_from_db()
        self.assertEqual(order.steward_contribution_cents, 500)
        self.assertEqual(order.total_cents, 1500 + 105 + 299 + 500)
        self.assertEqual(self.buyer.steward_progress, 50)
        self.assertFalse(self.buyer.steward_verified)
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, pool_before + 500)

    def test_checkout_post_blank_contribution_uses_two_dollar_default(self):
        add_cart_item(self.buyer, self.book)
        self._post(steward_contribution="")
        self.assertEqual(Order.objects.get().steward_contribution_cents, 200)

    def test_checkout_post_crossing_100_points_verifies_steward_and_redirects_welcome(self):
        self.buyer.steward_progress = 95
        self.buyer.save()
        add_cart_item(self.buyer, self.book)
        resp = self._post(steward_contribution="1.00")
        order = Order.objects.get()
        self.assertRedirects(resp, reverse("steward_welcome"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["steward_milestone_order_id"], order.id)
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.steward_verified)
        self.assertEqual(self.buyer.steward_progress, 100)

    def test_checkout_post_steward_free_line_waives_fee_and_deducts_pool(self):
        ensure_pool(10_000)
        steward = self.login(make_steward())
        self.addr = make_address(steward)
        self.pm = make_payment_method(steward)
        _free_line(steward, self.book)
        resp = self._post(steward_contribution="5")
        order = Order.objects.get(user=steward)
        self.assertRedirects(resp, reverse("order_confirmation", args=[order.id]), fetch_redirect_response=False)
        self.assertEqual((order.fees_cents, order.steward_contribution_cents, order.total_cents), (0, 0, 0))
        self.assertTrue(order.order_items.get().is_steward_free)
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, 10_000 - 1500)
        steward.refresh_from_db()
        self.assertIsNotNone(steward.last_free_book_redeemed_at)


class CartStewardFreeSelectViewTests(PassItOnTestCase):
    def setUp(self):
        ensure_pool()
        self.steward = self.login(make_steward())
        self.book = make_book(price_cents=1500, stock=3)

    def _post(self, book_id=None):
        return self.client.post(reverse("cart_steward_free_select", args=[book_id or self.book.id]))

    def test_select_get_redirects_without_change(self):
        ci = add_cart_item(self.steward, self.book)
        resp = self.client.get(reverse("cart_steward_free_select", args=[self.book.id]))
        self.assertRedirects(resp, reverse("cart"), fetch_redirect_response=False)
        ci.refresh_from_db()
        self.assertFalse(ci.is_steward_free)

    def test_select_non_steward_rejected(self):
        buyer = self.login(make_buyer())
        add_cart_item(buyer, self.book)
        self.assertMessage(self._post(), "Only verified stewards")

    def test_select_during_cooldown_rejected(self):
        self.steward.last_free_book_redeemed_at = timezone.now() - timedelta(days=1)
        self.steward.save()
        add_cart_item(self.steward, self.book)
        self.assertMessage(self._post(), "30-day cooldown")

    def test_select_book_not_in_cart_rejected(self):
        self.assertMessage(self._post(), "not in your cart")

    def test_select_inactive_book_rejected(self):
        add_cart_item(self.steward, self.book)
        self.book.is_active = False
        self.book.save()
        self.assertMessage(self._post(), "no longer available")

    def test_select_quantity_above_one_rejected(self):
        add_cart_item(self.steward, self.book, quantity=2)
        self.assertMessage(self._post(), "Set quantity to 1")

    def test_select_out_of_stock_rejected(self):
        book = make_book(stock=0)
        add_cart_item(self.steward, book)
        self.assertMessage(self._post(book.id), "out of stock")

    def test_select_pool_below_price_rejected(self):
        ensure_pool(100)
        add_cart_item(self.steward, self.book)
        self.assertMessage(self._post(), "pool doesn")

    def test_select_success_marks_line_free_and_zeroes_price(self):
        ci = add_cart_item(self.steward, self.book)
        resp = self._post()
        self.assertMessage(resp, "steward free book")
        ci.refresh_from_db()
        self.assertTrue(ci.is_steward_free)
        self.assertEqual(ci.unit_price_cents, 0)
        self.assertEqual(ci.steward_free_list_price_cents, 1500)

    def test_select_success_reverts_previous_free_line_to_paid(self):
        other = make_book(price_cents=900)
        previous = _free_line(self.steward, other)
        add_cart_item(self.steward, self.book)
        self._post()
        previous.refresh_from_db()
        self.assertFalse(previous.is_steward_free)
        self.assertEqual(previous.unit_price_cents, 900)
        self.assertEqual(CartItem.objects.filter(is_steward_free=True).get().book, self.book)


class CartStewardFreeDeselectViewTests(PassItOnTestCase):
    def setUp(self):
        self.steward = self.login(make_steward())
        self.book = make_book(price_cents=1500)

    def test_deselect_get_redirects_without_change(self):
        ci = _free_line(self.steward, self.book)
        self.client.get(reverse("cart_steward_free_deselect", args=[self.book.id]))
        ci.refresh_from_db()
        self.assertTrue(ci.is_steward_free)

    def test_deselect_no_free_line_redirects_quietly(self):
        add_cart_item(self.steward, self.book)
        resp = self.client.post(reverse("cart_steward_free_deselect", args=[self.book.id]))
        self.assertRedirects(resp, reverse("cart"), fetch_redirect_response=False)
        self.assertEqual(len(list(resp.wsgi_request._messages)), 0)

    def test_deselect_success_restores_list_price(self):
        ci = _free_line(self.steward, self.book)
        resp = self.client.post(reverse("cart_steward_free_deselect", args=[self.book.id]))
        self.assertMessage(resp, "Steward free book cleared")
        ci.refresh_from_db()
        self.assertFalse(ci.is_steward_free)
        self.assertEqual(ci.unit_price_cents, 1500)
        self.assertEqual(ci.steward_free_list_price_cents, 0)


class StewardWelcomeViewTests(PassItOnTestCase):
    def _set_session(self, **values):
        session = self.client.session
        session.update(values)
        session.save()

    def test_welcome_non_steward_redirects_to_catalog(self):
        self.login(make_buyer())
        resp = self.client.get(reverse("steward_welcome"))
        self.assertRedirects(resp, reverse("catalog"), fetch_redirect_response=False)

    def test_welcome_get_prefills_city(self):
        self.login(make_steward(steward_city="Denver"))
        resp = self.client.get(reverse("steward_welcome"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["steward_city_value"], "Denver")
        self.assertIsNone(resp.context["pending_order_id"])

    def test_welcome_stale_session_order_id_is_dropped(self):
        self.login(make_steward())
        self._set_session(steward_milestone_order_id=999_999)
        resp = self.client.get(reverse("steward_welcome"))
        self.assertIsNone(resp.context["pending_order_id"])
        self.assertNotIn("steward_milestone_order_id", self.client.session)

    def test_welcome_post_blank_city_shows_error(self):
        self.login(make_steward())
        resp = self.client.post(reverse("steward_welcome"), {"steward_city": "  "})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("City is required", resp.context["city_error"])

    def test_welcome_post_city_with_pending_order_redirects_to_confirmation(self):
        steward = self.login(make_steward(steward_city=None))
        order = make_order(steward, [(make_book(), 1)])
        self._set_session(steward_milestone_order_id=order.id)
        resp = self.client.post(reverse("steward_welcome"), {"steward_city": "Boston"})
        self.assertRedirects(resp, reverse("order_confirmation", args=[order.id]), fetch_redirect_response=False)
        steward.refresh_from_db()
        self.assertEqual(steward.steward_city, "Boston")
        self.assertNotIn("steward_milestone_order_id", self.client.session)

    def test_welcome_post_city_without_pending_order_redirects_to_dashboard(self):
        self.login(make_steward())
        resp = self.client.post(reverse("steward_welcome"), {"steward_city": "Boston"})
        self.assertRedirects(resp, reverse("buyer_dashboard"), fetch_redirect_response=False)


class RemoveCartItemViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.book = make_book()

    def _post(self):
        return self.client.post(reverse("remove_cart_item", args=[self.book.id]))

    def test_remove_quantity_above_one_decrements(self):
        ci = add_cart_item(self.buyer, self.book, quantity=3)
        self._post()
        ci.refresh_from_db()
        self.assertEqual(ci.quantity, 2)

    def test_remove_quantity_one_deletes_line(self):
        add_cart_item(self.buyer, self.book, quantity=1)
        resp = self._post()
        self.assertRedirects(resp, reverse("cart"), fetch_redirect_response=False)
        self.assertFalse(CartItem.objects.exists())

    def test_remove_missing_line_is_noop(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302)

    def test_remove_get_does_not_change_cart(self):
        add_cart_item(self.buyer, self.book, quantity=1)
        self.client.get(reverse("remove_cart_item", args=[self.book.id]))
        self.assertTrue(CartItem.objects.exists())

    def test_remove_other_users_line_untouched(self):
        other = make_buyer()
        add_cart_item(other, self.book, quantity=1)
        self._post()
        self.assertTrue(CartItem.objects.filter(cart__user=other).exists())


class UpdateCartItemViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.book = make_book(price_cents=800)

    def _post(self, **data):
        return self.client.post(reverse("update_cart_item", args=[self.book.id]), data)

    def test_update_sets_quantity_and_refreshes_price(self):
        ci = add_cart_item(self.buyer, self.book, quantity=1, unit_price_cents=500)
        self._post(quantity="4")
        ci.refresh_from_db()
        self.assertEqual((ci.quantity, ci.unit_price_cents), (4, 800))

    def test_update_invalid_quantity_defaults_to_one(self):
        ci = add_cart_item(self.buyer, self.book, quantity=3)
        self._post(quantity="x")
        ci.refresh_from_db()
        self.assertEqual(ci.quantity, 1)

    def test_update_zero_quantity_clamped_to_one(self):
        ci = add_cart_item(self.buyer, self.book, quantity=3)
        self._post(quantity="0")
        ci.refresh_from_db()
        self.assertEqual(ci.quantity, 1)

    def test_update_steward_free_line_forced_to_quantity_one(self):
        steward = self.login(make_steward())
        ci = _free_line(steward, self.book)
        self._post(quantity="5")
        ci.refresh_from_db()
        self.assertEqual((ci.quantity, ci.unit_price_cents), (1, 0))

    def test_update_get_is_noop(self):
        ci = add_cart_item(self.buyer, self.book, quantity=2)
        resp = self.client.get(reverse("update_cart_item", args=[self.book.id]), {"quantity": 9})
        self.assertRedirects(resp, reverse("cart"), fetch_redirect_response=False)
        ci.refresh_from_db()
        self.assertEqual(ci.quantity, 2)


class BuyerPaymentsViewTests(PassItOnTestCase):
    def test_payments_lists_only_own_methods(self):
        buyer = self.login(make_buyer())
        mine = make_payment_method(buyer)
        make_payment_method(make_buyer())
        resp = self.client.get(reverse("buyer_payments"))
        self.assertEqual(resp.context["payment_methods"], [mine])


class BuyerAddPaymentMethodViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def _data(self, **overrides):
        data = {
            "cardholder_name": "Pat Buyer",
            "card_number": "4242 4242 4242 4242",
            "brand": "Visa",
            "cvv": "123",
            "exp_month": "12",
            "exp_year": "2099",
        }
        data.update(overrides)
        return data

    def _assert_rejected(self, fragment, **overrides):
        resp = self.client.post(reverse("buyer_add_payment_method"), self._data(**overrides))
        self.assertEqual(resp.status_code, 200)
        self.assertMessage(resp, fragment)
        self.assertFalse(PaymentMethod.objects.exists())

    def test_add_payment_get_renders_form_with_next(self):
        resp = self.client.get(reverse("buyer_add_payment_method"), {"next": "/buyer/checkout/"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["next"], "/buyer/checkout/")

    def test_add_payment_missing_cardholder_rejected(self):
        self._assert_rejected("Cardholder name is required.", cardholder_name="")

    def test_add_payment_missing_brand_rejected(self):
        self._assert_rejected("Brand is required.", brand="")

    def test_add_payment_short_cvv_rejected(self):
        self._assert_rejected("CVV is required", cvv="1")

    def test_add_payment_short_card_number_rejected(self):
        self._assert_rejected("valid card number", card_number="1234 5678")

    def test_add_payment_month_out_of_range_rejected(self):
        self._assert_rejected("expiration month", exp_month="13")

    def test_add_payment_non_numeric_month_rejected(self):
        self._assert_rejected("expiration month", exp_month="Dec")

    def test_add_payment_year_out_of_range_rejected(self):
        self._assert_rejected("4-digit expiration year", exp_year="1999")

    def test_add_payment_expired_card_rejected(self):
        self._assert_rejected("expired", exp_year=str(date.today().year - 1))

    def test_add_payment_success_stores_only_last4(self):
        resp = self.client.post(reverse("buyer_add_payment_method"), self._data())
        self.assertRedirects(resp, reverse("buyer_payments"), fetch_redirect_response=False)
        pm = PaymentMethod.objects.get(user=self.buyer)
        self.assertEqual(pm.last4, "4242")
        self.assertFalse(pm.is_default)
        self.assertFalse(hasattr(pm, "card_number"))

    def test_add_payment_default_unsets_previous_default(self):
        old = make_payment_method(self.buyer, is_default=True)
        self.client.post(reverse("buyer_add_payment_method"), self._data(is_default="on"))
        old.refresh_from_db()
        self.assertFalse(old.is_default)
        self.assertTrue(PaymentMethod.objects.get(user=self.buyer, last4="4242", is_default=True))

    def test_add_payment_relative_next_redirects_there(self):
        resp = self.client.post(reverse("buyer_add_payment_method"), self._data(next="/buyer/checkout/"))
        self.assertRedirects(resp, "/buyer/checkout/", fetch_redirect_response=False)

    def test_add_payment_absolute_next_ignored(self):
        """Open-redirect guard: only '/'-prefixed next URLs are honoured."""
        resp = self.client.post(reverse("buyer_add_payment_method"), self._data(next="https://evil.example/"))
        self.assertRedirects(resp, reverse("buyer_payments"), fetch_redirect_response=False)


class SetDefaultPaymentMethodViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def test_set_default_payment_get_is_noop(self):
        pm = make_payment_method(self.buyer, is_default=False)
        resp = self.client.get(reverse("set_default_payment_method", args=[pm.id]))
        self.assertRedirects(resp, reverse("buyer_payments"), fetch_redirect_response=False)
        pm.refresh_from_db()
        self.assertFalse(pm.is_default)

    def test_set_default_payment_foreign_returns_404(self):
        pm = make_payment_method(make_buyer())
        self.assertEqual(self.client.post(reverse("set_default_payment_method", args=[pm.id])).status_code, 404)

    def test_set_default_payment_success_moves_default(self):
        old = make_payment_method(self.buyer, is_default=True)
        new = make_payment_method(self.buyer, is_default=False)
        self.client.post(reverse("set_default_payment_method", args=[new.id]))
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertFalse(old.is_default)
        self.assertTrue(new.is_default)


class DeletePaymentMethodViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def test_delete_payment_get_is_noop(self):
        pm = make_payment_method(self.buyer)
        self.client.get(reverse("delete_payment_method", args=[pm.id]))
        self.assertTrue(PaymentMethod.objects.filter(pk=pm.pk).exists())

    def test_delete_payment_foreign_returns_404(self):
        pm = make_payment_method(make_buyer())
        self.assertEqual(self.client.post(reverse("delete_payment_method", args=[pm.id])).status_code, 404)
        self.assertTrue(PaymentMethod.objects.filter(pk=pm.pk).exists())

    def test_delete_payment_success_removes_row(self):
        pm = make_payment_method(self.buyer)
        resp = self.client.post(reverse("delete_payment_method", args=[pm.id]))
        self.assertMessage(resp, "Payment method removed.")
        self.assertFalse(PaymentMethod.objects.filter(pk=pm.pk).exists())


class BuyerShippingViewTests(PassItOnTestCase):
    def test_shipping_lists_only_own_addresses(self):
        buyer = self.login(make_buyer())
        mine = make_address(buyer)
        make_address(make_buyer())
        resp = self.client.get(reverse("buyer_shipping"))
        self.assertEqual(list(resp.context["addresses"]), [mine])


class BuyerAddShippingAddressViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def _data(self, **overrides):
        data = {"label": "Dorm", "line1": "9 College Ave", "line2": "", "city": "Ithaca",
                "state": "NY", "postal_code": "14850", "country": "US"}
        data.update(overrides)
        return data

    def test_add_address_get_renders_form(self):
        resp = self.client.get(reverse("buyer_add_shipping_address"))
        self.assertEqual(resp.status_code, 200)

    def test_add_address_missing_required_field_rejected(self):
        resp = self.client.post(reverse("buyer_add_shipping_address"), self._data(city=""))
        self.assertEqual(resp.status_code, 200)
        self.assertMessage(resp, "are required")
        self.assertFalse(Address.objects.exists())

    def test_add_address_success_blank_optionals_stored_as_none(self):
        resp = self.client.post(reverse("buyer_add_shipping_address"), self._data(label=""))
        self.assertRedirects(resp, reverse("buyer_shipping"), fetch_redirect_response=False)
        addr = Address.objects.get(user=self.buyer)
        self.assertIsNone(addr.label)
        self.assertIsNone(addr.line2)
        self.assertFalse(addr.is_default)

    def test_add_address_default_unsets_previous_default(self):
        old = make_address(self.buyer, is_default=True)
        self.client.post(reverse("buyer_add_shipping_address"), self._data(is_default="on"))
        old.refresh_from_db()
        self.assertFalse(old.is_default)
        self.assertTrue(Address.objects.get(line1="9 College Ave").is_default)

    def test_add_address_relative_next_redirects_there(self):
        resp = self.client.post(reverse("buyer_add_shipping_address"), self._data(next="/buyer/checkout/"))
        self.assertRedirects(resp, "/buyer/checkout/", fetch_redirect_response=False)


class SetDefaultShippingAddressViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def test_set_default_address_get_is_noop(self):
        addr = make_address(self.buyer, is_default=False)
        self.client.get(reverse("set_default_address", args=[addr.id]))
        addr.refresh_from_db()
        self.assertFalse(addr.is_default)

    def test_set_default_address_foreign_returns_404(self):
        addr = make_address(make_buyer())
        self.assertEqual(self.client.post(reverse("set_default_address", args=[addr.id])).status_code, 404)

    def test_set_default_address_success_moves_default(self):
        old = make_address(self.buyer, is_default=True)
        new = make_address(self.buyer, is_default=False)
        resp = self.client.post(reverse("set_default_address", args=[new.id]))
        self.assertRedirects(resp, reverse("buyer_shipping"), fetch_redirect_response=False)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertFalse(old.is_default)
        self.assertTrue(new.is_default)


class DeleteShippingAddressViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())

    def test_delete_address_get_is_noop(self):
        addr = make_address(self.buyer)
        self.client.get(reverse("delete_address", args=[addr.id]))
        self.assertTrue(Address.objects.filter(pk=addr.pk).exists())

    def test_delete_address_foreign_returns_404(self):
        addr = make_address(make_buyer())
        self.assertEqual(self.client.post(reverse("delete_address", args=[addr.id])).status_code, 404)

    def test_delete_address_success_removes_row(self):
        addr = make_address(self.buyer)
        self.client.post(reverse("delete_address", args=[addr.id]))
        self.assertFalse(Address.objects.filter(pk=addr.pk).exists())


class BuyerProfileViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer(store_credit_cents=550))

    def _post(self, **data):
        return self.client.post(reverse("buyer_profile"), data)

    def test_profile_get_shows_store_credit_and_security_form(self):
        resp = self.client.get(reverse("buyer_profile"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["store_credit_display"], "5.50")
        self.assertIn("security_form", resp.context)

    def test_profile_update_missing_names_shows_error(self):
        resp = self._post(action="profile", first_name="", last_name="")
        self.assertIn("First name is required.", resp.context["profile_error"])
        self.assertIn("Last name is required.", resp.context["profile_error"])

    def test_profile_update_success_saves_fields(self):
        resp = self._post(action="profile", first_name="Ada", last_name="Lovelace",
                          phone="555-0100", steward_city="")
        self.assertRedirects(resp, reverse("buyer_profile"), fetch_redirect_response=False)
        self.buyer.refresh_from_db()
        self.assertEqual((self.buyer.first_name, self.buyer.last_name, self.buyer.phone), ("Ada", "Lovelace", "555-0100"))
        self.assertIsNone(self.buyer.steward_city)

    def test_profile_password_wrong_current_and_mismatch_shows_errors(self):
        resp = self._post(action="password", current_password="nope", new_password="short", confirm_password="x")
        err = resp.context["password_error"]
        self.assertIn("Current password is incorrect.", err)
        self.assertIn("at least 8 characters", err)
        self.assertIn("do not match", err)

    def test_profile_password_success_changes_password_and_keeps_session(self):
        resp = self._post(action="password", current_password=PASSWORD,
                          new_password="Brand-new-pass1", confirm_password="Brand-new-pass1")
        self.assertRedirects(resp, reverse("buyer_profile"), fetch_redirect_response=False)
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.check_password("Brand-new-pass1"))
        self.assertEqual(self.client.get(reverse("buyer_profile")).status_code, 200)

    def test_profile_security_valid_saves_hashed_answer(self):
        resp = self._post(action="security", security_question="In what city were you born?",
                          security_answer=" Paris ")
        self.assertRedirects(resp, reverse("buyer_profile"), fetch_redirect_response=False)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.security_question, "In what city were you born?")
        self.assertTrue(check_password("paris", self.buyer.security_answer_hash))

    def test_profile_security_invalid_question_shows_error(self):
        resp = self._post(action="security", security_question="Bogus?", security_answer="x")
        self.assertEqual(resp.context["security_error"], "Please correct the errors below.")
        self.assertTrue(resp.context["security_form"].errors)

    def test_profile_unknown_action_renders_page(self):
        self.assertEqual(self._post(action="other").status_code, 200)


class OrderConfirmationViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.book = make_book(make_seller())

    def test_confirmation_other_users_order_returns_404(self):
        order = make_order(make_buyer(), [(self.book, 1)])
        self.assertEqual(self.client.get(reverse("order_confirmation", args=[order.id])).status_code, 404)

    def test_confirmation_shows_items_totals_and_steward_points(self):
        order = make_order(self.buyer, [(self.book, 2)], steward_contribution_cents=300, total_cents=3300)
        resp = self.client.get(reverse("order_confirmation", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["steward_points_earned"], 30)
        self.assertEqual(resp.context["steward_contribution_display"], "3.00")
        self.assertEqual(resp.context["order_total_display"], "33.00")
        self.assertEqual(resp.context["order_items"][0]["title"], self.book.title)
        self.assertIsNone(resp.context["free_book_attribution"])

    def test_confirmation_free_book_order_credits_another_steward(self):
        make_steward(first_name="Grace", steward_city="Reno")
        order = make_order(self.buyer, [(self.book, 1)])
        OrderItem.objects.filter(order=order).update(is_steward_free=True)
        resp = self.client.get(reverse("order_confirmation", args=[order.id]))
        self.assertEqual(resp.context["free_book_attribution"], {"first_name": "Grace", "city": "Reno"})


class OrderDetailViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.book = make_book(make_seller())

    def _ctx(self, order):
        resp = self.client.get(reverse("order_detail", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        return resp.context

    def test_detail_other_users_order_returns_404(self):
        order = make_order(make_buyer(), [(self.book, 1)])
        self.assertEqual(self.client.get(reverse("order_detail", args=[order.id])).status_code, 404)

    def test_detail_return_allowed_for_paid_shipped_delivered(self):
        for status in ("paid", "shipped", "delivered"):
            with self.subTest(status=status):
                self.assertTrue(self._ctx(make_order(self.buyer, [(self.book, 1)], status=status))["can_request_return"])

    def test_detail_return_not_allowed_for_pending_or_cancelled(self):
        for status in ("pending", "cancelled"):
            with self.subTest(status=status):
                self.assertFalse(self._ctx(make_order(self.buyer, [(self.book, 1)], status=status))["can_request_return"])

    def test_detail_review_only_allowed_when_delivered(self):
        self.assertTrue(self._ctx(make_order(self.buyer, [(self.book, 1)], status="delivered"))["can_leave_review"])
        self.assertFalse(self._ctx(make_order(self.buyer, [(self.book, 1)], status="shipped"))["can_leave_review"])

    def test_detail_existing_review_rating_exposed(self):
        order = make_order(self.buyer, [(self.book, 1)], status="delivered")
        make_review(self.buyer, order.order_items.get(), rating=3)
        item = self._ctx(order)["order_items"][0]
        self.assertEqual(item["saved_rating"], 3)
        self.assertIsNotNone(item["existing_review"])

    def test_detail_return_status_labels(self):
        cases = {"requested": "Return requested", "refunded": "Return complete", "rejected": "Return declined"}
        for status, label in cases.items():
            with self.subTest(status=status):
                order = make_order(self.buyer, [(self.book, 1)])
                make_return(order, status=status)
                ctx = self._ctx(order)
                self.assertEqual(ctx["return_status_label"], label)
                self.assertFalse(ctx["can_request_return"])

    def test_detail_display_order_number_counts_from_oldest(self):
        first = make_order(self.buyer, [(self.book, 1)])
        second = make_order(self.buyer, [(self.book, 1)])
        self.assertEqual(self._ctx(first)["display_order_number"], 1)
        self.assertEqual(self._ctx(second)["display_order_number"], 2)

    def test_detail_items_fall_back_to_order_item_title_without_snapshot(self):
        order = make_order(self.buyer, [(self.book, 1)], with_snapshots=False)
        self.assertEqual(self._ctx(order)["order_items"][0]["title"], self.book.title)
        self.assertIsNone(self._ctx(order)["shipping"])


class OrderHistoryViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        book = make_book()
        self.paid = make_order(self.buyer, [(book, 1)], status="paid", with_snapshots=False)
        self.delivered = make_order(self.buyer, [(book, 1)], status="delivered", with_snapshots=False)
        make_order(make_buyer(), [(book, 1)], with_snapshots=False)

    def _orders(self, **params):
        return self.client.get(reverse("order_history"), params).context["orders"]

    def test_history_lists_only_own_orders_newest_first(self):
        self.assertEqual(self._orders(), [self.delivered, self.paid])

    def test_history_status_filter(self):
        self.assertEqual(self._orders(status="paid"), [self.paid])

    def test_history_from_date_in_future_excludes_all(self):
        tomorrow = (timezone.now() + timedelta(days=1)).date().isoformat()
        self.assertEqual(self._orders(**{"from": tomorrow}), [])

    def test_history_to_date_in_past_excludes_all(self):
        yesterday = (timezone.now() - timedelta(days=1)).date().isoformat()
        self.assertEqual(self._orders(to=yesterday), [])

    def test_history_date_range_including_today_keeps_orders(self):
        today = timezone.now().date().isoformat()
        self.assertEqual(len(self._orders(**{"from": today, "to": today})), 2)

    def test_history_invalid_dates_are_ignored(self):
        self.assertEqual(len(self._orders(**{"from": "not-a-date", "to": "31/12/2020"})), 2)


class ReturnRequestViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.order = make_order(self.buyer, [(make_book(make_seller()), 1)], status="delivered")
        self.url = reverse("return_request", args=[self.order.id])

    def test_return_get_renders_form(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("return_error", resp.context)

    def test_return_other_users_order_returns_404(self):
        other = make_order(make_buyer(), [(make_book(), 1)])
        self.assertEqual(self.client.get(reverse("return_request", args=[other.id])).status_code, 404)

    def test_return_blank_reason_shows_error(self):
        resp = self.client.post(self.url, {"reason_category": "", "reason_details": "  "})
        self.assertEqual(resp.context["return_error"], "Please describe your return reason.")
        self.assertFalse(ReturnRequest.objects.exists())

    def test_return_success_combines_category_and_details(self):
        resp = self.client.post(self.url, {"reason_category": "damaged", "reason_details": "Torn cover"})
        self.assertRedirects(resp, reverse("order_detail", args=[self.order.id]), fetch_redirect_response=False)
        rr = ReturnRequest.objects.get(order=self.order)
        self.assertEqual(rr.reason, "[damaged]\nTorn cover")
        self.assertEqual(rr.status, "requested")

    def test_return_category_only_is_accepted(self):
        self.client.post(self.url, {"reason_category": "wrong_item"})
        self.assertEqual(ReturnRequest.objects.get().reason, "[wrong_item]")

    def test_return_duplicate_request_blocked(self):
        make_return(self.order)
        resp = self.client.post(self.url, {"reason_details": "again"})
        self.assertIn("already been submitted", resp.context["return_error"])
        self.assertEqual(ReturnRequest.objects.count(), 1)

    # BUG: return_request_view never checks order status, so cancelled orders can be "returned".
    @unittest.expectedFailure
    def test_return_cancelled_order_is_refused(self):
        cancelled = make_order(self.buyer, [(make_book(), 1)], status="cancelled")
        self.client.post(reverse("return_request", args=[cancelled.id]), {"reason_details": "never arrived"})
        self.assertFalse(ReturnRequest.objects.filter(order=cancelled).exists())


class ReviewSubmissionViewTests(PassItOnTestCase):
    def setUp(self):
        self.buyer = self.login(make_buyer())
        self.order = make_order(self.buyer, [(make_book(make_seller()), 1)], status="delivered")
        self.item = self.order.order_items.get()
        self.url = reverse("review_submission", args=[self.item.id])

    def test_review_other_users_item_returns_404(self):
        other_item = make_order(make_buyer(), [(make_book(), 1)]).order_items.get()
        resp = self.client.post(reverse("review_submission", args=[other_item.id]), {"rating": "5"})
        self.assertEqual(resp.status_code, 404)

    def test_review_out_of_range_rating_ignored(self):
        for rating in ("0", "6"):
            with self.subTest(rating=rating):
                resp = self.client.post(self.url, {"rating": rating})
                self.assertRedirects(resp, reverse("order_detail", args=[self.order.id]), fetch_redirect_response=False)
        self.assertFalse(Review.objects.exists())

    def test_review_missing_rating_ignored(self):
        self.client.post(self.url, {})
        self.assertFalse(Review.objects.exists())

    def test_review_valid_rating_creates_review(self):
        resp = self.client.post(self.url, {"rating": "5"})
        self.assertRedirects(resp, reverse("order_detail", args=[self.order.id]), fetch_redirect_response=False)
        review = Review.objects.get()
        self.assertEqual((review.user, review.book, review.order_item, review.rating),
                         (self.buyer, self.item.book, self.item, 5))

    def test_review_resubmission_updates_existing(self):
        self.client.post(self.url, {"rating": "5"})
        self.client.post(self.url, {"rating": "2"})
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(Review.objects.get().rating, 2)

    # BUG: int(request.POST["rating"]) is unguarded; a non-numeric rating raises ValueError (HTTP 500).
    @unittest.expectedFailure
    def test_review_non_numeric_rating_redirects_without_review(self):
        resp = self.client.post(self.url, {"rating": "five"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Review.objects.exists())

    # BUG: review_submission does not check order status; reviews can be left before delivery.
    @unittest.expectedFailure
    def test_review_undelivered_order_is_refused(self):
        item = make_order(self.buyer, [(make_book(), 1)], status="paid").order_items.get()
        self.client.post(reverse("review_submission", args=[item.id]), {"rating": "4"})
        self.assertFalse(Review.objects.filter(order_item=item).exists())
