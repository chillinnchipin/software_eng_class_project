"""Shared test factories and helper utilities for PassItOnBooks tests."""

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.utils import timezone

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
from General.models import (
    Address,
    Book,
    Inventory,
    Notification,
    StewardContribution,
    StewardPool,
)
from General.steward import INITIAL_POOL_CENTS
from Seller.models import SellerProfile

User = get_user_model()

DEFAULT_PASSWORD = "TestPass123!"
DEFAULT_SECURITY_QUESTION = "In what city were you born?"
DEFAULT_SECURITY_ANSWER = "austin"


def ensure_steward_pool(pool_cents: int = INITIAL_POOL_CENTS) -> StewardPool:
    """Ensure the singleton StewardPool(pk=1) exists with the specified balance."""
    pool, _ = StewardPool.objects.update_or_create(
        pk=1, defaults={"pool_cents": pool_cents}
    )
    return pool


def create_buyer(
    email: str = "buyer@example.com",
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Alice",
    last_name: str = "Buyer",
    buyer_approved: bool = True,
    steward_verified: bool = False,
    steward_progress: int = 0,
    steward_city: str | None = None,
    store_credit_cents: int = 0,
    **extra_fields,
) -> User:
    """Create and return a User with role='buyer'."""
    user = User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role="buyer",
        buyer_approved=buyer_approved,
        buyer_approved_at=timezone.now() if buyer_approved else None,
        steward_verified=steward_verified,
        steward_progress=steward_progress,
        steward_city=steward_city,
        store_credit_cents=store_credit_cents,
        security_question=extra_fields.pop("security_question", DEFAULT_SECURITY_QUESTION),
        security_answer_hash=make_password(
            extra_fields.pop("security_answer", DEFAULT_SECURITY_ANSWER).strip().lower()
        ),
        **extra_fields,
    )
    return user


def create_seller(
    email: str = "seller@example.com",
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Bob",
    last_name: str = "Seller",
    seller_approved: bool = True,
    store_name: str = "Bob's Books",
    webhook_enabled: bool = False,
    webhook_url: str = "",
    webhook_secret: str = "",
    **extra_fields,
) -> User:
    """Create and return a User with role='seller' and an associated SellerProfile."""
    user = User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role="seller",
        seller_approved=seller_approved,
        seller_approved_at=timezone.now() if seller_approved else None,
        security_question=extra_fields.pop("security_question", DEFAULT_SECURITY_QUESTION),
        security_answer_hash=make_password(
            extra_fields.pop("security_answer", DEFAULT_SECURITY_ANSWER).strip().lower()
        ),
        **extra_fields,
    )
    SellerProfile.objects.update_or_create(
        user=user,
        defaults={
            "store_name": store_name,
            "is_verified": seller_approved,
            "webhook_enabled": webhook_enabled,
            "webhook_url": webhook_url,
            "webhook_secret": webhook_secret,
        },
    )
    return user


def create_admin_user(
    email: str = "admin@example.com",
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Carol",
    last_name: str = "Admin",
    is_staff: bool = True,
    is_superuser: bool = True,
    **extra_fields,
) -> User:
    """Create and return an admin staff user."""
    return User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role="admin",
        is_staff=is_staff,
        is_superuser=is_superuser,
        security_question=extra_fields.pop("security_question", DEFAULT_SECURITY_QUESTION),
        security_answer_hash=make_password(
            extra_fields.pop("security_answer", DEFAULT_SECURITY_ANSWER).strip().lower()
        ),
        **extra_fields,
    )


def create_book(
    seller: User | None = None,
    title: str = "Clean Code",
    author: str = "Robert C. Martin",
    description: str = "A handbook of agile software craftsmanship.",
    isbn: str = "9780132350884",
    language: str = "English",
    publisher: str = "Prentice Hall",
    publication_year: int = 2008,
    condition: str = "Good",
    base_price_cents: int = 2500,
    is_active: bool = True,
    quantity_available: int | None = 5,
    quantity_reserved: int = 0,
    reorder_threshold: int = 1,
) -> Book:
    """Create a Book and optionally its Inventory record (if quantity_available is not None)."""
    book = Book.objects.create(
        seller_user=seller,
        title=title,
        author=author,
        description=description,
        isbn=isbn,
        language=language,
        publisher=publisher,
        publication_year=publication_year,
        condition=condition,
        base_price_cents=base_price_cents,
        is_active=is_active,
    )
    if quantity_available is not None:
        Inventory.objects.create(
            book=book,
            quantity_available=quantity_available,
            quantity_reserved=quantity_reserved,
            reorder_threshold=reorder_threshold,
        )
    return book


def create_address(
    user: User,
    label: str = "Home",
    line1: str = "123 University Ave",
    line2: str | None = "Apt 4B",
    city: str = "Austin",
    state: str = "TX",
    postal_code: str = "78701",
    country: str = "USA",
    is_default: bool = True,
) -> Address:
    """Create a saved shipping Address for a user."""
    return Address.objects.create(
        user=user,
        label=label,
        line1=line1,
        line2=line2,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
        is_default=is_default,
    )


def create_payment_method(
    user: User,
    cardholder_name: str = "Alice Buyer",
    brand: str = "Visa",
    last4: str = "4242",
    exp_month: int = 12,
    exp_year: int = 2030,
    is_default: bool = True,
) -> PaymentMethod:
    """Create a saved PaymentMethod for a user."""
    return PaymentMethod.objects.create(
        user=user,
        cardholder_name=cardholder_name,
        brand=brand,
        last4=last4,
        exp_month=exp_month,
        exp_year=exp_year,
        is_default=is_default,
    )


def create_order_with_item(
    buyer: User,
    book: Book,
    quantity: int = 1,
    status: str = "paid",
    unit_price_cents: int | None = None,
    is_steward_free: bool = False,
    steward_contribution_cents: int = 200,
    create_snapshots: bool = True,
    address: Address | None = None,
    payment_method: PaymentMethod | None = None,
) -> tuple[Order, OrderItem]:
    """Create an Order with a single OrderItem and optional shipping/book snapshots."""
    if address is None:
        address = create_address(buyer)
    if payment_method is None:
        payment_method = create_payment_method(buyer)
    if unit_price_cents is None:
        unit_price_cents = 0 if is_steward_free else book.base_price_cents
    line_total_cents = unit_price_cents * quantity
    tax_cents = int(round(line_total_cents * 0.07))
    fees_cents = 0 if is_steward_free else 299
    total_cents = line_total_cents + tax_cents + fees_cents + steward_contribution_cents

    order = Order.objects.create(
        user=buyer,
        shipping_address=address,
        payment_method=payment_method,
        status=status,
        subtotal_cents=line_total_cents,
        tax_cents=tax_cents,
        fees_cents=fees_cents,
        discount_cents=0,
        steward_contribution_cents=steward_contribution_cents,
        total_cents=total_cents,
    )
    order_item = OrderItem.objects.create(
        order=order,
        book=book,
        title=book.title,
        author=book.author,
        quantity=quantity,
        unit_price_cents=unit_price_cents,
        line_total_cents=line_total_cents,
        is_steward_free=is_steward_free,
    )
    if create_snapshots:
        OrderShippingAddress.objects.create(
            order=order,
            shipping_name=address.label or buyer.email,
            shipping_line1=address.line1,
            shipping_line2=address.line2,
            shipping_city=address.city,
            shipping_state=address.state,
            shipping_postal_code=address.postal_code,
            shipping_country=address.country,
            source_address=address,
        )
        OrderItemBookSnapshot.objects.create(
            order_item=order_item,
            source_book=book,
            title=book.title,
            author=book.author,
            description=book.description,
            isbn=book.isbn,
            language=book.language,
            publisher=book.publisher,
            publication_year=book.publication_year,
            cover_image_url=book.cover_image_url,
            condition=book.condition,
        )
    return order, order_item
