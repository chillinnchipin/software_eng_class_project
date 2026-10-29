"""Unit tests for helper modules and private view helpers.

Covers General/steward.py, Buyer/cart_helpers.py, Seller/webhook_notify.py,
General/middleware.py, the create_admin management command, and the private
helper functions inside each app's views module.
"""

import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

import requests
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from Admin import views as admin_views
from Buyer import cart_helpers
from Buyer import views as buyer_views
from Buyer.models import Cart, CartItem, SellerReturnReceipt
from General import steward
from General import views as general_views
from General.middleware import NoCacheMiddleware
from General.models import User
from Seller import views as seller_views
from Seller import webhook_notify
from Seller.models import SellerProfile
from tests.helpers import (
    add_cart_item,
    ensure_pool,
    make_address,
    make_admin,
    make_book,
    make_buyer,
    make_flag,
    make_order,
    make_payment_method,
    make_return,
    make_seller,
    make_steward,
)


class FakeSession(dict):
    """Dict that also accepts the ``modified`` attribute like a real session."""

    modified = False


def _line(book, *, quantity=1, is_steward_free=False):
    return {"id": book.id, "book": book, "quantity": quantity, "is_steward_free": is_steward_free}


# --------------------------------------------------------------------------- General/steward.py


class GetStewardPoolTests(TestCase):
    def test_get_steward_pool_existing_row_returned(self):
        ensure_pool(5_000)
        self.assertEqual(steward.get_steward_pool().pool_cents, 5_000)

    def test_get_steward_pool_missing_row_created_with_initial_balance(self):
        from General.models import StewardPool

        StewardPool.objects.all().delete()
        pool = steward.get_steward_pool()
        self.assertEqual(pool.pk, 1)
        self.assertEqual(pool.pool_cents, steward.INITIAL_POOL_CENTS)


class FreeBookEligibilityTests(TestCase):
    def test_user_free_book_eligible_anonymous_false(self):
        self.assertFalse(steward.user_free_book_eligible(AnonymousUser()))

    def test_user_free_book_eligible_non_steward_false(self):
        self.assertFalse(steward.user_free_book_eligible(make_buyer()))

    def test_user_free_book_eligible_never_redeemed_true(self):
        self.assertTrue(steward.user_free_book_eligible(make_steward()))

    def test_user_free_book_eligible_within_cooldown_false(self):
        user = make_steward(last_free_book_redeemed_at=timezone.now() - timedelta(days=5))
        self.assertFalse(steward.user_free_book_eligible(user))

    def test_user_free_book_eligible_after_cooldown_true(self):
        user = make_steward(last_free_book_redeemed_at=timezone.now() - timedelta(days=31))
        self.assertTrue(steward.user_free_book_eligible(user))


class CooldownProgressTests(TestCase):
    def test_cooldown_progress_anonymous_zero(self):
        self.assertEqual(steward.free_book_cooldown_progress_percent(AnonymousUser()), 0)

    def test_cooldown_progress_non_steward_zero(self):
        self.assertEqual(steward.free_book_cooldown_progress_percent(make_buyer()), 0)

    def test_cooldown_progress_never_redeemed_hundred(self):
        self.assertEqual(steward.free_book_cooldown_progress_percent(make_steward()), 100)

    def test_cooldown_progress_halfway_about_fifty(self):
        user = make_steward(last_free_book_redeemed_at=timezone.now() - timedelta(days=15))
        self.assertIn(steward.free_book_cooldown_progress_percent(user), (49, 50))

    def test_cooldown_progress_expired_hundred(self):
        user = make_steward(last_free_book_redeemed_at=timezone.now() - timedelta(days=40))
        self.assertEqual(steward.free_book_cooldown_progress_percent(user), 100)

    def test_cooldown_progress_future_timestamp_clamped_to_zero(self):
        """A redemption time in the future (clock skew) must not produce a negative percent."""
        user = make_steward(last_free_book_redeemed_at=timezone.now() + timedelta(days=1))
        self.assertEqual(steward.free_book_cooldown_progress_percent(user), 0)


class NextFreeBookEligibleAtTests(TestCase):
    def test_next_free_book_eligible_at_non_steward_none(self):
        self.assertIsNone(steward.next_free_book_eligible_at(make_buyer()))
        self.assertIsNone(steward.next_free_book_eligible_at(AnonymousUser()))

    def test_next_free_book_eligible_at_never_redeemed_none(self):
        self.assertIsNone(steward.next_free_book_eligible_at(make_steward()))

    def test_next_free_book_eligible_at_in_cooldown_returns_end(self):
        redeemed = timezone.now() - timedelta(days=2)
        user = make_steward(last_free_book_redeemed_at=redeemed)
        self.assertEqual(steward.next_free_book_eligible_at(user), redeemed + steward.FREE_BOOK_COOLDOWN)

    def test_next_free_book_eligible_at_cooldown_over_none(self):
        user = make_steward(last_free_book_redeemed_at=timezone.now() - timedelta(days=60))
        self.assertIsNone(steward.next_free_book_eligible_at(user))


class RandomStewardAttributionTests(TestCase):
    def test_random_steward_attribution_no_stewards_none(self):
        make_buyer()
        self.assertIsNone(steward.random_steward_attribution())

    def test_random_steward_attribution_returns_first_name_and_city(self):
        make_steward(first_name="Rosa", steward_city="Tulsa")
        self.assertEqual(steward.random_steward_attribution(), {"first_name": "Rosa", "city": "Tulsa"})

    def test_random_steward_attribution_excludes_given_user_when_others_exist(self):
        me = make_steward(first_name="Me")
        make_steward(first_name="Other")
        for _ in range(10):
            self.assertEqual(steward.random_steward_attribution(exclude_user_id=me.pk)["first_name"], "Other")

    def test_random_steward_attribution_only_excluded_user_falls_back_to_them(self):
        me = make_steward(first_name="Solo")
        self.assertEqual(steward.random_steward_attribution(exclude_user_id=me.pk)["first_name"], "Solo")

    def test_random_steward_attribution_blank_first_name_none(self):
        make_steward(first_name="  ")
        self.assertIsNone(steward.random_steward_attribution())

    def test_random_steward_attribution_blank_city_returns_none_city(self):
        make_steward(first_name="Kai", steward_city="")
        self.assertEqual(steward.random_steward_attribution(), {"first_name": "Kai", "city": None})

    def test_random_steward_attribution_inactive_stewards_ignored(self):
        make_steward(is_active=False)
        self.assertIsNone(steward.random_steward_attribution())

    def test_random_steward_attribution_row_vanished_none(self):
        """If the chosen id no longer exists (race), the helper returns None."""
        make_steward()
        with mock.patch("General.steward.random.choice", return_value=10**9):
            self.assertIsNone(steward.random_steward_attribution())


class CartStewardPrivilegeRowTests(TestCase):
    def setUp(self):
        self.book = make_book(price_cents=1000, stock=3)
        self.steward = make_steward()

    def test_privilege_row_non_steward_hidden(self):
        row = steward.cart_steward_privilege_row(make_buyer(), _line(self.book), None, 10**6)
        self.assertFalse(row["show"])
        self.assertTrue(row["select_disabled"])

    def test_privilege_row_anonymous_hidden(self):
        row = steward.cart_steward_privilege_row(AnonymousUser(), _line(self.book), None, 10**6)
        self.assertFalse(row["show"])

    def test_privilege_row_free_line_shown_without_hint(self):
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book, is_steward_free=True), self.book.id, 10**6)
        self.assertTrue(row["show"])
        self.assertTrue(row["is_free_line"])
        self.assertEqual(row["hint"], "")

    def test_privilege_row_other_line_already_free_hint(self):
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book), self.book.id + 999, 10**6)
        self.assertIn("Another line", row["hint"])
        self.assertTrue(row["select_disabled"])

    def test_privilege_row_in_cooldown_hint(self):
        self.steward.last_free_book_redeemed_at = timezone.now()
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book), None, 10**6)
        self.assertIn("30-day cooldown", row["hint"])

    def test_privilege_row_pool_too_small_hint(self):
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book), None, 999)
        self.assertIn("pool balance", row["hint"])

    def test_privilege_row_insufficient_stock_hint(self):
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book, quantity=4), None, 10**6)
        self.assertIn("Not enough stock", row["hint"])

    def test_privilege_row_quantity_not_one_hint(self):
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book, quantity=2), None, 10**6)
        self.assertIn("Set quantity to 1", row["hint"])

    def test_privilege_row_eligible_line_selectable(self):
        row = steward.cart_steward_privilege_row(self.steward, _line(self.book), None, 1000)
        self.assertFalse(row["select_disabled"])
        self.assertIn("Use your steward free book", row["hint"])


# --------------------------------------------------------------------------- Buyer/cart_helpers.py


class GetOrCreateCartTests(TestCase):
    def test_get_or_create_cart_is_idempotent(self):
        buyer = make_buyer()
        self.assertEqual(cart_helpers.get_or_create_cart(buyer), cart_helpers.get_or_create_cart(buyer))
        self.assertEqual(Cart.objects.filter(user=buyer).count(), 1)


class MergeSessionCartTests(TestCase):
    def setUp(self):
        self.buyer = make_buyer()
        self.book = make_book(price_cents=1200)

    def test_merge_empty_session_does_nothing(self):
        session = FakeSession()
        cart_helpers.merge_session_cart_into_db(self.buyer, session)
        self.assertFalse(Cart.objects.filter(user=self.buyer).exists())
        self.assertFalse(session.modified)

    def test_merge_new_lines_created_and_session_cleared(self):
        session = FakeSession(cart={str(self.book.id): {"quantity": 2}})
        cart_helpers.merge_session_cart_into_db(self.buyer, session)
        ci = CartItem.objects.get(cart__user=self.buyer)
        self.assertEqual((ci.quantity, ci.unit_price_cents), (2, 1200))
        self.assertEqual(session["cart"], {})
        self.assertTrue(session.modified)

    def test_merge_skips_bad_inactive_and_missing_books(self):
        inactive = make_book(is_active=False)
        session = FakeSession(cart={"abc": {"quantity": 1}, str(inactive.id): {"quantity": 1}, "999999": {"quantity": 1}})
        cart_helpers.merge_session_cart_into_db(self.buyer, session)
        self.assertFalse(CartItem.objects.exists())
        self.assertEqual(session["cart"], {})

    def test_merge_bad_quantity_defaults_to_one(self):
        other = make_book()
        session = FakeSession(cart={str(self.book.id): {"quantity": "lots"}, str(other.id): {"quantity": 0}})
        cart_helpers.merge_session_cart_into_db(self.buyer, session)
        self.assertEqual(sorted(CartItem.objects.values_list("quantity", flat=True)), [1, 1])

    def test_merge_existing_line_adds_quantity_and_refreshes_price(self):
        add_cart_item(self.buyer, self.book, quantity=1, unit_price_cents=500)
        cart_helpers.merge_session_cart_into_db(self.buyer, FakeSession(cart={str(self.book.id): {"quantity": 3}}))
        ci = CartItem.objects.get()
        self.assertEqual((ci.quantity, ci.unit_price_cents), (4, 1200))

    def test_merge_existing_steward_free_line_left_untouched(self):
        add_cart_item(self.buyer, self.book, quantity=1, unit_price_cents=0, is_steward_free=True)
        cart_helpers.merge_session_cart_into_db(self.buyer, FakeSession(cart={str(self.book.id): {"quantity": 3}}))
        ci = CartItem.objects.get()
        self.assertEqual((ci.quantity, ci.unit_price_cents, ci.is_steward_free), (1, 0, True))


class DbCartLinesTests(TestCase):
    def test_db_cart_lines_no_cart_returns_empty(self):
        self.assertEqual(cart_helpers.db_cart_lines(make_buyer()), ([], 0))

    def test_db_cart_lines_builds_line_dicts_and_subtotal(self):
        buyer = make_buyer()
        a, b = make_book(price_cents=1000), make_book(price_cents=250)
        add_cart_item(buyer, a, quantity=2)
        add_cart_item(buyer, b, quantity=1)
        lines, subtotal = cart_helpers.db_cart_lines(buyer)
        self.assertEqual(subtotal, 2250)
        by_id = {line["id"]: line for line in lines}
        self.assertEqual(by_id[a.id]["subtotal"], 20.0)
        self.assertEqual(by_id[a.id]["price"], 10.0)
        self.assertEqual(by_id[b.id]["line_subtotal_cents"], 250)
        self.assertFalse(by_id[a.id]["is_steward_free"])

    def test_db_cart_lines_inactive_book_line_deleted(self):
        buyer = make_buyer()
        add_cart_item(buyer, make_book(is_active=False))
        add_cart_item(buyer, make_book(price_cents=300))
        lines, subtotal = cart_helpers.db_cart_lines(buyer)
        self.assertEqual((len(lines), subtotal), (1, 300))
        self.assertEqual(CartItem.objects.filter(cart__user=buyer).count(), 1)


class ClearAndAddToDbCartTests(TestCase):
    def setUp(self):
        self.buyer = make_buyer()
        self.book = make_book(price_cents=800)

    def test_clear_db_cart_removes_only_that_users_items(self):
        other = make_buyer()
        add_cart_item(self.buyer, self.book)
        add_cart_item(other, self.book)
        cart_helpers.clear_db_cart(self.buyer)
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer).exists())
        self.assertTrue(CartItem.objects.filter(cart__user=other).exists())

    def test_add_book_to_db_cart_new_line_returns_true(self):
        self.assertTrue(cart_helpers.add_book_to_db_cart(self.buyer, self.book, 2))
        self.assertEqual(CartItem.objects.get().quantity, 2)

    def test_add_book_to_db_cart_quantity_below_one_clamped(self):
        cart_helpers.add_book_to_db_cart(self.buyer, self.book, 0)
        self.assertEqual(CartItem.objects.get().quantity, 1)

    def test_add_book_to_db_cart_existing_line_incremented_and_repriced(self):
        add_cart_item(self.buyer, self.book, quantity=1, unit_price_cents=100)
        self.assertTrue(cart_helpers.add_book_to_db_cart(self.buyer, self.book, 2))
        ci = CartItem.objects.get()
        self.assertEqual((ci.quantity, ci.unit_price_cents), (3, 800))

    def test_add_book_to_db_cart_steward_free_line_blocked(self):
        add_cart_item(self.buyer, self.book, unit_price_cents=0, is_steward_free=True)
        self.assertFalse(cart_helpers.add_book_to_db_cart(self.buyer, self.book, 1))
        self.assertEqual(CartItem.objects.get().quantity, 1)


# --------------------------------------------------------------------------- Seller/webhook_notify.py


class SerializeWebhookBodyTests(SimpleTestCase):
    def test_serialize_webhook_body_sorted_compact_utf8(self):
        body = webhook_notify.serialize_webhook_body({"b": 1, "a": "é"})
        self.assertEqual(body, '{"a":"\\u00e9","b":1}'.encode("utf-8"))

    def test_serialize_webhook_body_key_order_independent(self):
        self.assertEqual(
            webhook_notify.serialize_webhook_body({"x": 1, "y": 2}),
            webhook_notify.serialize_webhook_body({"y": 2, "x": 1}),
        )


@mock.patch("Seller.webhook_notify.requests.post")
class PostSellerWebhookTests(SimpleTestCase):
    payload = {"event": "webhook.test"}

    def test_post_seller_webhook_success_returns_true(self, post):
        ok, err = webhook_notify.post_seller_webhook("https://h.example/x", "", self.payload)
        self.assertEqual((ok, err), (True, None))
        _, kwargs = post.call_args
        self.assertEqual(kwargs["data"], webhook_notify.serialize_webhook_body(self.payload))
        self.assertEqual(kwargs["timeout"], 10)
        self.assertNotIn("X-Webhook-Signature", kwargs["headers"])

    def test_post_seller_webhook_secret_adds_hmac_signature(self, post):
        webhook_notify.post_seller_webhook("https://h.example/x", "  s3cret ", self.payload)
        headers = post.call_args.kwargs["headers"]
        body = webhook_notify.serialize_webhook_body(self.payload)
        expected = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Webhook-Signature"], expected)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_post_seller_webhook_connection_error_returns_false_with_message(self, post):
        post.side_effect = requests.ConnectionError("boom")
        ok, err = webhook_notify.post_seller_webhook("https://h.example/x", None, self.payload)
        self.assertFalse(ok)
        self.assertIn("boom", err)

    def test_post_seller_webhook_http_error_status_returns_false(self, post):
        post.return_value.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        ok, err = webhook_notify.post_seller_webhook("https://h.example/x", "", self.payload)
        self.assertFalse(ok)
        self.assertIn("500", err)


class WebhookPayloadHelperTests(TestCase):
    def setUp(self):
        self.seller = make_seller()
        self.book = make_book(self.seller, title="Hooked")
        self.buyer = make_buyer()

    def test_ship_block_without_snapshot_returns_blank_fields(self):
        order = make_order(self.buyer, [(self.book, 1)], with_snapshots=False)
        block = webhook_notify._ship_block_from_order(order)
        self.assertEqual(set(block.values()), {""})
        self.assertEqual(set(block), {"name", "line1", "city", "state", "postal_code", "country"})

    def test_ship_block_with_snapshot_joins_line2(self):
        order = make_order(self.buyer, [(self.book, 1)])
        snap = order.shipping_snapshot
        snap.shipping_line2 = "Apt 4"
        snap.save()
        order.refresh_from_db()
        block = webhook_notify._ship_block_from_order(order)
        self.assertEqual(block["line1"], "1 Main St, Apt 4")
        self.assertEqual(block["name"], "Buyer")
        self.assertEqual(block["country"], "US")

    def test_ship_block_with_snapshot_without_line2(self):
        order = make_order(self.buyer, [(self.book, 1)])
        self.assertEqual(webhook_notify._ship_block_from_order(order)["line1"], "1 Main St")

    def test_display_title_uses_snapshot_title(self):
        order = make_order(self.buyer, [(self.book, 1)])
        oi = order.order_items.get()
        oi.book_snapshot.title = "Snapshot Title"
        oi.book_snapshot.save()
        self.assertEqual(webhook_notify._display_title_for_item(oi), "Snapshot Title")

    def test_display_title_without_snapshot_uses_item_title_and_quantity(self):
        order = make_order(self.buyer, [(self.book, 3)], with_snapshots=False)
        oi = order.order_items.get()
        self.assertEqual(webhook_notify._display_title_for_item(oi), "Hooked (×3)")

    def test_display_title_blank_title_falls_back_to_unknown(self):
        order = make_order(self.buyer, [(self.book, 1)], with_snapshots=False)
        oi = order.order_items.get()
        oi.title = ""
        self.assertEqual(webhook_notify._display_title_for_item(oi), "Unknown")

    def test_build_test_webhook_payload_shape(self):
        payload = webhook_notify.build_test_webhook_payload()
        self.assertEqual(payload["event"], "webhook.test")
        self.assertTrue(payload["order_placed_at"])
        self.assertEqual(payload["ship_to_address"]["state"], "CA")

    # BUG: build_order_placed_payload reads `order.orderitem`, but the related_name is `order_items`.
    @unittest.expectedFailure
    def test_build_order_placed_payload_lists_only_this_sellers_titles(self):
        other_book = make_book(make_seller(), title="Not Mine")
        order = make_order(self.buyer, [(self.book, 1), (other_book, 1)])
        payload = webhook_notify.build_order_placed_payload(order, self.seller.id)
        self.assertEqual(payload["event"], "order.placed")
        self.assertEqual(payload["product_name"], "Hooked")


class SendOrderPlacedWebhooksTests(TestCase):
    def setUp(self):
        self.seller = make_seller()
        profile = SellerProfile.objects.get(user=self.seller)
        profile.webhook_enabled = True
        profile.webhook_url = "https://hooks.example.com/orders"
        profile.webhook_secret = "k"
        profile.save()
        self.order = make_order(make_buyer(), [(make_book(self.seller, title="Hooked"), 1)])

    @mock.patch("Seller.webhook_notify.requests.post")
    def test_send_webhooks_missing_order_returns_without_posting(self, post):
        self.assertIsNone(webhook_notify.send_order_placed_webhooks_for_order(10**9))
        post.assert_not_called()

    @mock.patch("Seller.webhook_notify.requests.post")
    def test_send_webhooks_disabled_profile_does_not_post(self, post):
        SellerProfile.objects.filter(user=self.seller).update(webhook_enabled=False)
        webhook_notify.send_order_placed_webhooks_for_order(self.order.id)
        post.assert_not_called()

    # BUG: send_order_placed_webhooks_for_order prefetches "orderitem" (should be "order_items");
    # the error is swallowed by `except Exception`, so enabled sellers never receive webhooks.
    @unittest.expectedFailure
    @mock.patch("Seller.webhook_notify.requests.post")
    def test_send_webhooks_enabled_seller_receives_post(self, post):
        webhook_notify.send_order_placed_webhooks_for_order(self.order.id)
        post.assert_called_once()
        body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(body["event"], "order.placed")

    def test_schedule_order_placed_webhooks_registers_on_commit(self):
        with mock.patch("Seller.webhook_notify.send_order_placed_webhooks_for_order") as send:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                webhook_notify.schedule_order_placed_webhooks(self.order.id)
        self.assertEqual(len(callbacks), 1)
        send.assert_called_once_with(self.order.id)

    def test_schedule_order_placed_webhooks_not_run_before_commit(self):
        with mock.patch("Seller.webhook_notify.send_order_placed_webhooks_for_order") as send:
            with self.captureOnCommitCallbacks(execute=False):
                webhook_notify.schedule_order_placed_webhooks(self.order.id)
        send.assert_not_called()


# --------------------------------------------------------------------------- Buyer/views.py helpers


class StewardContributionParsingTests(SimpleTestCase):
    parse = staticmethod(buyer_views._parse_steward_contribution_dollars)

    def test_parse_contribution_none_or_blank_returns_default(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertEqual(self.parse(raw), (Decimal("2.00"), False))

    def test_parse_contribution_valid_value_quantized(self):
        self.assertEqual(self.parse(" 5 "), (Decimal("5.00"), False))
        self.assertEqual(self.parse("1.005"), (Decimal("1.01"), False))
        self.assertEqual(self.parse("0"), (Decimal("0.00"), False))

    def test_parse_contribution_non_string_numeric_accepted(self):
        self.assertEqual(self.parse(3), (Decimal("3.00"), False))

    def test_parse_contribution_garbage_invalid(self):
        self.assertEqual(self.parse("abc"), (None, True))

    def test_parse_contribution_negative_invalid(self):
        self.assertEqual(self.parse("-0.01"), (None, True))

    def test_parse_contribution_over_max_clamped_to_hundred(self):
        self.assertEqual(self.parse("250"), (Decimal("100.00"), False))

    # BUG: Decimal("NaN") parses, then `v < 0` raises InvalidOperation (uncaught -> HTTP 500 at checkout).
    @unittest.expectedFailure
    def test_parse_contribution_nan_invalid(self):
        self.assertEqual(self.parse("NaN"), (None, True))

    def test_steward_cents_from_dollars_rounds_half_up(self):
        self.assertEqual(buyer_views._steward_cents_from_dollars(Decimal("2.00")), 200)
        self.assertEqual(buyer_views._steward_cents_from_dollars(Decimal("0.005")), 1)

    def test_steward_points_from_cents_ten_cents_per_point(self):
        self.assertEqual(buyer_views._steward_points_from_contribution_cents(200), 20)
        self.assertEqual(buyer_views._steward_points_from_contribution_cents(9), 0)
        self.assertEqual(buyer_views._steward_points_from_contribution_cents(0), 0)


class BuyerReturnStatusLabelTests(SimpleTestCase):
    def test_return_status_label_each_status(self):
        cases = {
            "refunded": "Return complete",
            "rejected": "Return declined",
            "requested": "Return requested",
            "approved": "Return requested",
            "received": "Return requested",
        }
        for status, label in cases.items():
            with self.subTest(status=status):
                self.assertEqual(buyer_views._buyer_return_status_label(mock.Mock(status=status)), label)

    def test_return_status_label_none_returns_none(self):
        self.assertIsNone(buyer_views._buyer_return_status_label(None))


class SessionCartLinesTests(TestCase):
    def test_cart_lines_builds_lines_and_skips_bad_entries(self):
        good = make_book(price_cents=700)
        inactive = make_book(is_active=False)
        lines, subtotal = buyer_views._cart_lines(
            {str(good.id): {"quantity": 2}, str(inactive.id): {"quantity": 1}, "x": {"quantity": 1}, "999999": {}}
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(subtotal, 1400)
        self.assertEqual(lines[0]["price"], 7.0)
        self.assertEqual(lines[0]["subtotal"], 14.0)

    def test_cart_lines_zero_quantity_treated_as_one(self):
        book = make_book(price_cents=100)
        lines, subtotal = buyer_views._cart_lines({str(book.id): {"quantity": 0}})
        self.assertEqual((lines[0]["quantity"], subtotal), (1, 100))

    def test_cart_lines_empty_session_returns_empty(self):
        self.assertEqual(buyer_views._cart_lines({}), ([], 0))


class OrderItemsFromSnapshotsTests(TestCase):
    def test_order_items_use_snapshot_title_after_book_edit(self):
        book = make_book(title="Original", price_cents=1250)
        order = make_order(make_buyer(), [(book, 2)])
        book.title = "Edited"
        book.save()
        item = buyer_views._order_items_from_snapshots(order)[0]
        self.assertEqual(item["title"], "Original")
        self.assertEqual(item["unit_price"], 12.5)
        self.assertEqual(item["line_total_cents"], 2500)
        self.assertEqual(item["book_id"], book.id)

    def test_order_items_without_snapshot_use_item_title_then_book_title(self):
        book = make_book(title="Live Title", author="Live Author")
        order = make_order(make_buyer(), [(book, 1)], with_snapshots=False)
        order.order_items.update(title="", author="Stored Author")
        item = buyer_views._order_items_from_snapshots(order)[0]
        self.assertEqual(item["title"], "Live Title")
        self.assertEqual(item["author"], "Stored Author")
        self.assertEqual(item["deposit_amount"], 0.0)


class CheckoutPageContextTests(TestCase):
    def setUp(self):
        self.buyer = make_buyer()
        self.book = make_book(price_cents=1000)

    def test_checkout_context_normal_cart_default_contribution(self):
        ctx = buyer_views._checkout_page_context(self.buyer, [_line(self.book)], 1000, None)
        self.assertEqual(ctx["tax"], 0.7)
        self.assertEqual(ctx["fees"], 2.99)
        self.assertEqual(ctx["base_total_cents"], 1369)
        self.assertEqual(ctx["final_total"], 15.69)
        self.assertEqual(ctx["steward_points_preview"], 20)
        self.assertEqual(ctx["steward_contribution_value"], "2.00")
        self.assertFalse(ctx["steward_contribution_invalid"])

    def test_checkout_context_loads_addresses_and_payments_when_not_given(self):
        make_address(self.buyer)
        make_payment_method(self.buyer)
        ctx = buyer_views._checkout_page_context(self.buyer, [], 0, None)
        self.assertEqual(len(ctx["addresses"]), 1)
        self.assertEqual(len(ctx["payment_methods"]), 1)

    def test_checkout_context_empty_cart_has_no_fees(self):
        ctx = buyer_views._checkout_page_context(self.buyer, [], 0, "0")
        self.assertEqual((ctx["fees"], ctx["final_total"]), (0, 0))

    def test_checkout_context_steward_free_cart_waives_fees_and_contribution(self):
        ctx = buyer_views._checkout_page_context(
            self.buyer, [_line(self.book, is_steward_free=True)], 0, "50", addresses=[], payment_methods=[]
        )
        self.assertTrue(ctx["cart_has_steward_free"])
        self.assertEqual(ctx["fees"], 0)
        self.assertEqual(ctx["steward_contribution_value"], "0.00")
        self.assertEqual(ctx["steward_points_preview"], 0)

    def test_checkout_context_invalid_contribution_flagged_and_not_charged(self):
        ctx = buyer_views._checkout_page_context(self.buyer, [_line(self.book)], 1000, " abc ", checkout_error="x")
        self.assertTrue(ctx["steward_contribution_invalid"])
        self.assertEqual(ctx["steward_contribution_value"], "abc")
        self.assertEqual(ctx["final_total"], 13.69)
        self.assertEqual(ctx["checkout_error"], "x")


class BuyerDashboardContextTests(TestCase):
    def test_dashboard_context_anonymous_returns_defaults(self):
        ctx = buyer_views._buyer_dashboard_context(AnonymousUser())
        self.assertEqual(ctx["stats"], {"total_orders": 0, "open_orders": 0})
        self.assertEqual(ctx["credit_balance"], "0.00")

    def test_dashboard_context_buyer_counts_orders_and_credit(self):
        buyer = make_buyer(store_credit_cents=1234)
        book = make_book()
        for status in ("paid", "shipped", "delivered", "cancelled", "refunded"):
            make_order(buyer, [(book, 1)], status=status)
        ctx = buyer_views._buyer_dashboard_context(buyer)
        self.assertEqual(ctx["credit_balance"], "12.34")
        self.assertEqual(ctx["stats"], {"total_orders": 5, "open_orders": 2})
        self.assertEqual(len(ctx["recent_orders"]), 5)
        self.assertTrue(all(hasattr(o, "total_display") for o in ctx["recent_orders"]))
        self.assertNotIn("steward_pool_dollars", ctx)

    def test_dashboard_context_steward_includes_pool_and_cooldown(self):
        ensure_pool(123_456)
        ctx = buyer_views._buyer_dashboard_context(make_steward())
        self.assertEqual(ctx["steward_pool_dollars"], "1,234.56")
        self.assertTrue(ctx["steward_free_book_eligible"])
        self.assertEqual(ctx["steward_free_cooldown_progress"], 100)
        self.assertIsNone(ctx["steward_next_free_at"])


# --------------------------------------------------------------------------- General/views.py helpers


class SanitizeTitleForFilenameTests(SimpleTestCase):
    funcs = (general_views._sanitize_book_title_for_filename, seller_views._sanitize_book_title_for_filename)

    def test_sanitize_title_empty_returns_book(self):
        for f in self.funcs:
            self.assertEqual(f(""), "book")
            self.assertEqual(f(None), "book")

    def test_sanitize_title_replaces_invalid_and_control_chars(self):
        for f in self.funcs:
            self.assertEqual(f('a/b:c*d?"e<f>g|h\\i\x01'), "a_b_c_d__e_f_g_h_i_")

    def test_sanitize_title_strips_spaces_and_dots(self):
        for f in self.funcs:
            self.assertEqual(f("  Title. "), "Title")

    def test_sanitize_title_only_dots_returns_book(self):
        for f in self.funcs:
            self.assertEqual(f("..."), "book")


class BookCoverLookupTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for name in ("1984.jpg", "Mixed Case.PNG", "a_b.gif", "notes.txt"):
            (self.dir / name).write_bytes(b"x")
        (self.dir / "folder.jpg").mkdir()
        patcher = mock.patch.object(general_views, "_BOOK_IMAGES_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cover_filename_exact_title_match(self):
        self.assertEqual(general_views._get_book_cover_filename("1984"), "1984.jpg")

    def test_cover_filename_case_insensitive_match(self):
        self.assertEqual(general_views._get_book_cover_filename("MIXED CASE"), "Mixed Case.PNG")

    def test_cover_filename_sanitized_title_match(self):
        self.assertEqual(general_views._get_book_cover_filename("a/b"), "a_b.gif")

    def test_cover_filename_missing_or_non_image_none(self):
        self.assertIsNone(general_views._get_book_cover_filename("Nope"))
        self.assertIsNone(general_views._get_book_cover_filename("notes"))
        self.assertIsNone(general_views._get_book_cover_filename("folder"))

    def test_cover_filename_empty_title_none(self):
        self.assertIsNone(general_views._get_book_cover_filename(""))

    def test_cover_filename_missing_directory_none(self):
        with mock.patch.object(general_views, "_BOOK_IMAGES_DIR", self.dir / "absent"):
            self.assertIsNone(general_views._get_book_cover_filename("1984"))

    def test_cover_static_path_prefixes_book_images(self):
        self.assertEqual(general_views._get_book_cover_static_path("1984"), "book_images/1984.jpg")
        self.assertIsNone(general_views._get_book_cover_static_path("Nope"))


class RepoBookCoverTests(SimpleTestCase):
    def test_cover_filename_repo_images_dir_finds_1984(self):
        """Read-only check against the shipped media/book_images/ directory."""
        self.assertEqual(general_views._get_book_cover_filename("1984"), "1984.jpg")


# --------------------------------------------------------------------------- Seller/views.py helpers


class RequireSellerTests(TestCase):
    def test_require_seller_by_user_kind(self):
        self.assertFalse(seller_views._require_seller(AnonymousUser()))
        self.assertFalse(seller_views._require_seller(make_buyer()))
        self.assertFalse(seller_views._require_seller(make_seller(approved=False)))
        self.assertFalse(seller_views._require_seller(make_admin()))
        self.assertTrue(seller_views._require_seller(make_seller()))


class GetBookImagesDirTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "proj").mkdir()

    def test_book_images_dir_existing_under_base_dir_returned(self):
        (self.root / "proj" / "media" / "book_images").mkdir(parents=True)
        with override_settings(BASE_DIR=self.root / "proj"):
            self.assertEqual(
                seller_views._get_book_images_dir(),
                self.root / "proj" / "media" / "book_images",
            )

    def test_book_images_dir_missing_falls_back_to_parent_and_creates(self):
        with override_settings(BASE_DIR=self.root / "proj"):
            d = seller_views._get_book_images_dir()
        self.assertEqual(d, self.root / "media" / "book_images")
        self.assertTrue(d.is_dir())


class SellerOrderHelperTests(TestCase):
    def setUp(self):
        self.s1, self.s2 = make_seller(), make_seller()
        self.b1 = make_book(self.s1, price_cents=1000)
        self.b1b = make_book(self.s1, price_cents=500)
        self.b2 = make_book(self.s2, price_cents=300)
        self.orphan = make_book(None, price_cents=100)
        self.buyer = make_buyer()
        self.order = make_order(self.buyer, [(self.b1, 1), (self.b1b, 2), (self.b2, 1), (self.orphan, 1)])

    def test_distinct_seller_ids_ignores_books_without_seller(self):
        self.assertEqual(seller_views._order_distinct_seller_user_ids(self.order), {self.s1.id, self.s2.id})

    def test_seller_line_total_sums_only_that_sellers_lines(self):
        self.assertEqual(seller_views._seller_line_total_cents_for_order(self.order, self.s1), 2000)
        self.assertEqual(seller_views._seller_line_total_cents_for_order(self.order, make_seller()), 0)

    def test_maybe_finalize_rejected_return_unchanged(self):
        rr = make_return(self.order, status="rejected")
        for s in (self.s1, self.s2):
            SellerReturnReceipt.objects.create(return_request=rr, seller=s, amount_credited_cents=1)
        seller_views._maybe_finalize_buyer_return(rr)
        rr.refresh_from_db()
        self.assertEqual(rr.status, "rejected")

    def test_maybe_finalize_order_without_sellers_unchanged(self):
        rr = make_return(make_order(self.buyer, [(self.orphan, 1)]))
        seller_views._maybe_finalize_buyer_return(rr)
        rr.refresh_from_db()
        self.assertEqual(rr.status, "requested")

    def test_maybe_finalize_partial_ack_unchanged(self):
        rr = make_return(self.order)
        SellerReturnReceipt.objects.create(return_request=rr, seller=self.s1, amount_credited_cents=1)
        seller_views._maybe_finalize_buyer_return(rr)
        rr.refresh_from_db()
        self.assertEqual(rr.status, "requested")

    def test_maybe_finalize_all_sellers_ack_marks_refunded(self):
        rr = make_return(self.order)
        for s in (self.s1, self.s2):
            SellerReturnReceipt.objects.create(return_request=rr, seller=s, amount_credited_cents=1)
        seller_views._maybe_finalize_buyer_return(rr)
        rr.refresh_from_db()
        self.assertEqual(rr.status, "refunded")

    def test_pending_returns_includes_open_excludes_rejected_and_acknowledged(self):
        open_rr = make_return(self.order)
        other_order = make_order(self.buyer, [(self.b1, 1)])
        make_return(other_order, status="rejected")
        third = make_order(self.buyer, [(self.b1, 1)])
        acked = make_return(third)
        SellerReturnReceipt.objects.create(return_request=acked, seller=self.s1, amount_credited_cents=1)
        self.assertEqual(list(seller_views._pending_return_requests_qs(self.s1)), [open_rr])
        self.assertEqual(list(seller_views._pending_return_requests_qs(self.s2)), [open_rr])

    def test_pending_returns_other_seller_sees_nothing(self):
        make_return(self.order)
        self.assertEqual(list(seller_views._pending_return_requests_qs(make_seller())), [])

    def test_seller_sales_total_counts_recognized_statuses_only(self):
        for status in ("pending", "cancelled", "refunded", "shipped", "delivered"):
            make_order(self.buyer, [(self.b1, 1)], status=status)
        # paid order from setUp (2000) + shipped (1000) + delivered (1000)
        self.assertEqual(seller_views._seller_sales_total_cents(self.s1), 4000)
        self.assertEqual(seller_views._seller_sales_total_cents(make_seller()), 0)

    def test_format_cents_as_dollars(self):
        self.assertEqual(seller_views._format_cents_as_dollars(12345), "123.45")
        self.assertEqual(seller_views._format_cents_as_dollars(0), "0.00")
        self.assertEqual(seller_views._format_cents_as_dollars(5), "0.05")


class SellerDashboardStatsTests(TestCase):
    def test_dashboard_stats_counts_listings_and_stock_levels(self):
        seller = make_seller()
        make_book(seller, stock=10)
        make_book(seller, stock=2)
        make_book(seller, stock=0)
        make_book(seller, stock=0, is_active=False)
        make_book(seller, stock=1, is_active=False)
        make_book(make_seller(), stock=0)
        order = make_order(make_buyer(), [(make_book(seller), 1)])
        make_return(order)
        stats = seller_views._seller_dashboard_stats(seller)
        self.assertEqual(stats["published_books"], 4)
        self.assertEqual(stats["unpublished_books"], 2)
        self.assertEqual(stats["low_stock_count"], 1)
        self.assertEqual(stats["out_of_stock_count"], 1)
        self.assertEqual(stats["pending_returns_count"], 1)

    def test_dashboard_stats_new_seller_all_zero(self):
        stats = seller_views._seller_dashboard_stats(make_seller())
        self.assertEqual(
            (stats["published_books"], stats["unpublished_books"], stats["low_stock_count"],
             stats["out_of_stock_count"], stats["pending_returns_count"]),
            (0, 0, 0, 0, 0),
        )


# --------------------------------------------------------------------------- Admin/views.py helpers


class FormatStatusTests(SimpleTestCase):
    def test_format_status_known_and_unknown_values(self):
        cases = {"open": "Open", "reviewing": "Triage", "RESOLVED": "Resolved", "dismissed": "Dismissed",
                 "weird": "weird", None: "Open", "": "Open"}
        for raw, label in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(admin_views._format_status(raw), label)


class FlagRowTests(TestCase):
    def setUp(self):
        self.reporter = make_buyer()
        self.seller = make_seller(email="target@example.com")

    def test_flag_rows_with_target_user(self):
        flag = make_flag(self.reporter, book=make_book(self.seller), flag_type="payment", status="reviewing", details="odd")
        for fn in (admin_views._flag_to_steward_row, admin_views._flag_to_payment_row):
            row = fn(flag)
            self.assertEqual(row["username"], "target@example.com")
            self.assertEqual(row["user_id"], self.seller.id)
            self.assertEqual(row["status"], "Triage")
            self.assertEqual(row["severity"], "Medium")
        self.assertEqual(admin_views._flag_to_steward_row(flag)["reason"], "odd")
        self.assertEqual(admin_views._flag_to_payment_row(flag)["signal"], "odd")
        all_row = admin_views._flag_to_all_row(flag)
        self.assertEqual(all_row["flag_id"], f"F{flag.id}")
        self.assertEqual(all_row["target"], "target@example.com")
        self.assertEqual(all_row["type"], "payment")

    def test_flag_rows_book_only_target(self):
        book = make_book(None)
        flag = make_flag(self.reporter, book=book, details=None)
        self.assertEqual(admin_views._flag_to_all_row(flag)["target"], f"Book ID {book.id}")
        self.assertEqual(admin_views._flag_to_all_row(flag)["reason"], "—")
        self.assertEqual(admin_views._flag_to_steward_row(flag)["username"], "—")
        self.assertEqual(admin_views._flag_to_payment_row(flag)["user_id"], "—")

    def test_flag_rows_no_target(self):
        flag = make_flag(self.reporter, flag_type="", details="")
        row = admin_views._flag_to_all_row(flag)
        self.assertEqual((row["target"], row["type"], row["reason"]), ("—", "—", "—"))
        self.assertEqual(admin_views._flag_to_payment_row(flag)["signal"], "—")


class StaffRequiredDecoratorTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.view = admin_views.staff_required(lambda request: HttpResponse("secret"))

    def _request(self, user):
        request = self.rf.get("/staff/thing/?a=1")
        SessionMiddleware(lambda r: None).process_request(request)
        request.user = user
        return request

    def test_staff_required_anonymous_redirects_to_staff_login_with_next(self):
        response = self.view(self._request(AnonymousUser()))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/staff/login/?next=/staff/thing/%3Fa%3D1")

    def test_staff_required_non_admin_logged_out_and_redirected(self):
        for user in (make_buyer(), make_admin(is_staff=False), make_seller(is_staff=True)):
            with self.subTest(role=user.role):
                request = self._request(user)
                response = self.view(request)
                self.assertEqual(response["Location"], "/staff/login/")
                self.assertFalse(request.user.is_authenticated)

    def test_staff_required_admin_staff_passes_through(self):
        response = self.view(self._request(make_admin()))
        self.assertEqual(response.content, b"secret")

    def test_staff_required_preserves_wrapped_name(self):
        self.assertEqual(admin_views.admin_dashboard.__name__, "admin_dashboard")


# --------------------------------------------------------------------------- middleware / commands


class NoCacheMiddlewareTests(TestCase):
    def setUp(self):
        self.mw = NoCacheMiddleware(lambda request: HttpResponse("ok"))
        self.rf = RequestFactory()

    def test_no_cache_middleware_authenticated_sets_headers(self):
        request = self.rf.get("/")
        request.user = make_buyer()
        response = self.mw(request)
        self.assertEqual(response["Cache-Control"], "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["Expires"], "0")

    def test_no_cache_middleware_anonymous_leaves_headers(self):
        request = self.rf.get("/")
        request.user = AnonymousUser()
        response = self.mw(request)
        self.assertNotIn("Cache-Control", response)
        self.assertNotIn("Pragma", response)


class CreateAdminCommandTests(TestCase):
    def _call(self, **kw):
        out = StringIO()
        call_command("create_admin", stdout=out, **kw)
        return out.getvalue()

    def test_create_admin_new_user_created_with_lowercased_email(self):
        out = self._call(email="  Boss@Example.COM ", password="pw-12345678")
        user = User.objects.get(email="boss@example.com")
        self.assertEqual((user.role, user.is_staff), ("admin", True))
        self.assertEqual((user.first_name, user.last_name), ("Admin", "Staff"))
        self.assertTrue(user.check_password("pw-12345678"))
        self.assertIn("Admin user created", out)

    def test_create_admin_existing_user_promoted(self):
        buyer = make_buyer(email="promote@example.com")
        out = self._call(email="PROMOTE@example.com", password="new-pass-123", first_name="Ada", last_name="L")
        buyer.refresh_from_db()
        self.assertEqual((buyer.role, buyer.is_staff, buyer.first_name), ("admin", True, "Ada"))
        self.assertTrue(buyer.check_password("new-pass-123"))
        self.assertIn("Updated existing user", out)

    def test_create_admin_admin_already_exists_skips(self):
        make_admin()
        before = User.objects.count()
        out = self._call(email="second@example.com", password="pw")
        self.assertIn("already exists", out)
        self.assertEqual(User.objects.count(), before)

    def test_create_admin_missing_required_args_errors(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("create_admin", stdout=StringIO())
