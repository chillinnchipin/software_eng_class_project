"""Unit tests for models in General, Buyer, Seller, and Admin apps."""

from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError, transaction
from django.test import TestCase

from Admin.models import FlagReport
from Buyer.models import (
    Cart,
    CartItem,
    Order,
    OrderItem,
    OrderItemBookSnapshot,
    OrderShippingAddress,
    PaymentMethod,
    ReturnRequest,
    Review,
    SellerReturnReceipt,
)
from General.admin import StewardPoolAdmin
from General.models import (
    Address,
    Book,
    Inventory,
    Notification,
    StewardContribution,
    StewardPool,
    User,
)
from Seller.models import SellerProfile
from tests.helpers import (
    add_cart_item,
    make_address,
    make_book,
    make_buyer,
    make_flag,
    make_order,
    make_payment_method,
    make_return,
    make_review,
    make_seller,
)


class UserManagerTests(TestCase):
    def test_create_user_without_email_raises_value_error(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="x", first_name="a", last_name="b")

    def test_create_user_normalizes_email_domain(self):
        user = User.objects.create_user(email="Someone@EXAMPLE.COM", password="pw12345678")
        self.assertEqual(user.email, "Someone@example.com")

    def test_create_user_hashes_password(self):
        user = User.objects.create_user(email="a@example.com", password="pw12345678")
        self.assertNotEqual(user.password, "pw12345678")
        self.assertTrue(user.check_password("pw12345678"))

    def test_create_user_without_password_is_unusable(self):
        user = User.objects.create_user(email="nopw@example.com")
        self.assertFalse(user.has_usable_password())

    def test_create_superuser_sets_admin_defaults(self):
        user = User.objects.create_superuser(email="root@example.com", password="pw12345678")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.role, "admin")

    def test_create_superuser_is_staff_false_raises_value_error(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="r1@example.com", password="pw", is_staff=False)

    def test_create_superuser_is_superuser_false_raises_value_error(self):
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="r2@example.com", password="pw", is_superuser=False)


class UserModelTests(TestCase):
    def test_user_str_returns_email(self):
        user = make_buyer(email="str@example.com")
        self.assertEqual(str(user), "str@example.com")

    def test_user_is_steward_reflects_steward_verified(self):
        user = make_buyer()
        self.assertFalse(user.is_steward)
        user.steward_verified = True
        self.assertTrue(user.is_steward)

    def test_user_defaults_unapproved_no_credit(self):
        user = User.objects.create_user(email="d@example.com", password="pw", role="buyer")
        self.assertFalse(user.buyer_approved)
        self.assertFalse(user.seller_approved)
        self.assertEqual(user.store_credit_cents, 0)
        self.assertEqual(user.steward_progress, 0)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)

    def test_user_duplicate_email_raises_integrity_error(self):
        make_buyer(email="dup@example.com")
        with self.assertRaises(IntegrityError):
            make_buyer(email="dup@example.com")

    def test_user_username_field_is_email(self):
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertEqual(User.REQUIRED_FIELDS, ["first_name", "last_name"])


class AddressModelTests(TestCase):
    def test_address_str_includes_email_and_label(self):
        buyer = make_buyer(email="addr@example.com")
        addr = make_address(buyer, label="Dorm")
        self.assertEqual(str(addr), "addr@example.com - Dorm")


class BookModelTests(TestCase):
    def test_book_str_returns_title(self):
        self.assertEqual(str(make_book(title="Dune")), "Dune")

    def test_book_price_dollars_converts_cents(self):
        self.assertEqual(make_book(price_cents=1999).price_dollars, 19.99)

    def test_book_stock_quantity_reads_inventory(self):
        self.assertEqual(make_book(stock=7).stock_quantity, 7)

    def test_book_stock_quantity_without_inventory_returns_zero(self):
        self.assertEqual(make_book(with_inventory=False).stock_quantity, 0)

    def test_book_seller_deleted_sets_seller_null(self):
        seller = make_seller()
        book = make_book(seller)
        seller.delete()
        book.refresh_from_db()
        self.assertIsNone(book.seller_user)

    def test_book_defaults_active(self):
        book = Book.objects.create(title="T", author="A", condition="New", base_price_cents=100)
        self.assertTrue(book.is_active)


class InventoryModelTests(TestCase):
    def test_inventory_str_mentions_book_title(self):
        book = make_book(title="Emma")
        self.assertEqual(str(book.inventory), "Emma inventory")

    def test_inventory_defaults_reserved_and_threshold_zero(self):
        book = make_book(with_inventory=False)
        inv = Inventory.objects.create(book=book, quantity_available=3)
        self.assertEqual(inv.quantity_reserved, 0)
        self.assertEqual(inv.reorder_threshold, 0)

    def test_inventory_second_row_for_same_book_raises_integrity_error(self):
        book = make_book()
        with self.assertRaises(IntegrityError):
            Inventory.objects.create(book=book, quantity_available=1)

    def test_inventory_deleted_with_book(self):
        book = make_book()
        book.delete()
        self.assertFalse(Inventory.objects.exists())


class StewardPoolModelTests(TestCase):
    def test_steward_pool_save_forces_singleton_pk(self):
        StewardPool.objects.all().delete()
        pool = StewardPool(pk=99, pool_cents=5)
        pool.save()
        self.assertEqual(pool.pk, 1)
        self.assertEqual(StewardPool.objects.count(), 1)

    def test_steward_pool_second_instance_overwrites_first(self):
        StewardPool(pool_cents=10).save()
        StewardPool(pool_cents=20).save()
        self.assertEqual(StewardPool.objects.count(), 1)
        self.assertEqual(StewardPool.objects.get().pool_cents, 20)

    def test_steward_pool_seeded_by_migration_with_one_million(self):
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, 100_000_000)

    def test_steward_pool_admin_pool_dollars_formats_currency(self):
        admin = StewardPoolAdmin(StewardPool, AdminSite())
        self.assertEqual(admin.pool_dollars(StewardPool(pool_cents=123456789)), "$1,234,567.89")


class StewardContributionAndNotificationTests(TestCase):
    def test_steward_contribution_str_shows_name_and_city(self):
        c = StewardContribution.objects.create(
            steward_user=make_buyer(), contributor_name="Ana", contributor_city="Reno", amount_cents=500
        )
        self.assertEqual(str(c), "Ana from Reno")
        self.assertTrue(c.is_active)

    def test_notification_str_and_defaults(self):
        user = make_buyer(email="n@example.com")
        n = Notification.objects.create(user=user, message="hello")
        self.assertEqual(str(n), f"Notification {n.id} to n@example.com")
        self.assertEqual(n.type, "info")
        self.assertFalse(n.is_read)


class CartModelTests(TestCase):
    def test_cart_str_mentions_user(self):
        buyer = make_buyer(email="c@example.com")
        cart = Cart.objects.create(user=buyer)
        self.assertEqual(str(cart), "Cart of c@example.com")
        self.assertEqual(cart.status, "active")

    def test_cart_item_str_shows_title_and_quantity(self):
        buyer = make_buyer()
        ci = add_cart_item(buyer, make_book(title="Ulysses"), quantity=2)
        self.assertEqual(str(ci), "Ulysses x 2")
        self.assertFalse(ci.is_steward_free)
        self.assertEqual(ci.steward_free_list_price_cents, 0)

    def test_cart_item_same_book_twice_raises_integrity_error(self):
        buyer = make_buyer()
        book = make_book()
        add_cart_item(buyer, book)
        with self.assertRaises(IntegrityError):
            add_cart_item(buyer, book)


class PaymentMethodModelTests(TestCase):
    def test_payment_method_str_shows_brand_last4_email(self):
        buyer = make_buyer(email="p@example.com")
        pm = make_payment_method(buyer, brand="Amex", last4="0005")
        self.assertEqual(str(pm), "Amex *0005 (p@example.com)")

    def test_payment_method_ordering_puts_default_first(self):
        buyer = make_buyer()
        make_payment_method(buyer, is_default=False, last4="1111")
        make_payment_method(buyer, is_default=True, last4="2222")
        make_payment_method(buyer, is_default=False, last4="3333")
        self.assertEqual(PaymentMethod.objects.filter(user=buyer).first().last4, "2222")

    def test_payment_method_related_name_payment_methods(self):
        buyer = make_buyer()
        make_payment_method(buyer)
        self.assertEqual(buyer.payment_methods.count(), 1)


class OrderModelTests(TestCase):
    def test_order_str_mentions_id_and_email(self):
        buyer = make_buyer(email="o@example.com")
        order = make_order(buyer, [(make_book(), 1)])
        self.assertEqual(str(order), f"Order {order.id} - o@example.com")

    def test_order_dollar_properties_convert_cents(self):
        order = make_order(
            make_buyer(), [], subtotal_cents=1000, tax_cents=70, fees_cents=299,
            discount_cents=50, total_cents=1369, steward_contribution_cents=200,
        )
        self.assertEqual(order.subtotal_dollars, 10.0)
        self.assertEqual(order.tax_dollars, 0.7)
        self.assertEqual(order.fees_dollars, 2.99)
        self.assertEqual(order.discount_dollars, 0.5)
        self.assertEqual(order.total_dollars, 13.69)
        self.assertEqual(order.steward_contribution_dollars, 2.0)

    def test_order_default_status_pending(self):
        order = Order.objects.create(user=make_buyer(), subtotal_cents=0, total_cents=0)
        self.assertEqual(order.status, "pending")

    def test_order_shipping_address_deleted_sets_null(self):
        buyer = make_buyer()
        addr = make_address(buyer)
        order = make_order(buyer, [], shipping_address=addr)
        addr.delete()
        order.refresh_from_db()
        self.assertIsNone(order.shipping_address)

    def test_order_payment_method_deleted_sets_null(self):
        buyer = make_buyer()
        pm = make_payment_method(buyer)
        order = make_order(buyer, [], payment_method=pm)
        pm.delete()
        order.refresh_from_db()
        self.assertIsNone(order.payment_method)


class OrderItemAndSnapshotTests(TestCase):
    def setUp(self):
        self.buyer = make_buyer()
        self.book = make_book(title="Snap")
        self.order = make_order(self.buyer, [(self.book, 2)])
        self.oi = self.order.order_items.get()

    def test_order_item_str_mentions_order(self):
        self.assertEqual(str(self.oi), f"OrderItem {self.oi.id} (Order {self.order.id})")

    def test_order_item_deposit_amount_converts_cents(self):
        self.oi.deposit_amount_cents = 1250
        self.assertEqual(self.oi.deposit_amount, 12.5)

    def test_order_item_related_name_order_items(self):
        self.assertEqual(self.order.order_items.count(), 1)

    def test_book_snapshot_str(self):
        snap = self.oi.book_snapshot
        self.assertEqual(str(snap), f"Book snapshot for OrderItem {self.oi.id}")

    def test_shipping_snapshot_str(self):
        snap = self.order.shipping_snapshot
        self.assertEqual(str(snap), f"Shipping snapshot for Order {self.order.id}")

    def test_book_snapshot_survives_source_book_edit(self):
        self.book.title = "Renamed"
        self.book.save()
        self.oi.book_snapshot.refresh_from_db()
        self.assertEqual(self.oi.book_snapshot.title, "Snap")

    def test_order_deleted_cascades_items_and_snapshots(self):
        self.order.delete()
        self.assertFalse(OrderItem.objects.exists())
        self.assertFalse(OrderItemBookSnapshot.objects.exists())
        self.assertFalse(OrderShippingAddress.objects.exists())


class ReturnModelsTests(TestCase):
    def setUp(self):
        self.seller = make_seller()
        self.order = make_order(make_buyer(), [(make_book(self.seller), 1)])

    def test_return_request_str_and_default_status(self):
        rr = ReturnRequest.objects.create(order=self.order, reason="bad")
        self.assertEqual(str(rr), f"ReturnRequest for Order {self.order.id}")
        self.assertEqual(rr.status, "requested")

    def test_return_request_one_per_order(self):
        make_return(self.order)
        with self.assertRaises(IntegrityError):
            make_return(self.order)

    def test_seller_return_receipt_str(self):
        rr = make_return(self.order)
        r = SellerReturnReceipt.objects.create(return_request=rr, seller=self.seller, amount_credited_cents=10)
        self.assertEqual(str(r), f"SellerReturnReceipt rr={rr.id} seller={self.seller.id}")

    def test_seller_return_receipt_duplicate_seller_raises_integrity_error(self):
        rr = make_return(self.order)
        SellerReturnReceipt.objects.create(return_request=rr, seller=self.seller, amount_credited_cents=10)
        with self.assertRaises(IntegrityError):
            SellerReturnReceipt.objects.create(return_request=rr, seller=self.seller, amount_credited_cents=10)


class ReviewModelTests(TestCase):
    def test_review_duplicate_order_item_raises_integrity_error(self):
        buyer = make_buyer()
        order = make_order(buyer, [(make_book(), 1)])
        oi = order.order_items.get()
        make_review(buyer, oi)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_review(buyer, oi, rating=2)

    def test_review_optional_order_item_and_blank_text(self):
        review = Review.objects.create(user=make_buyer(), book=make_book(), rating=5)
        self.assertIsNone(review.order_item)
        self.assertEqual(review.review_text, "")


class SellerProfileModelTests(TestCase):
    def test_seller_profile_str_and_webhook_defaults(self):
        seller = make_seller(email="s@example.com")
        profile = SellerProfile.objects.get(user=seller)
        self.assertEqual(str(profile), "SellerProfile: s@example.com")
        self.assertFalse(profile.webhook_enabled)
        self.assertEqual(profile.webhook_url, "")
        self.assertFalse(profile.is_verified)


class FlagReportModelTests(TestCase):
    def test_flag_report_str_and_default_status(self):
        flag = FlagReport.objects.create(reporter_user=make_buyer(), flag_type="spam")
        self.assertEqual(str(flag), f"FlagReport {flag.id} open")

    def test_flag_report_target_book_deleted_sets_null(self):
        book = make_book(make_seller())
        flag = make_flag(make_buyer(), book=book)
        book.delete()
        flag.refresh_from_db()
        self.assertIsNone(flag.target_book)

    def test_flag_report_related_names(self):
        reporter = make_buyer()
        seller = make_seller()
        make_flag(reporter, book=make_book(seller))
        self.assertEqual(reporter.flags_made.count(), 1)
        self.assertEqual(seller.flags_received.count(), 1)
