"""Integration: registration, login, approval gates, JWT, role permissions, password reset."""

from django.test import Client

from app.buyer.models import CartItem
from app.general.models import User
from tests.helpers import (
    PASSWORD,
    SECURITY_ANSWER,
    PassItOnTestCase,
    make_admin,
    make_book,
    make_buyer,
    make_seller,
    set_security_question,
)


def register_payload(email, role="buyer", **extra):
    data = {
        "email": email,
        "first_name": "Reg",
        "last_name": "Istered",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "role": role,
        "security_question": "In what city were you born?",
        "security_answer": SECURITY_ANSWER,
    }
    data.update(extra)
    return data


class RegistrationLoginFlowTests(PassItOnTestCase):
    def test_buyer_register_logout_login_blocked_until_approved_then_succeeds(self):
        """Register -> logout -> login is refused while unapproved -> admin approves -> login works."""
        resp = self.client.post("/register/", register_payload("newbuyer@example.com"))
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        user = User.objects.get(email="newbuyer@example.com")
        self.assertFalse(user.buyer_approved)

        self.client.get("/logout/")
        resp = self.login_via_form(user)
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertMessage(resp, "pending admin approval")
        self.assertNotIn("_auth_user_id", self.client.session)

        admin_client = Client()
        admin_client.force_login(make_admin())
        admin_client.post(f"/staff/staff/buyers/approve/{user.id}/")

        resp = self.login_via_form(user)
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

    def test_seller_register_unapproved_login_lands_on_buyer_home_then_seller_pages_after_approval(self):
        resp = self.client.post(
            "/register/", register_payload("newseller@example.com", role="seller", store_name="Shelf")
        )
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)
        seller = User.objects.get(email="newseller@example.com")
        self.assertEqual(seller.sellerprofile.store_name, "Shelf")
        self.client.get("/logout/")

        resp = self.login_via_form(seller)
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)
        self.assertMessage(resp, "pending approval")
        for url in ("/seller/", "/seller/dashboard/", "/seller/inventory/", "/seller/orders/"):
            self.assertRedirects(self.client.get(url), "/buyer/", fetch_redirect_response=False)

        admin_client = Client()
        admin_client.force_login(make_admin())
        admin_client.post(f"/staff/staff/sellers/approve/{seller.id}/")

        self.client.get("/logout/")
        resp = self.login_via_form(seller)
        self.assertRedirects(resp, "/seller/", fetch_redirect_response=False)
        for url in ("/seller/", "/seller/dashboard/", "/seller/inventory/", "/seller/orders/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_login_wrong_password_rerenders_form_with_error(self):
        buyer = make_buyer()
        resp = self.login_via_form(buyer, password="wrong-password")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["error"], "Invalid email or password.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_with_next_param_redirects_to_next_and_sets_jwt_cookies(self):
        buyer = make_buyer()
        resp = self.login_via_form(buyer, next="/buyer/orders/")
        self.assertRedirects(resp, "/buyer/orders/", fetch_redirect_response=False)
        self.assertTrue(resp.cookies["access_token"].value)
        self.assertTrue(resp.cookies["refresh_token"].value)
        self.assertTrue(resp.cookies["access_token"]["httponly"])

    def test_logout_clears_session_and_protected_pages_redirect_to_login(self):
        buyer = make_buyer()
        self.login_via_form(buyer)
        self.assertEqual(self.client.get("/buyer/orders/").status_code, 200)
        resp = self.client.get("/logout/")
        self.assertRedirects(resp, "/login/", fetch_redirect_response=False)
        self.assertEqual(resp.cookies["access_token"].value, "")
        resp = self.client.get("/buyer/orders/")
        self.assertRedirects(resp, "/login/?next=/buyer/orders/", fetch_redirect_response=False)

    def test_session_cart_lines_merge_into_db_cart_on_login(self):
        """Anonymous session cart is moved to the persisted Cart on login, merging quantities."""
        buyer = make_buyer()
        book_a, book_b = make_book(make_seller()), make_book(make_seller())
        from tests.helpers import add_cart_item

        add_cart_item(buyer, book_a, quantity=1)
        session = self.client.session
        session["cart"] = {str(book_a.id): {"quantity": 2}, str(book_b.id): {"quantity": 1}, "999999": {"quantity": 1}}
        session.save()

        self.login_via_form(buyer)
        quantities = dict(CartItem.objects.filter(cart__user=buyer).values_list("book_id", "quantity"))
        self.assertEqual(quantities, {book_a.id: 3, book_b.id: 1})
        self.assertEqual(self.client.session["cart"], {})


class JwtApiFlowTests(PassItOnTestCase):
    def test_token_obtain_then_refresh_returns_new_access_token(self):
        buyer = make_buyer()
        resp = self.client.post("/api/token/", {"email": buyer.email, "password": PASSWORD})
        self.assertEqual(resp.status_code, 200)
        tokens = resp.json()
        self.assertIn("access", tokens)
        self.assertIn("refresh", tokens)

        resp = self.client.post("/api/token/refresh/", {"refresh": tokens["refresh"]})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.json())

    def test_token_obtain_bad_credentials_or_frozen_user_returns_401(self):
        buyer = make_buyer()
        resp = self.client.post("/api/token/", {"email": buyer.email, "password": "nope-nope"})
        self.assertEqual(resp.status_code, 401)
        buyer.is_active = False
        buyer.save()
        resp = self.client.post("/api/token/", {"email": buyer.email, "password": PASSWORD})
        self.assertEqual(resp.status_code, 401)

    def test_token_refresh_with_garbage_token_returns_401(self):
        resp = self.client.post("/api/token/refresh/", {"refresh": "not-a-jwt"})
        self.assertEqual(resp.status_code, 401)


class RoleAccessMatrixTests(PassItOnTestCase):
    SELLER_PAGES = ["/seller/", "/seller/dashboard/", "/seller/dashboard/sales-overview/",
                    "/seller/inventory/", "/seller/inventory/add/", "/seller/orders/",
                    "/seller/returns/", "/seller/webhooks/"]
    STAFF_PAGES = ["/staff/", "/staff/dashboard/", "/staff/reports/", "/staff/staff/sellers/approvals/",
                   "/staff/staff/buyers/approvals/", "/staff/users/list/", "/staff/books/",
                   "/staff/inventory/", "/staff/returns/", "/staff/payments/", "/staff/audit/"]
    LOGIN_REQUIRED = ["/catalog/", "/buyer/", "/buyer/cart/", "/buyer/checkout/", "/buyer/orders/",
                      "/buyer/profile/", "/steward/contribute/", "/seller/", "/logout/"]

    def test_buyer_denied_seller_pages_redirects_to_buyer_home(self):
        self.login(make_buyer())
        for url in self.SELLER_PAGES:
            self.assertRedirects(self.client.get(url), "/buyer/", fetch_redirect_response=False, msg_prefix=url)

    def test_buyer_denied_staff_pages_is_logged_out_and_sent_to_staff_login(self):
        self.login(make_buyer())
        for url in self.STAFF_PAGES:
            resp = self.client.get(url)
            self.assertRedirects(resp, "/staff/login/", fetch_redirect_response=False, msg_prefix=url)
            self.assertNotIn("_auth_user_id", self.client.session)
            self.login(make_buyer())

    def test_seller_denied_staff_pages_is_logged_out(self):
        seller = make_seller()
        for url in self.STAFF_PAGES:
            self.login(seller)
            self.assertRedirects(self.client.get(url), "/staff/login/", fetch_redirect_response=False)

    def test_admin_allowed_all_staff_pages(self):
        self.login(make_admin())
        for url in self.STAFF_PAGES:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_non_staff_admin_role_user_denied_staff_pages(self):
        """role=admin but is_staff=False must not pass staff_required."""
        self.login(make_admin(is_staff=False))
        self.assertRedirects(self.client.get("/staff/"), "/staff/login/", fetch_redirect_response=False)

    def test_anonymous_login_required_pages_redirect_to_login_with_next(self):
        for url in self.LOGIN_REQUIRED:
            self.assertRedirects(self.client.get(url), f"/login/?next={url}", fetch_redirect_response=False)

    def test_anonymous_staff_pages_redirect_to_staff_login_with_next(self):
        resp = self.client.get("/staff/reports/")
        self.assertRedirects(resp, "/staff/login/?next=/staff/reports/", fetch_redirect_response=False)
        # staff_login simply forwards to the shared login page.
        self.assertRedirects(self.client.get("/staff/login/"), "/login/", fetch_redirect_response=False)

    def test_authenticated_user_visiting_login_redirected_by_role(self):
        cases = [(make_admin(), "/staff/dashboard/"), (make_seller(), "/seller/"),
                 (make_seller(approved=False), "/buyer/"), (make_buyer(), "/buyer/")]
        for user, target in cases:
            self.login(user)
            self.assertRedirects(self.client.get("/login/"), target, fetch_redirect_response=False)


class FrozenAccountFlowTests(PassItOnTestCase):
    def test_admin_freeze_blocks_login_and_existing_session_unfreeze_restores(self):
        buyer = make_buyer()
        buyer_client = Client()
        buyer_client.force_login(buyer)
        self.login(make_admin())

        self.client.post(f"/staff/users/{buyer.id}/toggle-freeze/")
        buyer.refresh_from_db()
        self.assertFalse(buyer.is_active)
        # Existing session is no longer valid for an inactive user.
        self.assertEqual(buyer_client.get("/buyer/orders/").status_code, 302)
        resp = buyer_client.post("/login/", {"username": buyer.email, "password": PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["error"], "Invalid email or password.")

        self.client.post(f"/staff/users/{buyer.id}/toggle-freeze/")
        resp = buyer_client.post("/login/", {"username": buyer.email, "password": PASSWORD})
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)


class ForgotPasswordFlowTests(PassItOnTestCase):
    def test_forgot_password_full_flow_new_password_works_old_fails(self):
        buyer = set_security_question(make_buyer())
        resp = self.client.post("/forgot-password/", {"email": buyer.email.upper()})
        self.assertRedirects(resp, "/forgot-password/question/", fetch_redirect_response=False)

        resp = self.client.get("/forgot-password/question/")
        self.assertEqual(resp.context["security_question"], "In what city were you born?")

        resp = self.client.post("/forgot-password/question/", {
            "answer": "  SPRINGFIELD ", "new_password1": "Brand-new-pw-9", "new_password2": "Brand-new-pw-9",
        })
        self.assertRedirects(resp, "/forgot-password/done/", fetch_redirect_response=False)
        self.assertNotIn("password_reset_user_id", self.client.session)

        self.assertEqual(self.login_via_form(buyer).status_code, 200)  # old password rejected
        resp = self.login_via_form(buyer, password="Brand-new-pw-9")
        self.assertRedirects(resp, "/buyer/", fetch_redirect_response=False)

    def test_forgot_password_wrong_answer_keeps_old_password(self):
        buyer = set_security_question(make_buyer())
        self.client.post("/forgot-password/", {"email": buyer.email})
        resp = self.client.post("/forgot-password/question/", {
            "answer": "wrong", "new_password1": "Brand-new-pw-9", "new_password2": "Brand-new-pw-9",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Incorrect security answer.", resp.context["form"].errors["answer"])
        buyer.refresh_from_db()
        self.assertTrue(buyer.check_password(PASSWORD))

    def test_forgot_password_question_without_session_redirects_to_email_step(self):
        resp = self.client.get("/forgot-password/question/")
        self.assertRedirects(resp, "/forgot-password/", fetch_redirect_response=False)
