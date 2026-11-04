"""Unit tests for all views in General, Buyer, Seller, and Admin apps."""

from datetime import timedelta
from pathlib import Path
from unittest import expectedFailure
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from Admin.models import FlagReport
from Buyer.cart_helpers import add_book_to_db_cart, get_or_create_cart
from Buyer.models import CartItem, Order, OrderItem, PaymentMethod, ReturnRequest, Review, SellerReturnReceipt
from General import views as general_views
from General.models import Address, Book, Inventory, StewardContribution, StewardPool, User
from Seller.models import SellerProfile
from tests.helpers import (
    DEFAULT_PASSWORD,
    create_address,
    create_admin_user,
    create_book,
    create_buyer,
    create_order_with_item,
    create_payment_method,
    create_seller,
    ensure_steward_pool,
)


class GeneralViewsUnitTests(TestCase):
    """Unit tests for every view in General/views.py."""

    def setUp(self):
        self.buyer = create_buyer(email="gen_buyer@example.com")
        self.seller = create_seller(email="gen_seller@example.com", seller_approved=True)
        self.admin = create_admin_user(email="gen_admin@example.com")
        self.book = create_book(
            seller=self.seller,
            title="1984",
            author="George Orwell",
            description="Dystopian classic",
            base_price_cents=1999,
            quantity_available=4,
        )

    def test_home_view_renders_login_page(self):
        resp = self.client.get(reverse("general_home"))
        self.assertEqual(resp.status_code, 200)

    def test_serve_book_cover_valid_and_invalid_paths(self):
        """serve_book_cover serves existing image and raises 404 on traversal or missing file."""
        resp = self.client.get(reverse("serve_book_cover", kwargs={"path": "1984.jpg"}))
        self.assertEqual(resp.status_code, 200)
        resp.close()

        resp_missing = self.client.get(reverse("serve_book_cover", kwargs={"path": "nonexistent_cover.jpg"}))
        self.assertEqual(resp_missing.status_code, 404)

        rf = RequestFactory()
        for bad_path in ("", "../manage.py", "sub/file.jpg", "sub\\file.jpg"):
            with self.assertRaises(Http404):
                general_views.serve_book_cover(rf.get("/"), bad_path)

    def test_login_page_get_authenticated_redirects_by_role(self):
        """Authenticated GET on login_page redirects admin, approved/unapproved seller, and buyer."""
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("login")), reverse("admin_dashboard"), fetch_redirect_response=False)

        self.client.force_login(self.seller)
        self.assertRedirects(self.client.get(reverse("login")), reverse("seller_home"), fetch_redirect_response=False)

        unapproved_seller = create_seller(email="unapp_s@example.com", seller_approved=False)
        self.client.force_login(unapproved_seller)
        self.assertRedirects(self.client.get(reverse("login")), reverse("buyer_home"), fetch_redirect_response=False)

        self.client.force_login(self.buyer)
        self.assertRedirects(self.client.get(reverse("login")), reverse("buyer_home"), fetch_redirect_response=False)

    def test_login_page_post_valid_and_invalid_and_unapproved_roles(self):
        """Covers login POST for approved/unapproved buyer, approved/unapproved seller, admin, next_url, invalid."""
        # Invalid credentials
        resp_bad = self.client.post(reverse("login"), {"username": "gen_buyer@example.com", "password": "wrong"})
        self.assertEqual(resp_bad.status_code, 200)
        self.assertContains(resp_bad, "Invalid email or password.")

        # Unapproved buyer blocked on login
        unapp_buyer = create_buyer(email="unapp_b@example.com", buyer_approved=False)
        resp_unapp_b = self.client.post(
            reverse("login"), {"username": "unapp_b@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_unapp_b, reverse("login"))

        # Unapproved seller redirects to buyer_home with message
        unapp_seller = create_seller(email="unapp_s2@example.com", seller_approved=False)
        resp_unapp_s = self.client.post(
            reverse("login"), {"username": "unapp_s2@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_unapp_s, reverse("buyer_home"))
        self.client.logout()

        # Approved seller redirects to seller_home
        resp_seller = self.client.post(
            reverse("login"), {"username": "gen_seller@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_seller, reverse("seller_home"))
        self.client.logout()

        # Admin redirects to admin_dashboard
        resp_admin = self.client.post(
            reverse("login"), {"username": "gen_admin@example.com", "password": DEFAULT_PASSWORD}
        )
        self.assertRedirects(resp_admin, reverse("admin_dashboard"))
        self.client.logout()

        # Approved buyer with next_url redirects to next_url and sets JWT cookies
        resp_next = self.client.post(
            reverse("login"),
            {"username": "gen_buyer@example.com", "password": DEFAULT_PASSWORD, "next": "/catalog/"},
        )
        self.assertRedirects(resp_next, "/catalog/")
        self.assertIn("access_token", resp_next.cookies)

    def test_register_view_get_and_post_errors_and_role_fallback(self):
        """register validates required fields, password rules, duplicate email, and unknown role fallback."""
        self.assertEqual(self.client.get(reverse("register")).status_code, 200)

        # Missing fields and short mismatched passwords and duplicate email
        resp_err = self.client.post(
            reverse("register"),
            {
                "email": "gen_buyer@example.com",
                "first_name": "",
                "last_name": "",
                "password": "short",
                "confirm_password": "diff",
                "security_question": "",
                "security_answer": "",
            },
        )
        self.assertEqual(resp_err.status_code, 200)
        self.assertIn("form_errors", resp_err.context)

        # Unknown role falls back to buyer
        resp_role = self.client.post(
            reverse("register"),
            {
                "email": "newrole@example.com",
                "first_name": "New",
                "last_name": "Role",
                "role": "superadmin",
                "password": DEFAULT_PASSWORD,
                "confirm_password": DEFAULT_PASSWORD,
                "security_question": "city",
                "security_answer": "austin",
            },
        )
        self.assertRedirects(resp_role, reverse("buyer_home"), fetch_redirect_response=False)
        self.assertEqual(User.objects.get(email="newrole@example.com").role, "buyer")

    @expectedFailure
    def test_email_verification_view_renders_template(self):
        """
        BUG: General/views.py:256 renders 'auth/email_verification.html',
        but the actual template file is 'auth/email_verif.html'.
        """
        resp = self.client.get(reverse("email_verif"))
        self.assertEqual(resp.status_code, 200)

    def test_profile_settings_post_valid_redirects(self):
        """Valid POST on profile_settings updates security question and redirects before rendering missing template."""
        self.client.force_login(self.buyer)
        resp = self.client.post(
            reverse("profile_settings"),
            {
                "security_question": "In what city were you born?",
                "security_answer": "Seattle",
            },
        )
        self.assertRedirects(resp, reverse("profile_settings"), fetch_redirect_response=False)

    @expectedFailure
    def test_profile_settings_get_renders_template(self):
        """
        BUG: General/views.py:262-284 renders 'auth/buyer_profile.html' (or seller/admin),
        which does not exist in General/templates/auth/.
        """
        self.client.force_login(self.buyer)
        resp = self.client.get(reverse("profile_settings"))
        self.assertEqual(resp.status_code, 200)

    def test_catalog_filters_sorting_and_pagination(self):
        """catalog supports q, min_price, max_price, in_stock, sort options, and page=0."""
        self.client.force_login(self.buyer)
        create_book(seller=self.seller, title="Brave New World", base_price_cents=999, quantity_available=0)

        for params in (
            {"q": "Orwell", "min_price": "10", "max_price": "30", "in_stock": "1", "sort": "price_asc"},
            {"min_price": "invalid", "max_price": "invalid", "sort": "price_desc", "page": "0"},
            {"sort": "newest"},
            {"sort": "rating_desc"},
        ):
            resp = self.client.get(reverse("catalog"), params)
            self.assertEqual(resp.status_code, 200)

    def test_book_detail_and_compare_views(self):
        """book_detail, add_to_compare, remove_from_compare, clear_compare, compare_products."""
        self.client.force_login(self.buyer)
        resp_detail = self.client.get(reverse("book_detail", kwargs={"pk": self.book.id}))
        self.assertEqual(resp_detail.status_code, 200)

        # Add 5 books to compare; capped at 4
        books = [self.book] + [
            create_book(seller=self.seller, title=f"Cmp {i}", base_price_cents=1000 + i * 100)
            for i in range(4)
        ]
        # Add review to first book to cover avg_rating branch in compare_products
        _, oi = create_order_with_item(self.buyer, self.book, status="delivered")
        Review.objects.create(user=self.buyer, book=self.book, order_item=oi, rating=5)

        for b in books:
            self.client.get(reverse("add_to_compare", kwargs={"book_id": b.id}))
        self.assertEqual(len(self.client.session["compare_list"]), 4)

        resp_cmp = self.client.get(reverse("compare_products"))
        self.assertEqual(resp_cmp.status_code, 200)
        self.assertEqual(len(resp_cmp.context["compared_books"]), 4)

        self.client.get(reverse("remove_from_compare", kwargs={"book_id": self.book.id}))
        self.assertNotIn(self.book.id, self.client.session["compare_list"])

        self.client.get(reverse("clear_compare"))
        self.assertEqual(self.client.session["compare_list"], [])

    @expectedFailure
    def test_general_legacy_cart_and_checkout_views_render(self):
        """
        BUG: General/views.py:447 (cart) and :461 (checkout) render 'catalog/cart.html',
        which does not exist (Buyer app uses 'cart/buyer_cart.html').
        """
        self.client.force_login(self.buyer)
        resp = self.client.get("/cart/")
        self.assertEqual(resp.status_code, 200)

    def test_logout_view_clears_session_and_redirects(self):
        self.client.force_login(self.buyer)
        resp = self.client.get(reverse("logout"))
        self.assertRedirects(resp, reverse("login"))

    def test_steward_contribute_get_and_post_valid_and_invalid(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(reverse("steward_contribute")).status_code, 200)

        # Invalid POST: missing name/city and invalid/zero amount
        resp_bad1 = self.client.post(
            reverse("steward_contribute"),
            {"contributor_name": "", "contributor_city": "", "amount_dollars": "0"},
        )
        self.assertEqual(resp_bad1.status_code, 200)

        resp_bad2 = self.client.post(
            reverse("steward_contribute"),
            {"contributor_name": "Alice", "contributor_city": "Austin", "amount_dollars": "not-a-number"},
        )
        self.assertEqual(resp_bad2.status_code, 200)

        # Valid POST
        resp_ok = self.client.post(
            reverse("steward_contribute"),
            {
                "contributor_name": "Alice",
                "contributor_city": "Austin",
                "amount_dollars": "15.50",
                "message": "Keep learning!",
            },
        )
        self.assertRedirects(resp_ok, reverse("steward_contribute"))
        self.assertEqual(StewardContribution.objects.filter(steward_user=self.buyer, amount_cents=1550).count(), 1)

    def test_flag_book_get_and_post(self):
        self.client.force_login(self.buyer)
        resp_get = self.client.get(reverse("flag_book", kwargs={"pk": self.book.id}))
        self.assertRedirects(resp_get, reverse("book_detail", kwargs={"pk": self.book.id}))

        resp_post = self.client.post(
            reverse("flag_book", kwargs={"pk": self.book.id}),
            {"flag_type": "inappropriate", "details": "Spam text"},
        )
        self.assertRedirects(resp_post, reverse("book_detail", kwargs={"pk": self.book.id}))
        self.assertTrue(FlagReport.objects.filter(target_book=self.book, reporter_user=self.buyer).exists())

    def test_forgot_password_and_account_security_flows(self):
        """Covers forgot_password_email, forgot_password_question, forgot_password_done, account_security."""
        self.assertEqual(self.client.get(reverse("forgot_password_email")).status_code, 200)

        # Unknown email
        resp_unknown = self.client.post(reverse("forgot_password_email"), {"email": "nobody@example.com"})
        self.assertEqual(resp_unknown.status_code, 200)

        # Question page without session redirects to email page
        self.assertRedirects(self.client.get(reverse("forgot_password_question")), reverse("forgot_password_email"))

        # Valid email sets session and redirects to question page
        resp_email = self.client.post(reverse("forgot_password_email"), {"email": "gen_buyer@example.com"})
        self.assertRedirects(resp_email, reverse("forgot_password_question"))

        # GET question page
        self.assertEqual(self.client.get(reverse("forgot_password_question")).status_code, 200)

        # Wrong security answer
        resp_wrong = self.client.post(
            reverse("forgot_password_question"),
            {
                "answer": "wrongcity",
                "new_password1": "NewValidPass123!",
                "new_password2": "NewValidPass123!",
            },
        )
        self.assertEqual(resp_wrong.status_code, 200)

        # Correct security answer resets password
        resp_reset = self.client.post(
            reverse("forgot_password_question"),
            {
                "answer": "austin",
                "new_password1": "NewValidPass123!",
                "new_password2": "NewValidPass123!",
            },
        )
        self.assertRedirects(resp_reset, reverse("forgot_password_done"))
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.check_password("NewValidPass123!"))
        self.assertEqual(self.client.get(reverse("forgot_password_done")).status_code, 200)

        # User with no security question set
        no_q_user = create_buyer(email="noq@example.com")
        no_q_user.security_question = ""
        no_q_user.save()
        session = self.client.session
        session["password_reset_user_id"] = no_q_user.id
        session.save()
        resp_no_q = self.client.get(reverse("forgot_password_question"))
        self.assertTrue(resp_no_q.context["no_question_set"])

        # account_security GET and POST for authenticated user
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.get(reverse("account_security")).status_code, 200)
        resp_sec = self.client.post(
            reverse("account_security"),
            {
                "security_question": "What was the name of your first pet?",
                "security_answer": "Rex",
            },
        )
        self.assertRedirects(resp_sec, reverse("account_security"))


class BuyerViewsUnitTests(TestCase):
    """Unit tests for all views in Buyer/views.py."""

    def setUp(self):
        ensure_steward_pool()
        self.buyer = create_buyer(email="bview@example.com")
        self.seller = create_seller(email="sview@example.com", seller_approved=True)
        self.book = create_book(
            seller=self.seller,
            title="Clean Architecture",
            base_price_cents=3000,
            quantity_available=5,
        )
        self.client.force_login(self.buyer)

    def test_buyer_home_and_dashboard_views(self):
        self.assertEqual(self.client.get(reverse("buyer_home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("buyer_dashboard")).status_code, 200)

    def test_cart_add_update_remove_and_steward_free_selection(self):
        """Covers add_to_cart, buyer_cart, update_cart_item, remove_cart_item, cart_steward_free_select/deselect."""
        # Add to cart with invalid quantity -> defaults to 1
        self.client.post(reverse("add_to_cart", kwargs={"book_id": self.book.id}), {"quantity": "invalid"})
        self.assertEqual(self.client.get(reverse("cart")).status_code, 200)

        # Update cart item quantity to 2
        self.client.post(reverse("update_cart_item", kwargs={"item_id": self.book.id}), {"quantity": 2})
        self.assertEqual(CartItem.objects.get(cart__user=self.buyer, book=self.book).quantity, 2)

        # Remove cart item decrements from 2 to 1, then deletes on second call
        self.client.post(reverse("remove_cart_item", kwargs={"item_id": self.book.id}))
        self.assertEqual(CartItem.objects.get(cart__user=self.buyer, book=self.book).quantity, 1)

        # Non-steward cannot select free book
        self.client.post(reverse("cart_steward_free_select", kwargs={"book_id": self.book.id}))
        self.assertFalse(CartItem.objects.get(cart__user=self.buyer, book=self.book).is_steward_free)

        # Make buyer a verified steward and select free book
        self.buyer.steward_verified = True
        self.buyer.save()
        self.client.post(reverse("cart_steward_free_select", kwargs={"book_id": self.book.id}))
        ci = CartItem.objects.get(cart__user=self.buyer, book=self.book)
        self.assertTrue(ci.is_steward_free)
        self.assertEqual(ci.unit_price_cents, 0)

        # Adding paid copy while free is selected shows error message
        self.client.post(reverse("add_to_cart", kwargs={"book_id": self.book.id}), {"quantity": 1})
        # Updating quantity while steward_free keeps quantity=1
        self.client.post(reverse("update_cart_item", kwargs={"item_id": self.book.id}), {"quantity": 4})
        ci.refresh_from_db()
        self.assertEqual(ci.quantity, 1)

        # Deselect steward free book restores unit_price_cents
        self.client.post(reverse("cart_steward_free_deselect", kwargs={"book_id": self.book.id}))
        ci.refresh_from_db()
        self.assertFalse(ci.is_steward_free)
        self.assertEqual(ci.unit_price_cents, 3000)

        # Remove last copy deletes CartItem
        self.client.post(reverse("remove_cart_item", kwargs={"item_id": self.book.id}))
        self.assertFalse(CartItem.objects.filter(cart__user=self.buyer, book=self.book).exists())

    def test_buyer_checkout_validation_errors_and_success_and_steward_milestone(self):
        """Covers checkout error paths (empty cart, bad address, bad payment, bad steward, stock) and milestone."""
        addr = create_address(self.buyer)
        pm = create_payment_method(self.buyer)

        # Empty cart POST
        resp_empty = self.client.post(reverse("checkout"), {})
        self.assertEqual(resp_empty.context["checkout_error"], "Your cart is empty.")

        add_book_to_db_cart(self.buyer, self.book, 1)

        # Missing shipping address
        resp_no_addr = self.client.post(reverse("checkout"), {"payment_method_id": pm.id})
        self.assertEqual(resp_no_addr.context["checkout_error"], "Please select a shipping address.")

        # Invalid shipping address
        resp_bad_addr = self.client.post(
            reverse("checkout"), {"shipping_address_id": 999999, "payment_method_id": pm.id}
        )
        self.assertEqual(resp_bad_addr.context["checkout_error"], "Invalid shipping address.")

        # Missing payment method
        resp_no_pm = self.client.post(reverse("checkout"), {"shipping_address_id": addr.id})
        self.assertEqual(resp_no_pm.context["checkout_error"], "Please select a payment method.")

        # Invalid payment method
        resp_bad_pm = self.client.post(
            reverse("checkout"), {"shipping_address_id": addr.id, "payment_method_id": 999999}
        )
        self.assertEqual(resp_bad_pm.context["checkout_error"], "Invalid payment method.")

        # Invalid steward contribution
        resp_bad_stew = self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": addr.id,
                "payment_method_id": pm.id,
                "steward_contribution": "-5",
            },
        )
        self.assertIn("valid steward contribution", resp_bad_stew.context["checkout_error"])

        # Insufficient stock inside atomic checkout
        self.book.inventory.quantity_available = 0
        self.book.inventory.save()
        resp_nostock = self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": addr.id,
                "payment_method_id": pm.id,
                "steward_contribution": "2.00",
            },
        )
        self.assertIn("Not enough stock", resp_nostock.context["checkout_error"])

        # Restore stock and contribute $10.00 (100 points) to trigger steward milestone welcome redirect
        self.book.inventory.quantity_available = 5
        self.book.inventory.save()
        resp_ok = self.client.post(
            reverse("checkout"),
            {
                "shipping_address_id": addr.id,
                "payment_method_id": pm.id,
                "steward_contribution": "10.00",
            },
        )
        self.assertRedirects(resp_ok, reverse("steward_welcome"))
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.steward_verified)
        self.assertEqual(self.buyer.steward_progress, 100)

        # Complete steward_welcome form (invalid empty city, then valid city)
        resp_welcome_err = self.client.post(reverse("steward_welcome"), {"steward_city": "   "})
        self.assertIsNotNone(resp_welcome_err.context["city_error"])

        order = Order.objects.filter(user=self.buyer).first()
        resp_welcome_ok = self.client.post(reverse("steward_welcome"), {"steward_city": "Austin"})
        self.assertRedirects(resp_welcome_ok, reverse("order_confirmation", kwargs={"order_id": order.id}))

    def test_payment_methods_and_shipping_addresses_crud(self):
        """Covers add/list/set-default/delete for payment methods and shipping addresses."""
        # Add payment method validation errors
        resp_pm_err = self.client.post(
            reverse("buyer_add_payment_method"),
            {
                "cardholder_name": "",
                "brand": "",
                "cvv": "1",
                "card_number": "123",
                "exp_month": "13",
                "exp_year": "1999",
            },
        )
        self.assertEqual(resp_pm_err.status_code, 200)

        # Expired card validation error
        resp_pm_exp = self.client.post(
            reverse("buyer_add_payment_method"),
            {
                "cardholder_name": "Alice",
                "brand": "Visa",
                "cvv": "123",
                "card_number": "4111111111114242",
                "exp_month": "1",
                "exp_year": "2020",
            },
        )
        self.assertEqual(resp_pm_exp.status_code, 200)

        # Valid payment method creation
        resp_pm_ok = self.client.post(
            reverse("buyer_add_payment_method"),
            {
                "cardholder_name": "Alice Buyer",
                "brand": "Visa",
                "cvv": "123",
                "card_number": "4111 1111 1111 4242",
                "exp_month": "12",
                "exp_year": "2030",
                "is_default": "on",
            },
        )
        self.assertRedirects(resp_pm_ok, reverse("buyer_payments"))
        pm = PaymentMethod.objects.get(user=self.buyer)
        self.assertEqual(pm.last4, "4242")

        self.assertRedirects(
            self.client.post(reverse("set_default_payment_method", kwargs={"payment_method_id": pm.id})),
            reverse("buyer_payments"),
        )
        self.assertRedirects(
            self.client.post(reverse("delete_payment_method", kwargs={"payment_method_id": pm.id})),
            reverse("buyer_payments"),
        )

        # Shipping address validation error and valid CRUD
        resp_addr_err = self.client.post(reverse("buyer_add_shipping_address"), {"line1": ""})
        self.assertEqual(resp_addr_err.status_code, 200)

        resp_addr_ok = self.client.post(
            reverse("buyer_add_shipping_address"),
            {
                "label": "Campus",
                "line1": "500 College Rd",
                "line2": "",
                "city": "Austin",
                "state": "TX",
                "postal_code": "78705",
                "country": "USA",
                "is_default": "on",
            },
        )
        self.assertRedirects(resp_addr_ok, reverse("buyer_shipping"))
        addr = Address.objects.get(user=self.buyer)
        self.assertRedirects(
            self.client.post(reverse("set_default_address", kwargs={"address_id": addr.id})),
            reverse("buyer_shipping"),
        )
        self.assertRedirects(
            self.client.post(reverse("delete_address", kwargs={"address_id": addr.id})),
            reverse("buyer_shipping"),
        )

    def test_buyer_profile_update_info_password_and_security(self):
        """Covers buyer_profile GET and POST actions: profile, password, and security."""
        self.assertEqual(self.client.get(reverse("buyer_profile")).status_code, 200)

        # Profile error and success
        resp_prof_err = self.client.post(
            reverse("buyer_profile"), {"action": "profile", "first_name": "", "last_name": ""}
        )
        self.assertIn("profile_error", resp_prof_err.context)

        resp_prof_ok = self.client.post(
            reverse("buyer_profile"),
            {
                "action": "profile",
                "first_name": "Alicia",
                "last_name": "Updated",
                "phone": "555-9999",
                "steward_city": "Houston",
            },
        )
        self.assertRedirects(resp_prof_ok, reverse("buyer_profile"))

        # Password error and success
        resp_pw_err = self.client.post(
            reverse("buyer_profile"),
            {"action": "password", "current_password": "wrong", "new_password": "short", "confirm_password": "x"},
        )
        self.assertIn("password_error", resp_pw_err.context)

        resp_pw_ok = self.client.post(
            reverse("buyer_profile"),
            {
                "action": "password",
                "current_password": DEFAULT_PASSWORD,
                "new_password": "NewBuyerPass123!",
                "confirm_password": "NewBuyerPass123!",
            },
        )
        self.assertRedirects(resp_pw_ok, reverse("buyer_profile"))

        # Security question error and success
        resp_sec_err = self.client.post(
            reverse("buyer_profile"),
            {"action": "security", "security_question": "", "security_answer": ""},
        )
        self.assertIn("security_error", resp_sec_err.context)

        resp_sec_ok = self.client.post(
            reverse("buyer_profile"),
            {
                "action": "security",
                "security_question": "In what city were you born?",
                "security_answer": "Dallas",
            },
        )
        self.assertRedirects(resp_sec_ok, reverse("buyer_profile"))

    def test_order_history_detail_return_request_and_review_submission(self):
        """Covers order_history filters, order_detail, return_request_view, and review_submission."""
        order, item = create_order_with_item(self.buyer, self.book, status="delivered")

        resp_hist = self.client.get(
            reverse("order_history"),
            {"status": "delivered", "from": "2020-01-01", "to": "2035-12-31"},
        )
        self.assertEqual(resp_hist.status_code, 200)
        self.assertEqual(len(resp_hist.context["orders"]), 1)

        # Invalid date filters are gracefully ignored
        self.assertEqual(
            self.client.get(reverse("order_history"), {"from": "bad", "to": "bad"}).status_code,
            200,
        )

        # Order detail
        self.assertEqual(self.client.get(reverse("order_detail", kwargs={"order_id": order.id})).status_code, 200)

        # Review submission: invalid rating (<1 or >5) redirects without creating review
        self.client.post(reverse("review_submission", kwargs={"item_id": item.id}), {"rating": 0})
        self.assertFalse(Review.objects.filter(order_item=item).exists())

        # Valid rating creates review
        self.client.post(reverse("review_submission", kwargs={"item_id": item.id}), {"rating": 5})
        self.assertEqual(Review.objects.get(order_item=item).rating, 5)

        # Return request GET, empty POST error, valid POST, and duplicate error
        self.assertEqual(self.client.get(reverse("return_request", kwargs={"order_id": order.id})).status_code, 200)
        resp_ret_empty = self.client.post(
            reverse("return_request", kwargs={"order_id": order.id}),
            {"reason_category": "", "reason_details": "   "},
        )
        self.assertIn("return_error", resp_ret_empty.context)

        resp_ret_ok = self.client.post(
            reverse("return_request", kwargs={"order_id": order.id}),
            {"reason_category": "damaged", "reason_details": "Pages torn"},
        )
        self.assertRedirects(resp_ret_ok, reverse("order_detail", kwargs={"order_id": order.id}))

        resp_ret_dup = self.client.get(reverse("return_request", kwargs={"order_id": order.id}))
        self.assertIn("already been submitted", resp_ret_dup.context["return_error"])


class SellerViewsUnitTests(TestCase):
    """Unit tests for all views in Seller/views.py."""

    def setUp(self):
        self.seller = create_seller(email="s_unit@example.com", seller_approved=True)
        self.buyer = create_buyer(email="b_unit@example.com")
        self.book = create_book(
            seller=self.seller,
            title="Design Patterns",
            base_price_cents=3500,
            quantity_available=3,
        )
        self.client.force_login(self.seller)

    def test_seller_access_guards_redirect_non_sellers_and_unapproved_sellers(self):
        """Non-sellers and unapproved sellers are redirected away from seller views."""
        unapp = create_seller(email="unapp_seller@example.com", seller_approved=False)
        self.client.force_login(unapp)
        self.assertRedirects(self.client.get(reverse("seller_home")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("seller_dashboard")), reverse("buyer_home"))

        self.client.force_login(self.buyer)
        self.assertRedirects(self.client.get(reverse("seller_profile")), reverse("general_home"))
        self.assertRedirects(self.client.get(reverse("add_books")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("manage_inventory")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("seller_sales_overview")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("seller_orders")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("seller_return_requests")), reverse("buyer_home"))
        self.assertRedirects(self.client.get(reverse("seller_webhooks")), reverse("buyer_home"))

    def test_seller_home_dashboard_and_sales_overview(self):
        order, item = create_order_with_item(self.buyer, self.book, status="delivered")
        Review.objects.create(user=self.buyer, book=self.book, order_item=item, rating=4)

        self.assertEqual(self.client.get(reverse("seller_home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("seller_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("seller_sales_overview")).status_code, 200)

    def test_seller_profile_password_and_security_and_invalid_profile(self):
        self.assertEqual(self.client.get(reverse("seller_profile")).status_code, 200)

        # Invalid profile missing names
        resp_err = self.client.post(
            reverse("seller_profile"), {"action": "profile", "first_name": "", "last_name": ""}
        )
        self.assertIn("profile_error", resp_err.context)

        # Password update valid & invalid
        resp_pw_err = self.client.post(
            reverse("seller_profile"),
            {"action": "password", "current_password": "bad", "new_password": "short", "confirm_password": "x"},
        )
        self.assertIn("password_error", resp_pw_err.context)

        resp_pw_ok = self.client.post(
            reverse("seller_profile"),
            {
                "action": "password",
                "current_password": DEFAULT_PASSWORD,
                "new_password": "NewSellerPass123!",
                "confirm_password": "NewSellerPass123!",
            },
        )
        self.assertRedirects(resp_pw_ok, reverse("seller_profile"))

        # Security question valid & invalid
        resp_sec_err = self.client.post(
            reverse("seller_profile"), {"action": "security", "security_question": "", "security_answer": ""}
        )
        self.assertIn("security_error", resp_sec_err.context)

        resp_sec_ok = self.client.post(
            reverse("seller_profile"),
            {
                "action": "security",
                "security_question": "In what city were you born?",
                "security_answer": "Miami",
            },
        )
        self.assertRedirects(resp_sec_ok, reverse("seller_profile"))

    @expectedFailure
    def test_seller_profile_update_profile_action_succeeds(self):
        """
        BUG: Seller/views.py:273 calls `messages.sucess(...)` (typo for `messages.success`),
        and line 267 slices `phone[:50]` when `phone` is None.
        """
        resp = self.client.post(
            reverse("seller_profile"),
            {
                "action": "profile",
                "first_name": "Robert",
                "last_name": "Seller",
                "phone": "555-1234",
                "store_name": "Robert's Emporium",
            },
        )
        self.assertRedirects(resp, reverse("seller_profile"))

    def test_add_books_get_and_post_valid_and_invalid_and_cover_upload(self):
        self.assertEqual(self.client.get(reverse("add_books")).status_code, 200)

        # Invalid POST
        resp_err = self.client.post(
            reverse("add_books"),
            {"title": "", "author": "", "condition": "", "price": "0", "quantity_available": "-1"},
        )
        self.assertIn("form_error", resp_err.context)

        # Invalid file extension
        bad_file = SimpleUploadedFile("bad.txt", b"hello", content_type="text/plain")
        resp_bad_file = self.client.post(
            reverse("add_books"),
            {
                "title": "Test Upload Book",
                "author": "Author",
                "condition": "New",
                "price": "19.99",
                "quantity_available": "2",
                "cover_image": bad_file,
            },
        )
        self.assertIn("form_error", resp_bad_file.context)

        # Valid POST with PNG upload; clean up created cover file afterward
        png_file = SimpleUploadedFile("cover.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")
        title = "UniqueUnitCoverBookTest"
        created_cover = Path(settings.BASE_DIR) / "book_images" / f"{title}.png"
        try:
            resp_ok = self.client.post(
                reverse("add_books"),
                {
                    "title": title,
                    "author": "Jane Author",
                    "condition": "Like New",
                    "price": "24.50",
                    "quantity_available": "4",
                    "publication_year": "2024",
                    "reorder_threshold": "2",
                    "cover_image": png_file,
                },
            )
            self.assertRedirects(resp_ok, reverse("manage_inventory"))
            book = Book.objects.get(title=title)
            self.assertEqual(book.base_price_cents, 2450)
            self.assertEqual(book.stock_quantity, 4)
        finally:
            if created_cover.exists():
                created_cover.unlink()

    def test_manage_inventory_filters_sorts_and_post_updates(self):
        """Covers manage_inventory GET filters/sorts and POST price/stock/active updates."""
        for status in ("PUBLISHED", "UNPUBLISHED", "OUT_OF_STOCK", "LOW_STOCK"):
            self.assertEqual(self.client.get(reverse("manage_inventory"), {"status": status}).status_code, 200)
        for sort in ("newest", "oldest", "stock_asc", "stock_desc", "price_asc", "price_desc", "title"):
            self.assertEqual(self.client.get(reverse("manage_inventory"), {"q": "Design", "sort": sort}).status_code, 200)

        # Invalid price and invalid stock
        self.assertRedirects(
            self.client.post(
                reverse("manage_inventory"),
                {"book_id": self.book.id, "price": "0", "stock_quantity": "5"},
            ),
            reverse("manage_inventory"),
        )
        self.assertRedirects(
            self.client.post(
                reverse("manage_inventory"),
                {"book_id": self.book.id, "price": "20.00", "stock_quantity": "not-an-int"},
            ),
            reverse("manage_inventory"),
        )

        # Valid update
        self.assertRedirects(
            self.client.post(
                reverse("manage_inventory"),
                {"book_id": self.book.id, "price": "29.99", "stock_quantity": "8", "is_active": "on"},
            ),
            reverse("manage_inventory"),
        )
        self.book.refresh_from_db()
        self.assertEqual(self.book.base_price_cents, 2999)
        self.assertEqual(self.book.stock_quantity, 8)

    def test_seller_orders_order_details_and_update_status(self):
        order, _ = create_order_with_item(self.buyer, self.book, status="paid")
        self.assertEqual(self.client.get(reverse("seller_orders")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("seller_order_details", kwargs={"order_id": order.id})).status_code,
            200,
        )

        resp_status = self.client.post(
            reverse("update_order_status", kwargs={"order_id": order.id}),
            {"status": "shipped"},
        )
        self.assertRedirects(resp_status, reverse("seller_order_details", kwargs={"order_id": order.id}))
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")

    @expectedFailure
    def test_update_order_status_invalid_status_redirects_to_order_details(self):
        """
        BUG: Seller/views.py:851 redirects to `" seller_order_details"` (with a leading space),
        raising NoReverseMatch when an invalid status is submitted.
        """
        order, _ = create_order_with_item(self.buyer, self.book, status="paid")
        resp = self.client.post(
            reverse("update_order_status", kwargs={"order_id": order.id}),
            {"status": "not_a_valid_status"},
        )
        self.assertRedirects(resp, reverse("seller_order_details", kwargs={"order_id": order.id}))

    @expectedFailure
    def test_seller_orders_open_status_filter_excludes_delivered_orders(self):
        """
        BUG: Seller/views.py:648 filters `qs` for `status=OPEN`, but lines 650-656 immediately
        overwrite `orders` with an unfiltered query.
        """
        create_order_with_item(self.buyer, self.book, status="delivered")
        resp = self.client.get(reverse("seller_orders"), {"status": "OPEN"})
        self.assertEqual(len(resp.context["orders"]), 0)

    def test_seller_return_requests_and_book_received_flow(self):
        """Seller marks return as book_received, crediting buyer and restoring inventory."""
        order, _ = create_order_with_item(self.buyer, self.book, quantity=1, status="delivered")
        rr = ReturnRequest.objects.create(order=order, reason="Wrong book")

        self.assertEqual(self.client.get(reverse("seller_return_requests")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("seller_return_request_detail", kwargs={"return_id": rr.id})).status_code,
            200,
        )

        resp_rec = self.client.post(
            reverse("seller_return_request_detail", kwargs={"return_id": rr.id}),
            {"action": "book_received"},
        )
        self.assertRedirects(resp_rec, reverse("seller_return_request_detail", kwargs={"return_id": rr.id}))
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 3500)
        rr.refresh_from_db()
        self.assertEqual(rr.status, "refunded")

        # Duplicate POST shows already received message and does not double-credit
        self.client.post(
            reverse("seller_return_request_detail", kwargs={"return_id": rr.id}),
            {"action": "book_received"},
        )
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 3500)

    @expectedFailure
    def test_seller_return_request_detail_approve_action_sets_status_approved(self):
        """
        BUG: Seller/views.py:752 gates all POST handling behind `request.POST.get("action") == "book_received"`,
        making `if action == "approve":` and `if action == "deny":` on lines 754-772 unreachable.
        """
        order, _ = create_order_with_item(self.buyer, self.book, quantity=1, status="delivered")
        rr = ReturnRequest.objects.create(order=order, reason="Damaged")
        self.client.post(
            reverse("seller_return_request_detail", kwargs={"return_id": rr.id}),
            {"action": "approve"},
        )
        rr.refresh_from_db()
        self.assertEqual(rr.status, "approved")

    def test_seller_webhooks_view_save_and_test_actions(self):
        self.assertEqual(self.client.get(reverse("seller_webhooks")).status_code, 200)

        # Test action without URL
        resp_no_url = self.client.post(reverse("seller_webhooks"), {"action": "test", "webhook_url": ""})
        self.assertEqual(resp_no_url.status_code, 200)

        # Test action with mocked webhook delivery
        with patch("Seller.views.post_seller_webhook", return_value=(True, None)):
            resp_test_ok = self.client.post(
                reverse("seller_webhooks"),
                {"action": "test", "webhook_url": "https://example.com/hook", "webhook_secret": "s"},
            )
            self.assertEqual(resp_test_ok.status_code, 200)

        with patch("Seller.views.post_seller_webhook", return_value=(False, "Timeout")):
            resp_test_fail = self.client.post(
                reverse("seller_webhooks"),
                {"action": "test", "webhook_url": "https://example.com/hook"},
            )
            self.assertEqual(resp_test_fail.status_code, 200)

        # Save action
        resp_save = self.client.post(
            reverse("seller_webhooks"),
            {
                "action": "save",
                "webhook_url": "https://example.com/hook",
                "webhook_secret": "sec",
                "webhook_enabled": "on",
            },
        )
        self.assertRedirects(resp_save, reverse("seller_webhooks"))


class AdminViewsUnitTests(TestCase):
    """Unit tests for all views and staff_required decorator in Admin/views.py."""

    def setUp(self):
        self.admin = create_admin_user(email="admin_unit@example.com")
        self.buyer = create_buyer(email="buyer_for_admin@example.com", buyer_approved=False)
        self.seller = create_seller(email="seller_for_admin@example.com", seller_approved=False)
        self.book = create_book(seller=self.seller, title="Admin Monitored Book")

    def test_staff_required_redirects_anonymous_and_non_admin_users(self):
        """staff_required redirects anonymous users and logs out non-staff/non-admin users."""
        resp_anon = self.client.get(reverse("admin_dashboard"))
        self.assertIn(reverse("staff_login"), resp_anon.url)

        self.client.force_login(self.buyer)
        resp_buyer = self.client.get(reverse("admin_dashboard"))
        self.assertRedirects(resp_buyer, reverse("staff_login"), fetch_redirect_response=False)

    def test_staff_login_and_logout_redirects(self):
        self.assertRedirects(self.client.get(reverse("staff_login")), reverse("login"), fetch_redirect_response=False)
        self.client.force_login(self.admin)
        self.assertRedirects(
            self.client.get(reverse("staff_logout")),
            reverse("staff_login"),
            fetch_redirect_response=False,
        )

    def test_admin_all_get_pages_render_for_staff_admin(self):
        """Verifies all Admin GET views return 200 for an authenticated staff admin."""
        self.client.force_login(self.admin)
        FlagReport.objects.create(
            reporter_user=self.buyer,
            target_user=self.seller,
            flag_type="steward_check",
            details="Check steward",
        )
        FlagReport.objects.create(
            reporter_user=self.buyer,
            target_user=self.seller,
            flag_type="payment_fraud",
            details="Suspicious card",
        )
        url_names = [
            "admin_dashboard",
            "reports_flags",
            "user_monitoring",
            "seller_approvals",
            "buyer_approvals",
            "admin_users",
            "admin_books",
            "admin_inventory",
            "admin_returns",
            "admin_payments",
            "admin_notifications",
            "admin_audit_logs",
            "admin_settings",
            "steward_application",
            "abuse_detection",
            "return_disputes",
            "activity_logs",
        ]
        for name in url_names:
            with self.subTest(url_name=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 200)

    def test_approve_seller_and_approve_buyer_and_freeze_toggles(self):
        """Covers approve_seller, approve_buyer, toggle_user_freeze (including self-freeze guard), toggle_book_freeze."""
        self.client.force_login(self.admin)

        # GET on POST-only endpoints redirects
        self.assertRedirects(
            self.client.get(reverse("approve_seller", kwargs={"user_id": self.seller.id})),
            reverse("seller_approvals"),
        )
        self.assertRedirects(
            self.client.get(reverse("approve_buyer", kwargs={"user_id": self.buyer.id})),
            reverse("buyer_approvals"),
        )
        self.assertRedirects(
            self.client.get(reverse("toggle_user_freeze", kwargs={"user_id": self.buyer.id})),
            reverse("admin_users"),
        )
        self.assertRedirects(
            self.client.get(reverse("toggle_book_freeze", kwargs={"book_id": self.book.id})),
            reverse("admin_books"),
        )

        # POST approve seller & buyer
        self.client.post(reverse("approve_seller", kwargs={"user_id": self.seller.id}))
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.seller_approved)

        self.client.post(reverse("approve_buyer", kwargs={"user_id": self.buyer.id}))
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.buyer_approved)

        # Admin cannot freeze own account
        self.client.post(reverse("toggle_user_freeze", kwargs={"user_id": self.admin.id}))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

        # Freeze and unfreeze buyer
        self.client.post(reverse("toggle_user_freeze", kwargs={"user_id": self.buyer.id}))
        self.buyer.refresh_from_db()
        self.assertFalse(self.buyer.is_active)
        self.client.post(reverse("toggle_user_freeze", kwargs={"user_id": self.buyer.id}))
        self.buyer.refresh_from_db()
        self.assertTrue(self.buyer.is_active)

        # Freeze and unfreeze book
        self.client.post(reverse("toggle_book_freeze", kwargs={"book_id": self.book.id}))
        self.book.refresh_from_db()
        self.assertFalse(self.book.is_active)
        self.client.post(reverse("toggle_book_freeze", kwargs={"book_id": self.book.id}))
        self.book.refresh_from_db()
        self.assertTrue(self.book.is_active)

    def test_admin_settings_valid_password_and_security_updates(self):
        self.client.force_login(self.admin)

        # Security question error and valid update
        resp_sec_err = self.client.post(
            reverse("admin_settings"),
            {"action": "security", "security_question": "", "security_answer": ""},
        )
        self.assertIn("security_error", resp_sec_err.context)

        resp_sec_ok = self.client.post(
            reverse("admin_settings"),
            {"action": "security", "security_question": "city", "security_answer": "Chicago"},
        )
        self.assertRedirects(resp_sec_ok, reverse("admin_settings"))

        # Valid password update
        resp_pw_ok = self.client.post(
            reverse("admin_settings"),
            {
                "action": "password",
                "current_password": DEFAULT_PASSWORD,
                "new_password": "NewAdminPass123!",
                "confirm_password": "NewAdminPass123!",
            },
        )
        self.assertRedirects(resp_pw_ok, reverse("admin_settings"))

    @expectedFailure
    def test_admin_settings_invalid_password_renders_error_message(self):
        """
        BUG: Admin/views.py:391 has `ctx["passwrod_error"] - " ".join(errs)` (typo in key
        and `-` operator instead of `=`), raising KeyError('passwrod_error').
        """
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("admin_settings"),
            {
                "action": "password",
                "current_password": "wrong",
                "new_password": "short",
                "confirm_password": "mismatch",
            },
        )
        self.assertEqual(resp.status_code, 200)
