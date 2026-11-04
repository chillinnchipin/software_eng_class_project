"""Multi-component integration tests covering auth, cart/checkout, inventory, reviews/returns, and admin flows."""

from django.test import TestCase
from django.urls import reverse

from Admin.models import FlagReport
from Buyer.cart_helpers import add_book_to_db_cart, db_cart_lines
from Buyer.models import CartItem, Order, ReturnRequest, Review, SellerReturnReceipt
from General.models import Address, Book, StewardPool, User
from General.steward import get_steward_pool, user_free_book_eligible
from tests.helpers import (
    DEFAULT_PASSWORD,
    create_address,
    create_admin_user,
    create_book,
    create_buyer,
    create_payment_method,
    create_seller,
    ensure_steward_pool,
)


class AuthAndPermissionsIntegrationTests(TestCase):
    """Integration tests for authentication, session cart merge, JWT, and cross-role permissions."""

    def setUp(self):
        self.seller = create_seller(email="int_seller@example.com", seller_approved=True)
        self.book = create_book(seller=self.seller, title="Operating System Concepts", base_price_cents=4000)

    def test_session_cart_merges_into_db_cart_upon_buyer_login_and_sets_jwt_cookies(self):
        """Items in session['cart'] are persisted to CartItem when an approved buyer logs in."""
        buyer = create_buyer(email="merge_buyer@example.com", buyer_approved=True)
        session = self.client.session
        session["cart"] = {str(self.book.id): {"quantity": 2}}
        session.save()

        resp = self.client.post(
            reverse("login"),
            {"username": "merge_buyer@example.com", "password": DEFAULT_PASSWORD},
        )
        self.assertRedirects(resp, reverse("buyer_home"))
        self.assertIn("access_token", resp.cookies)
        self.assertIn("refresh_token", resp.cookies)

        lines, subtotal = db_cart_lines(buyer)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["quantity"], 2)
        self.assertEqual(subtotal, 8000)
        self.assertEqual(self.client.session.get("cart"), {})

    def test_jwt_token_obtain_and_refresh_api_endpoints(self):
        """SimpleJWT /api/token/ and /api/token/refresh/ endpoints issue and refresh tokens."""
        create_buyer(email="jwt_api@example.com", buyer_approved=True)
        resp = self.client.post(
            reverse("token_obtain_pair"),
            {"email": "jwt_api@example.com", "password": DEFAULT_PASSWORD},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access", data)
        self.assertIn("refresh", data)

        refresh_resp = self.client.post(
            reverse("token_refresh"),
            {"refresh": data["refresh"]},
        )
        self.assertEqual(refresh_resp.status_code, 200)
        self.assertIn("access", refresh_resp.json())

    def test_cross_role_access_restrictions_enforced_across_apps(self):
        """Buyers cannot access Seller or Admin views; Sellers cannot access Admin views."""
        buyer = create_buyer(email="role_buyer@example.com", buyer_approved=True)
        self.client.force_login(buyer)

        # Buyer blocked from Seller views
        self.assertRedirects(self.client.get(reverse("add_books")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("manage_inventory")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("seller_orders")), reverse("buyer_home"))

        # Buyer blocked from Admin views (and logged out by staff_required)
        self.assertRedirects(
            self.client.get(reverse("admin_dashboard")),
            reverse("staff_login"),
            fetch_redirect_response=False,
        )

        # Seller blocked from Admin views
        self.client.force_login(self.seller)
        self.assertRedirects(
            self.client.get(reverse("seller_approvals")),
            reverse("staff_login"),
            fetch_redirect_response=False,
        )


class CartCheckoutInventoryStewardIntegrationTests(TestCase):
    """Integration tests for cart, checkout, snapshots, inventory updates, and steward free books."""

    def setUp(self):
        ensure_steward_pool(100_000_000)
        self.seller = create_seller(email="chk_seller@example.com", seller_approved=True)
        self.buyer = create_buyer(email="chk_buyer@example.com", buyer_approved=True)
        self.book = create_book(
            seller=self.seller,
            title="Computer Networks",
            author="Andrew S. Tanenbaum",
            base_price_cents=5000,
            quantity_available=3,
        )
        self.addr = create_address(self.buyer, line1="100 Main St")
        self.pm = create_payment_method(self.buyer)

    def test_checkout_deducts_inventory_creates_snapshots_and_preserves_order_history_after_edits(self):
        """
        Full checkout workflow:
        1. Buyer adds 2 copies to cart and checks out with a $5.00 steward contribution.
        2. Inventory decreases from 3 to 1; StewardPool increases by 500 cents; buyer gains 50 steward points.
        3. Seller later renames the book and changes price; buyer edits address.
        4. Order detail still displays original book snapshot title/price and shipping snapshot address.
        """
        self.client.force_login(self.buyer)
        self.client.post(reverse("add_to_cart", kwargs={"book_id": self.book.id}), {"quantity": 2})

        pool_before = get_steward_pool().pool_cents
        resp = self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": self.addr.id,
                "payment_method_id": self.pm.id,
                "steward_contribution": "5.00",
            },
        )
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(resp, reverse("order_confirmation", kwargs={"order_id": order.id}))

        # Verify inventory deduction
        self.book.inventory.refresh_from_db()
        self.assertEqual(self.book.inventory.quantity_available, 1)

        # Verify steward pool and buyer progress
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.steward_progress, 50)
        self.assertEqual(get_steward_pool().pool_cents, pool_before + 500)

        # Seller updates book title and price; buyer updates saved address
        self.book.title = "Computer Networks (6th Edition)"
        self.book.base_price_cents = 9900
        self.book.save()
        self.addr.line1 = "999 Changed Blvd"
        self.addr.save()

        # Order detail still shows snapshot values
        detail_resp = self.client.get(reverse("order_detail", kwargs={"order_id": order.id}))
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.context["order_items"][0]["title"], "Computer Networks")
        self.assertEqual(detail_resp.context["order_items"][0]["unit_price_cents"], 5000)
        self.assertEqual(detail_resp.context["shipping"].shipping_line1, "100 Main St")

    def test_steward_free_book_redemption_deducts_from_pool_waives_fees_and_starts_cooldown(self):
        """Verified steward selects a free book in cart, checks out for $0, deducts list price from pool, and enters 30d cooldown."""
        self.buyer.steward_verified = True
        self.buyer.steward_progress = 100
        self.buyer.steward_city = "Austin"
        self.buyer.save()

        self.client.force_login(self.buyer)
        self.client.post(reverse("add_to_cart", kwargs={"book_id": self.book.id}), {"quantity": 1})
        self.client.post(reverse("cart_steward_free_select", kwargs={"book_id": self.book.id}))

        pool_before = get_steward_pool().pool_cents
        resp = self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": self.addr.id,
                "payment_method_id": self.pm.id,
            },
        )
        order = Order.objects.get(user=self.buyer)
        self.assertRedirects(resp, reverse("order_confirmation", kwargs={"order_id": order.id}))
        self.assertEqual(order.subtotal_cents, 0)
        self.assertEqual(order.tax_cents, 0)
        self.assertEqual(order.fees_cents, 0)
        self.assertEqual(order.total_cents, 0)
        self.assertEqual(get_steward_pool().pool_cents, pool_before - 5000)

        self.buyer.refresh_from_db()
        self.assertIsNotNone(self.buyer.last_free_book_redeemed_at)
        self.assertFalse(user_free_book_eligible(self.buyer))


class ReviewAndMultiSellerReturnIntegrationTests(TestCase):
    """Integration tests for order delivery, book review, and multi-seller return receipt workflow."""

    def setUp(self):
        ensure_steward_pool()
        self.seller1 = create_seller(email="s1_ret@example.com", seller_approved=True)
        self.seller2 = create_seller(email="s2_ret@example.com", seller_approved=True)
        self.buyer = create_buyer(email="b_ret@example.com", buyer_approved=True)
        self.book1 = create_book(seller=self.seller1, title="Book Alpha", base_price_cents=2000, quantity_available=5)
        self.book2 = create_book(seller=self.seller2, title="Book Beta", base_price_cents=3000, quantity_available=5)
        self.addr = create_address(self.buyer)
        self.pm = create_payment_method(self.buyer)

    def test_multi_seller_order_delivery_review_and_staged_return_completion(self):
        """
        1. Buyer purchases Book Alpha (seller1) and Book Beta (seller2) in a single order.
        2. Seller1 updates order status to 'delivered'.
        3. Buyer submits a 5-star review on Book Alpha; review appears on book_detail and seller1 dashboard.
        4. Buyer submits a ReturnRequest for the order.
        5. Seller1 marks book_received -> buyer gets $20.00 store credit, Book Alpha stock +1, return stays 'requested'.
        6. Seller2 marks book_received -> buyer gets +$30.00 ($50.00 total) store credit, Book Beta stock +1, return becomes 'refunded'.
        """
        self.client.force_login(self.buyer)
        add_book_to_db_cart(self.buyer, self.book1, 1)
        add_book_to_db_cart(self.buyer, self.book2, 1)
        self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": self.addr.id,
                "payment_method_id": self.pm.id,
                "steward_contribution": "0",
            },
        )
        order = Order.objects.get(user=self.buyer)
        item1 = order.order_items.get(book=self.book1)

        # Seller1 marks order delivered
        self.client.force_login(self.seller1)
        self.client.post(
            reverse("update_order_status", kwargs={"order_id": order.id}),
            {"status": "delivered"},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")

        # Buyer leaves a 5-star review on item1 and submits a return request
        self.client.force_login(self.buyer)
        self.client.post(reverse("review_submission", kwargs={"item_id": item1.id}), {"rating": 5})
        self.assertTrue(Review.objects.filter(order_item=item1, rating=5).exists())

        book_detail_resp = self.client.get(reverse("book_detail", kwargs={"pk": self.book1.id}))
        self.assertEqual(book_detail_resp.context["reviews"].count(), 1)

        self.client.post(
            reverse("return_request", kwargs={"order_id": order.id}),
            {"reason_category": "wrong_item", "reason_details": "Ordered wrong textbooks"},
        )
        rr = ReturnRequest.objects.get(order=order)
        self.assertEqual(rr.status, "requested")

        # Seller1 marks book received: partial return (Seller2 hasn't received yet)
        self.client.force_login(self.seller1)
        self.client.post(
            reverse("seller_return_request_detail", kwargs={"return_id": rr.id}),
            {"action": "book_received"},
        )
        rr.refresh_from_db()
        self.buyer.refresh_from_db()
        self.book1.inventory.refresh_from_db()
        self.assertEqual(rr.status, "requested")
        self.assertEqual(self.buyer.store_credit_cents, 2000)
        self.assertEqual(self.book1.inventory.quantity_available, 5)

        # Seller2 marks book received: all sellers in order have now acknowledged -> status becomes 'refunded'
        self.client.force_login(self.seller2)
        self.client.post(
            reverse("seller_return_request_detail", kwargs={"return_id": rr.id}),
            {"action": "book_received"},
        )
        rr.refresh_from_db()
        self.buyer.refresh_from_db()
        self.book2.inventory.refresh_from_db()
        self.assertEqual(rr.status, "refunded")
        self.assertEqual(self.buyer.store_credit_cents, 5000)
        self.assertEqual(self.book2.inventory.quantity_available, 5)


class AdminApprovalAndModerationIntegrationTests(TestCase):
    """Integration tests for admin account approvals, flag reporting, book freezing, and user freezing."""

    def setUp(self):
        self.admin = create_admin_user(email="mod_admin@example.com")

    def test_new_buyer_and_seller_registration_to_admin_approval_and_login(self):
        """
        1. New buyer and seller register via /register/.
        2. After logging out, buyer cannot log in until admin approves buyer; seller redirects to buyer_home until approved.
        3. Admin approves both accounts via /staff/staff/buyers/approve/ and /staff/staff/sellers/approve/.
        4. Both can now log in and reach their respective dashboards.
        """
        # Register buyer
        self.client.post(
            reverse("register"),
            {
                "email": "new_b@example.com",
                "first_name": "New",
                "last_name": "Buyer",
                "role": "buyer",
                "password": DEFAULT_PASSWORD,
                "confirm_password": DEFAULT_PASSWORD,
                "security_question": "city",
                "security_answer": "austin",
            },
        )
        self.client.get(reverse("logout"))

        # Unapproved buyer blocked at login
        resp_b_blocked = self.client.post(
            reverse("login"), {"username": "new_b@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_b_blocked, reverse("login"))

        # Register seller
        self.client.post(
            reverse("register"),
            {
                "email": "new_s@example.com",
                "first_name": "New",
                "last_name": "Seller",
                "role": "seller",
                "store_name": "New Seller Store",
                "password": DEFAULT_PASSWORD,
                "confirm_password": DEFAULT_PASSWORD,
                "security_question": "city",
                "security_answer": "austin",
            },
        )
        self.client.get(reverse("logout"))

        buyer = User.objects.get(email="new_b@example.com")
        seller = User.objects.get(email="new_s@example.com")

        # Admin approves both
        self.client.force_login(self.admin)
        self.client.post(reverse("approve_buyer", kwargs={"user_id": buyer.id}))
        self.client.post(reverse("approve_seller", kwargs={"user_id": seller.id}))
        self.client.get(reverse("staff_logout"))

        # Approved buyer logs in to buyer_home
        resp_b_ok = self.client.post(
            reverse("login"), {"username": "new_b@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_b_ok, reverse("buyer_home"))
        self.client.get(reverse("logout"))

        # Approved seller logs in to seller_home
        resp_s_ok = self.client.post(
            reverse("login"), {"username": "new_s@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_s_ok, reverse("seller_home"))

    def test_flag_report_to_admin_freeze_book_and_freeze_user(self):
        """
        1. Buyer flags a book listing.
        2. Admin inspects reports_flags and freezes the book -> book is hidden from catalog and removed from buyer cart.
        3. Admin freezes the seller user -> seller can no longer authenticate via login.
        """
        seller = create_seller(email="bad_seller@example.com", seller_approved=True)
        buyer = create_buyer(email="good_buyer@example.com", buyer_approved=True)
        book = create_book(seller=seller, title="Pirated Textbook", base_price_cents=1000)

        self.client.force_login(buyer)
        add_book_to_db_cart(buyer, book, 1)
        self.client.post(
            reverse("flag_book", kwargs={"pk": book.id}),
            {"flag_type": "counterfeit", "details": "Photocopied pages"},
        )
        self.assertEqual(FlagReport.objects.count(), 1)

        # Admin views flags and freezes book and seller
        self.client.force_login(self.admin)
        flags_resp = self.client.get(reverse("reports_flags"))
        self.assertEqual(len(flags_resp.context["all_flags"]), 1)

        self.client.post(reverse("toggle_book_freeze", kwargs={"book_id": book.id}))
        self.client.post(reverse("toggle_user_freeze", kwargs={"user_id": seller.id}))

        # Book is now inactive: 404 on book_detail and purged from buyer's cart lines
        self.client.force_login(buyer)
        self.assertEqual(self.client.get(reverse("book_detail", kwargs={"pk": book.id})).status_code, 404)
        lines, _ = db_cart_lines(buyer)
        self.assertEqual(lines, [])

        # Frozen seller cannot log in
        self.client.logout()
        resp_frozen = self.client.post(
            reverse("login"), {"username": "bad_seller@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertContains(resp_frozen, "Invalid email or password.")
