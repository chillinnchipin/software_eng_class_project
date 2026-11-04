"""Unit tests for all models across General, Buyer, Seller, and Admin apps."""

from io import StringIO
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
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
    create_address,
    create_buyer,
    create_book,
    create_order_with_item,
    create_payment_method,
    create_seller,
)


class UserModelUnitTests(TestCase):
    """Unit tests for UserManager and User model."""

    def test_create_user_valid_email_normalizes_and_hashes_password(self):
        """UserManager.create_user normalizes email domain and hashes password."""
        user = User.objects.create_user(
            email="Student@EXAMPLE.COM",
            password="SecretPassword123!",
            first_name="Jane",
            last_name="Doe",
            role="buyer",
        )
        self.assertEqual(user.email, "Student@example.com")
        self.assertTrue(user.check_password("SecretPassword123!"))
        self.assertEqual(str(user), "Student@example.com")
        self.assertFalse(user.is_steward)

    def test_create_user_empty_email_raises_value_error(self):
        """UserManager.create_user raises ValueError when email is empty."""
        with self.assertRaisesMessage(ValueError, "Users must have an email address"):
            User.objects.create_user(email="", password="SecretPassword123!")

    def test_create_superuser_defaults_sets_admin_staff_superuser(self):
        """UserManager.create_superuser sets role='admin', is_staff=True, is_superuser=True."""
        su = User.objects.create_superuser(
            email="root@example.com",
            password="RootPassword123!",
            first_name="Root",
            last_name="Admin",
        )
        self.assertTrue(su.is_staff)
        self.assertTrue(su.is_superuser)
        self.assertEqual(su.role, "admin")

    def test_create_superuser_is_staff_false_raises_value_error(self):
        """UserManager.create_superuser rejects is_staff=False."""
        with self.assertRaisesMessage(ValueError, "Superuser must have is_staff=True."):
            User.objects.create_superuser(
                email="badstaff@example.com",
                password="RootPassword123!",
                is_staff=False,
            )

    def test_create_superuser_is_superuser_false_raises_value_error(self):
        """UserManager.create_superuser rejects is_superuser=False."""
        with self.assertRaisesMessage(ValueError, "Superuser must have is_superuser=True."):
            User.objects.create_superuser(
                email="badsu@example.com",
                password="RootPassword123!",
                is_superuser=False,
            )

    def test_user_is_steward_property_reflects_steward_verified(self):
        """User.is_steward returns True only when steward_verified is True."""
        user = create_buyer(email="steward_prop@example.com", steward_verified=False)
        self.assertFalse(user.is_steward)
        user.steward_verified = True
        self.assertTrue(user.is_steward)

    def test_user_steward_progress_above_100_fails_full_clean(self):
        """User.steward_progress has MaxValueValidator(100)."""
        user = create_buyer(email="overprogress@example.com", steward_progress=105)
        with self.assertRaises(ValidationError):
            user.full_clean()


class GeneralModelsUnitTests(TestCase):
    """Unit tests for Address, Book, Inventory, StewardPool, StewardContribution, Notification."""

    def test_address_str_returns_email_and_label(self):
        buyer = create_buyer(email="addr@example.com")
        addr = create_address(buyer, label="Dorm")
        self.assertEqual(str(addr), "addr@example.com - Dorm")

    def test_book_str_and_price_dollars_and_stock_quantity_with_inventory(self):
        seller = create_seller(email="book_seller@example.com")
        book = create_book(
            seller=seller,
            title="Algorithms",
            base_price_cents=4999,
            quantity_available=7,
        )
        self.assertEqual(str(book), "Algorithms")
        self.assertAlmostEqual(book.price_dollars, 49.99)
        self.assertEqual(book.stock_quantity, 7)
        self.assertEqual(str(book.inventory), "Algorithms inventory")

    def test_book_stock_quantity_without_inventory_returns_zero(self):
        """Book.stock_quantity catches Inventory.DoesNotExist and returns 0."""
        book = create_book(title="No Inventory Book", quantity_available=None)
        self.assertEqual(book.stock_quantity, 0)

    def test_steward_pool_save_enforces_singleton_pk_one(self):
        """StewardPool.save always forces pk=1 regardless of initial pk."""
        pool = StewardPool(pk=99, pool_cents=50_000)
        pool.save()
        self.assertEqual(pool.pk, 1)
        self.assertEqual(StewardPool.objects.get(pk=1).pool_cents, 50_000)

    def test_steward_pool_admin_pool_dollars_formats_currency(self):
        """StewardPoolAdmin.pool_dollars formats cents into dollar string."""
        pool = StewardPool(pk=1, pool_cents=123_456)
        admin_obj = StewardPoolAdmin(StewardPool, AdminSite())
        self.assertEqual(admin_obj.pool_dollars(pool), "$1,234.56")

    def test_steward_contribution_str_formats_contributor_and_city(self):
        buyer = create_buyer(email="contrib@example.com")
        contrib = StewardContribution.objects.create(
            steward_user=buyer,
            contributor_name="Sam",
            contributor_city="Dallas",
            amount_cents=500,
        )
        self.assertEqual(str(contrib), "Sam from Dallas")

    def test_notification_str_formats_id_and_user_email(self):
        buyer = create_buyer(email="notify@example.com")
        notif = Notification.objects.create(
            user=buyer,
            type="order",
            title="Order Shipped",
            message="Your order is on the way.",
        )
        self.assertEqual(str(notif), f"Notification {notif.id} to notify@example.com")


class BuyerModelsUnitTests(TestCase):
    """Unit tests for Cart, CartItem, PaymentMethod, Order, OrderItem, snapshots, ReturnRequest, Review."""

    def setUp(self):
        self.buyer = create_buyer(email="buyermodel@example.com")
        self.seller = create_seller(email="sellermodel@example.com")
        self.book = create_book(seller=self.seller, title="Refactoring", base_price_cents=3000)

    def test_cart_and_cart_item_str_and_unique_constraint(self):
        """CartItem enforces unique (cart, book) constraint."""
        cart = Cart.objects.create(user=self.buyer)
        self.assertEqual(str(cart), "Cart of buyermodel@example.com")
        ci = CartItem.objects.create(
            cart=cart,
            book=self.book,
            quantity=2,
            unit_price_cents=3000,
        )
        self.assertEqual(str(ci), "Refactoring x 2")
        with self.assertRaises(IntegrityError):
            CartItem.objects.create(
                cart=cart,
                book=self.book,
                quantity=1,
                unit_price_cents=3000,
            )

    def test_payment_method_str_formats_brand_last4_and_email(self):
        pm = create_payment_method(self.buyer, brand="Mastercard", last4="8888")
        self.assertEqual(str(pm), "Mastercard *8888 (buyermodel@example.com)")

    def test_order_and_order_item_dollar_properties_and_str(self):
        """Order and OrderItem expose accurate dollar conversion properties."""
        order, item = create_order_with_item(
            self.buyer,
            self.book,
            quantity=2,
            unit_price_cents=3000,
            steward_contribution_cents=250,
        )
        order.discount_cents = 100
        order.save()
        item.deposit_required = True
        item.deposit_amount_cents = 500
        item.save()

        self.assertEqual(str(order), f"Order {order.id} - buyermodel@example.com")
        self.assertAlmostEqual(order.subtotal_dollars, 60.00)
        self.assertAlmostEqual(order.tax_dollars, 4.20)
        self.assertAlmostEqual(order.fees_dollars, 2.99)
        self.assertAlmostEqual(order.discount_dollars, 1.00)
        self.assertAlmostEqual(order.steward_contribution_dollars, 2.50)
        self.assertAlmostEqual(order.total_dollars, (6000 + 420 + 299 + 250) / 100.0)
        self.assertEqual(str(item), f"OrderItem {item.id} (Order {order.id})")
        self.assertAlmostEqual(item.deposit_amount, 5.00)
        self.assertEqual(
            str(order.shipping_snapshot),
            f"Shipping snapshot for Order {order.id}",
        )
        self.assertEqual(
            str(item.book_snapshot),
            f"Book snapshot for OrderItem {item.id}",
        )

    def test_return_request_and_seller_return_receipt_str_and_uniqueness(self):
        """SellerReturnReceipt enforces one receipt per (return_request, seller)."""
        order, _ = create_order_with_item(self.buyer, self.book)
        rr = ReturnRequest.objects.create(order=order, reason="Damaged cover")
        self.assertEqual(str(rr), f"ReturnRequest for Order {order.id}")

        receipt = SellerReturnReceipt.objects.create(
            return_request=rr,
            seller=self.seller,
            amount_credited_cents=3000,
        )
        self.assertEqual(
            str(receipt),
            f"SellerReturnReceipt rr={rr.id} seller={self.seller.id}",
        )
        with self.assertRaises(IntegrityError):
            SellerReturnReceipt.objects.create(
                return_request=rr,
                seller=self.seller,
                amount_credited_cents=3000,
            )

    def test_review_unique_per_user_and_order_item(self):
        """Review enforces unique_together on (user, order_item)."""
        _, item = create_order_with_item(self.buyer, self.book)
        Review.objects.create(
            user=self.buyer,
            book=self.book,
            order_item=item,
            rating=5,
            review_text="Great book!",
        )
        with self.assertRaises(IntegrityError):
            Review.objects.create(
                user=self.buyer,
                book=self.book,
                order_item=item,
                rating=4,
            )


class SellerAndAdminModelsUnitTests(TestCase):
    """Unit tests for SellerProfile, FlagReport, and create_admin management command."""

    def test_seller_profile_str_returns_email(self):
        seller = create_seller(email="profile_seller@example.com")
        profile = SellerProfile.objects.get(user=seller)
        self.assertEqual(str(profile), "SellerProfile: profile_seller@example.com")

    def test_flag_report_str_returns_id_and_status(self):
        buyer = create_buyer(email="reporter@example.com")
        flag = FlagReport.objects.create(
            reporter_user=buyer,
            flag_type="spam",
            details="Suspicious listing",
        )
        self.assertEqual(str(flag), f"FlagReport {flag.id} open")

    def test_create_admin_command_creates_new_admin_and_skips_when_exists(self):
        """create_admin creates an admin user, then skips if an admin already exists."""
        out = StringIO()
        call_command(
            "create_admin",
            email="FirstAdmin@Example.com",
            password="AdminPassword123!",
            first_name="First",
            last_name="Admin",
            stdout=out,
        )
        self.assertIn("Admin user created: firstadmin@example.com", out.getvalue())
        admin_user = User.objects.get(email="firstadmin@example.com")
        self.assertEqual(admin_user.role, "admin")
        self.assertTrue(admin_user.is_staff)

        out2 = StringIO()
        call_command(
            "create_admin",
            email="secondadmin@example.com",
            password="AdminPassword123!",
            stdout=out2,
        )
        self.assertIn("An admin user already exists. Skipping creation.", out2.getvalue())

    def test_create_admin_command_updates_existing_non_admin_user(self):
        """create_admin promotes an existing user with matching email when no admin exists."""
        existing = create_buyer(email="promote@example.com")
        out = StringIO()
        call_command(
            "create_admin",
            email="promote@example.com",
            password="NewAdminPassword123!",
            first_name="Promoted",
            last_name="Staff",
            stdout=out,
        )
        existing.refresh_from_db()
        self.assertEqual(existing.role, "admin")
        self.assertTrue(existing.is_staff)
        self.assertEqual(existing.first_name, "Promoted")
        self.assertIn("Updated existing user promote@example.com to admin.", out.getvalue())
