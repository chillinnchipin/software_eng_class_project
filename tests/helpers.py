"""Shared factories and base test cases for the PassItOnBooks test suite.

Factories are plain functions (no third-party factory library is installed) that
create rows with sensible defaults; pass keyword overrides for anything that matters
to the test.
"""

import itertools

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from Admin.models import FlagReport
from Buyer.cart_helpers import get_or_create_cart
from Buyer.models import (
    CartItem,
    Order,
    OrderItem,
    OrderItemBookSnapshot,
    OrderShippingAddress,
    PaymentMethod,
    ReturnRequest,
    Review,
)
from General.models import Address, Book, Inventory, StewardPool, User
from Seller.models import SellerProfile

PASSWORD = "Sup3r-secret-pw"
SECURITY_ANSWER = "Springfield"

_seq = itertools.count(1)


def _n():
    return next(_seq)


def make_user(role="buyer", *, email=None, password=PASSWORD, approved=True, **extra):
    """Create a user. Buyers/sellers are approved by default so they can log in."""
    n = _n()
    email = email or f"{role}{n}@example.com"
    defaults = {"first_name": f"First{n}", "last_name": f"Last{n}", "role": role}
    if role == "buyer":
        defaults["buyer_approved"] = approved
    elif role == "seller":
        defaults["seller_approved"] = approved
    defaults.update(extra)
    return User.objects.create_user(email=email, password=password, **defaults)


def make_buyer(**kw):
    return make_user("buyer", **kw)


def make_seller(*, store_name="Test Store", **kw):
    user = make_user("seller", **kw)
    SellerProfile.objects.get_or_create(user=user, defaults={"store_name": store_name})
    return user


def make_admin(**kw):
    kw.setdefault("is_staff", True)
    return make_user("admin", **kw)


def make_steward(**kw):
    kw.setdefault("steward_verified", True)
    kw.setdefault("steward_progress", 100)
    kw.setdefault("steward_city", "Austin")
    return make_buyer(**kw)


def set_security_question(user, question="In what city were you born?", answer=SECURITY_ANSWER):
    user.security_question = question
    user.security_answer_hash = make_password(answer.strip().lower())
    user.save(update_fields=["security_question", "security_answer_hash"])
    return user


def make_book(seller=None, *, stock=5, price_cents=1500, with_inventory=True, **extra):
    n = _n()
    fields = {
        "title": f"Book Title {n}",
        "author": f"Author {n}",
        "condition": "Good",
        "base_price_cents": price_cents,
        "description": f"Description {n}",
        "isbn": f"978000000{n:04d}",
        "is_active": True,
    }
    fields.update(extra)
    book = Book.objects.create(seller_user=seller, **fields)
    if with_inventory:
        Inventory.objects.create(book=book, quantity_available=stock)
    return book


def make_address(user, *, is_default=True, **extra):
    fields = {
        "label": "Home",
        "line1": "1 Main St",
        "city": "Springfield",
        "state": "IL",
        "postal_code": "62701",
        "country": "US",
        "is_default": is_default,
    }
    fields.update(extra)
    return Address.objects.create(user=user, **fields)


def make_payment_method(user, *, is_default=True, **extra):
    fields = {
        "cardholder_name": "Card Holder",
        "brand": "Visa",
        "last4": "4242",
        "exp_month": 12,
        "exp_year": 2099,
        "is_default": is_default,
    }
    fields.update(extra)
    return PaymentMethod.objects.create(user=user, **fields)


def add_cart_item(user, book, *, quantity=1, **extra):
    cart = get_or_create_cart(user)
    fields = {"quantity": quantity, "unit_price_cents": book.base_price_cents}
    fields.update(extra)
    return CartItem.objects.create(cart=cart, book=book, **fields)


def make_order(buyer, items=(), *, status="paid", with_snapshots=True, **extra):
    """Create an order with OrderItems. ``items`` is a sequence of (book, quantity)."""
    items = list(items)
    subtotal = sum(b.base_price_cents * q for b, q in items)
    fields = {
        "status": status,
        "subtotal_cents": subtotal,
        "tax_cents": 0,
        "fees_cents": 0,
        "total_cents": subtotal,
    }
    fields.update(extra)
    order = Order.objects.create(user=buyer, **fields)
    for book, qty in items:
        oi = OrderItem.objects.create(
            order=order,
            book=book,
            title=book.title,
            author=book.author,
            quantity=qty,
            unit_price_cents=book.base_price_cents,
            line_total_cents=book.base_price_cents * qty,
        )
        if with_snapshots:
            OrderItemBookSnapshot.objects.create(
                order_item=oi, source_book=book, title=book.title,
                author=book.author, condition=book.condition,
            )
    if with_snapshots:
        OrderShippingAddress.objects.create(
            order=order, shipping_name="Buyer", shipping_line1="1 Main St",
            shipping_city="Springfield", shipping_state="IL",
            shipping_postal_code="62701", shipping_country="US",
        )
    return order


def make_return(order, *, reason="[damaged] cover torn", status="requested"):
    return ReturnRequest.objects.create(order=order, reason=reason, status=status)


def make_review(user, order_item, *, rating=4, **extra):
    return Review.objects.create(
        user=user, book=order_item.book, order_item=order_item, rating=rating, **extra
    )


def make_flag(reporter, *, book=None, target=None, flag_type="other", status="open", details="spam"):
    return FlagReport.objects.create(
        reporter_user=reporter,
        target_user=target if target is not None else (book.seller_user if book else None),
        target_book=book,
        flag_type=flag_type,
        status=status,
        details=details,
    )


def ensure_pool(cents=100_000_000):
    """The data migration seeds the pool; tests that tweak the balance call this."""
    pool, _ = StewardPool.objects.get_or_create(pk=1, defaults={"pool_cents": cents})
    if pool.pool_cents != cents:
        pool.pool_cents = cents
        pool.save()
    return pool


def messages_text(response):
    """All django.contrib.messages attached to a response, as plain strings."""
    return [str(m) for m in response.wsgi_request._messages] if hasattr(response, "wsgi_request") else []


class PassItOnTestCase(TestCase):
    """TestCase with login helpers shared by view, integration, and use-case tests."""

    def login(self, user):
        self.client.force_login(user)
        return user

    def login_via_form(self, user, password=PASSWORD, **extra):
        return self.client.post("/login/", {"username": user.email, "password": password, **extra})

    def assertMessage(self, response, fragment):
        texts = messages_text(response)
        self.assertTrue(
            any(fragment in t for t in texts),
            f"No message containing {fragment!r}; got {texts!r}",
        )
