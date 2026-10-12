"""Unit tests for every view in General/views.py (one test class per view)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.test import TestCase

from Admin.models import FlagReport
from Buyer.models import CartItem
from General.models import StewardContribution, User
from Seller.models import SellerProfile
from tests.helpers import (
    PASSWORD,
    SECURITY_ANSWER,
    PassItOnTestCase,
    make_admin,
    make_book,
    make_buyer,
    make_order,
    make_review,
    make_seller,
    set_security_question,
)


def _register_payload(**overrides):
    data = {
        "email": "new.user@example.com",
        "first_name": "New",
        "last_name": "User",
        "password": "longenough1",
        "confirm_password": "longenough1",
        "role": "buyer",
        "security_question": "In what city were you born?",
        "security_answer": "  Springfield ",
    }
    data.update(overrides)
    return data


def _set_session(client, **values):
    session = client.session
    for key, value in values.items():
        session[key] = value
    session.save()


class HomeViewTests(TestCase):
    def test_home_get_renders_login_landing(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "login/login_page.html")


class LoginPageViewTests(PassItOnTestCase):
    def test_login_page_get_renders_form_with_next(self):
        response = self.client.get("/login/?next=/catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next"], "/catalog/")

    def test_login_page_authenticated_admin_redirects_to_admin_dashboard(self):
        self.login(make_admin())
        response = self.client.get("/login/")
        self.assertRedirects(response, "/staff/dashboard/", fetch_redirect_response=False)

    def test_login_page_authenticated_approved_seller_redirects_to_seller_home(self):
        self.login(make_seller())
        response = self.client.get("/login/")
        self.assertRedirects(response, "/seller/", fetch_redirect_response=False)

    def test_login_page_authenticated_unapproved_seller_redirects_to_buyer_home_with_message(self):
        self.login(make_seller(approved=False))
        response = self.client.get("/login/")
        self.assertRedirects(response, "/buyer/", fetch_redirect_response=False)
        self.assertMessage(response, "pending approval")

    def test_login_page_authenticated_buyer_redirects_to_buyer_home(self):
        self.login(make_buyer())
        response = self.client.get("/login/")
        self.assertRedirects(response, "/buyer/", fetch_redirect_response=False)

    def test_login_page_post_invalid_credentials_rerenders_with_error(self):
        buyer = make_buyer()
        response = self.client.post(
            "/login/", {"username": buyer.email, "password": "wrong", "next": "/catalog/"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], "Invalid email or password.")
        self.assertEqual(response.context["next"], "/catalog/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_page_post_unknown_email_rerenders_with_error(self):
        response = self.client.post("/login/", {"username": "nobody@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Invalid email or password.", response.content.decode())

    def test_login_page_post_inactive_user_rejected(self):
        buyer = make_buyer(is_active=False)
        response = self.login_via_form(buyer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], "Invalid email or password.")

    def test_login_page_post_unapproved_buyer_blocked_with_message(self):
        buyer = make_buyer(approved=False)
        response = self.login_via_form(buyer)
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertMessage(response, "pending admin approval")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_page_post_approved_buyer_redirects_and_sets_httponly_jwt_cookies(self):
        buyer = make_buyer()
        response = self.login_via_form(buyer)
        self.assertRedirects(response, "/buyer/", fetch_redirect_response=False)
        for name in ("access_token", "refresh_token"):
            self.assertIn(name, response.cookies)
            self.assertTrue(response.cookies[name]["httponly"])
            self.assertTrue(response.cookies[name].value)
        self.assertEqual(int(self.client.session["_auth_user_id"]), buyer.pk)

    def test_login_page_post_relative_next_url_is_honoured(self):
        response = self.login_via_form(make_buyer(), next="/catalog/")
        self.assertRedirects(response, "/catalog/", fetch_redirect_response=False)

    def test_login_page_post_absolute_next_url_is_ignored(self):
        """Open-redirect guard: only next values starting with '/' are followed."""
        response = self.login_via_form(make_buyer(), next="https://evil.example.com/")
        self.assertRedirects(response, "/buyer/", fetch_redirect_response=False)

    def test_login_page_post_admin_redirects_to_admin_dashboard(self):
        response = self.login_via_form(make_admin())
        self.assertRedirects(response, "/staff/dashboard/", fetch_redirect_response=False)

    def test_login_page_post_approved_seller_redirects_to_seller_home(self):
        response = self.login_via_form(make_seller())
        self.assertRedirects(response, "/seller/", fetch_redirect_response=False)

    def test_login_page_post_unapproved_seller_logs_in_but_redirects_to_buyer_home(self):
        seller = make_seller(approved=False)
        response = self.login_via_form(seller)
        self.assertRedirects(response, "/buyer/", fetch_redirect_response=False)
        self.assertMessage(response, "seller account is pending approval")
        self.assertEqual(int(self.client.session["_auth_user_id"]), seller.pk)

    def test_login_page_post_merges_session_cart_into_db_cart(self):
        """Anonymous session cart lines become CartItem rows on login and the session cart is cleared."""
        buyer = make_buyer()
        book = make_book(make_seller())
        _set_session(self.client, cart={str(book.id): {"quantity": 2}})
        self.login_via_form(buyer)
        item = CartItem.objects.get(cart__user=buyer, book=book)
        self.assertEqual(item.quantity, 2)
        self.assertEqual(self.client.session["cart"], {})


class RegisterViewTests(PassItOnTestCase):
    def test_register_get_renders_form(self):
        response = self.client.get("/register/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/register.html")

    def test_register_post_missing_fields_lists_every_error(self):
        response = self.client.post("/register/", {})
        self.assertEqual(response.status_code, 200)
        errors = response.context["form_errors"]
        for fragment in (
            "Email is required.",
            "First name is required.",
            "Last name is required.",
            "Password must be at least 8 characters.",
            "Security question is required.",
            "Security answer is required.",
        ):
            self.assertIn(fragment, errors)
        self.assertFalse(User.objects.exists())

    def test_register_post_short_password_rejected(self):
        response = self.client.post("/register/", _register_payload(password="short", confirm_password="short"))
        self.assertIn("at least 8 characters", response.context["form_errors"])

    def test_register_post_password_mismatch_rejected(self):
        response = self.client.post("/register/", _register_payload(confirm_password="different1"))
        self.assertIn("Passwords do not match.", response.context["form_errors"])
        self.assertFalse(User.objects.filter(email="new.user@example.com").exists())

    def test_register_post_duplicate_email_case_insensitive_rejected(self):
        make_buyer(email="dupe@example.com")
        response = self.client.post("/register/", _register_payload(email="DUPE@Example.com"))
        self.assertIn("already exists", response.context["form_errors"])
        self.assertEqual(User.objects.filter(email__iexact="dupe@example.com").count(), 1)

    def test_register_post_errors_echo_post_data(self):
        response = self.client.post("/register/", _register_payload(first_name=""))
        self.assertEqual(response.context["post"]["email"], "new.user@example.com")

    def test_register_post_buyer_creates_user_logs_in_and_redirects(self):
        response = self.client.post("/register/", _register_payload())
        self.assertRedirects(response, "/buyer/", fetch_redirect_response=False)
        user = User.objects.get(email="new.user@example.com")
        self.assertEqual(user.role, "buyer")
        self.assertTrue(user.check_password("longenough1"))
        self.assertIn("access_token", response.cookies)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_register_post_security_answer_hashed_trimmed_and_lowercased(self):
        self.client.post("/register/", _register_payload(security_answer="  SpringField "))
        user = User.objects.get(email="new.user@example.com")
        self.assertEqual(user.security_question, "In what city were you born?")
        self.assertTrue(check_password("springfield", user.security_answer_hash))
        self.assertNotEqual(user.security_answer_hash, "springfield")

    def test_register_post_invalid_role_coerced_to_buyer(self):
        """Privilege-escalation guard: role=admin from the form is downgraded to buyer."""
        self.client.post("/register/", _register_payload(role="admin"))
        user = User.objects.get(email="new.user@example.com")
        self.assertEqual(user.role, "buyer")
        self.assertFalse(user.is_staff)

    def test_register_post_blank_phone_stored_as_none(self):
        self.client.post("/register/", _register_payload(phone="   "))
        self.assertIsNone(User.objects.get(email="new.user@example.com").phone)

    def test_register_post_seller_creates_unapproved_seller_with_profile(self):
        response = self.client.post(
            "/register/", _register_payload(role="Seller", store_name=" Campus Books ", phone="555-1234")
        )
        self.assertRedirects(response, "/seller/", fetch_redirect_response=False)
        user = User.objects.get(email="new.user@example.com")
        self.assertEqual(user.role, "seller")
        self.assertFalse(user.seller_approved)
        self.assertEqual(user.phone, "555-1234")
        self.assertEqual(SellerProfile.objects.get(user=user).store_name, "Campus Books")

    def test_register_post_merges_session_cart(self):
        book = make_book(make_seller())
        _set_session(self.client, cart={str(book.id): {"quantity": 1}})
        self.client.post("/register/", _register_payload())
        user = User.objects.get(email="new.user@example.com")
        self.assertTrue(CartItem.objects.filter(cart__user=user, book=book).exists())


class EmailVerificationViewTests(TestCase):
    # BUG: view renders auth/email_verification.html but the template is named auth/email_verif.html.
    @unittest.expectedFailure
    def test_email_verification_get_renders_page(self):
        response = self.client.get("/email-verification/")
        self.assertEqual(response.status_code, 200)


class ProfileSettingsViewTests(PassItOnTestCase):
    def test_profile_settings_anonymous_redirects_to_login(self):
        response = self.client.get("/profile/settings/")
        self.assertRedirects(response, "/login/?next=/profile/settings/", fetch_redirect_response=False)

    def test_profile_settings_post_valid_updates_security_question_and_redirects(self):
        buyer = self.login(make_buyer())
        response = self.client.post(
            "/profile/settings/",
            {"security_question": "What was the name of your first pet?", "security_answer": " Rex "},
        )
        self.assertRedirects(response, "/profile/settings/", fetch_redirect_response=False)
        buyer.refresh_from_db()
        self.assertEqual(buyer.security_question, "What was the name of your first pet?")
        self.assertTrue(check_password("rex", buyer.security_answer_hash))

    # BUG: profile_settings renders auth/buyer_profile.html, which does not exist (TemplateDoesNotExist).
    @unittest.expectedFailure
    def test_profile_settings_buyer_get_renders_page(self):
        self.login(make_buyer())
        self.assertEqual(self.client.get("/profile/settings/").status_code, 200)

    # BUG: profile_settings renders auth/seller_profile.html, which does not exist.
    @unittest.expectedFailure
    def test_profile_settings_seller_get_renders_page(self):
        self.login(make_seller())
        self.assertEqual(self.client.get("/profile/settings/").status_code, 200)

    # BUG: profile_settings renders auth/admin_profile.html, which does not exist.
    @unittest.expectedFailure
    def test_profile_settings_admin_get_renders_page(self):
        self.login(make_admin())
        self.assertEqual(self.client.get("/profile/settings/").status_code, 200)


class CatalogViewTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.cheap = make_book(self.seller, title="Alpha Algorithms", author="Knuth", price_cents=500, stock=0)
        self.mid = make_book(self.seller, title="Beta Biology", author="Darwin", price_cents=1500,
                             description="evolution notes")
        self.pricey = make_book(self.seller, title="Gamma Geometry", author="Euclid", price_cents=3000)
        self.hidden = make_book(self.seller, title="Hidden Algorithms", is_active=False)
        self.login(make_buyer())

    def _titles(self, response):
        return [b.title for b in response.context["page_obj"].object_list]

    def test_catalog_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.get("/catalog/")
        self.assertRedirects(response, "/login/?next=/catalog/", fetch_redirect_response=False)

    def test_catalog_default_lists_active_books_by_title(self):
        response = self.client.get("/catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._titles(response), ["Alpha Algorithms", "Beta Biology", "Gamma Geometry"])

    def test_catalog_search_matches_title_author_and_description(self):
        self.assertEqual(self._titles(self.client.get("/catalog/?q=algorithms")), ["Alpha Algorithms"])
        self.assertEqual(self._titles(self.client.get("/catalog/?q=euclid")), ["Gamma Geometry"])
        self.assertEqual(self._titles(self.client.get("/catalog/?q=EVOLUTION")), ["Beta Biology"])

    def test_catalog_search_no_match_returns_empty_page(self):
        self.assertEqual(self._titles(self.client.get("/catalog/?q=zzzz")), [])

    def test_catalog_price_range_filters_inclusive_bounds(self):
        response = self.client.get("/catalog/?min_price=5&max_price=15")
        self.assertEqual(self._titles(response), ["Alpha Algorithms", "Beta Biology"])

    def test_catalog_invalid_price_values_are_ignored(self):
        response = self.client.get("/catalog/?min_price=abc&max_price=xyz")
        self.assertEqual(len(self._titles(response)), 3)

    def test_catalog_in_stock_excludes_zero_inventory(self):
        self.assertEqual(self._titles(self.client.get("/catalog/?in_stock=1")), ["Beta Biology", "Gamma Geometry"])

    def test_catalog_sort_price_asc_and_desc(self):
        self.assertEqual(self._titles(self.client.get("/catalog/?sort=price_asc"))[0], "Alpha Algorithms")
        self.assertEqual(self._titles(self.client.get("/catalog/?sort=price_desc"))[0], "Gamma Geometry")

    def test_catalog_sort_newest_puts_latest_first(self):
        self.assertEqual(self._titles(self.client.get("/catalog/?sort=newest"))[0], "Gamma Geometry")

    def test_catalog_sort_rating_desc_and_unknown_fall_back_to_title_order(self):
        for sort in ("rating_desc", "bogus"):
            self.assertEqual(self._titles(self.client.get(f"/catalog/?sort={sort}"))[0], "Alpha Algorithms")

    def test_catalog_pagination_twelve_per_page_with_page_edge_values(self):
        for i in range(10):
            make_book(self.seller, title=f"Zeta {i:02d}")
        self.assertEqual(len(self._titles(self.client.get("/catalog/"))), 12)
        self.assertEqual(self.client.get("/catalog/?page=0").context["page_obj"].number, 1)
        self.assertEqual(self.client.get("/catalog/?page=").context["page_obj"].number, 1)
        self.assertEqual(self.client.get("/catalog/?page=abc").context["page_obj"].number, 1)
        last = self.client.get("/catalog/?page=999").context["page_obj"]
        self.assertEqual(last.number, 2)
        self.assertEqual(len(last.object_list), 1)

    def test_catalog_cover_url_uses_default_when_no_image_file(self):
        response = self.client.get("/catalog/")
        for book in response.context["page_obj"].object_list:
            self.assertTrue(book.cover_serve_url.endswith("/book_covers/default.jpg"))

    def test_catalog_cover_url_uses_matching_image_file(self):
        make_book(self.seller, title="1984")
        response = self.client.get("/catalog/?q=1984")
        self.assertTrue(response.context["page_obj"].object_list[0].cover_serve_url.endswith("/book_covers/1984.jpg"))

    def test_catalog_compare_count_reflects_session(self):
        _set_session(self.client, compare_list=[self.mid.id, self.pricey.id])
        self.assertEqual(self.client.get("/catalog/").context["compare_count"], 2)


class BookDetailViewTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller(first_name="Sally", last_name="Seller")
        self.book = make_book(self.seller)
        self.login(make_buyer())

    def test_book_detail_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.get(f"/catalog/{self.book.id}/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))

    def test_book_detail_active_book_shows_seller_name(self):
        response = self.client.get(f"/catalog/{self.book.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["book"], self.book)
        self.assertEqual(response.context["seller_display_name"], "Sally Seller")
        self.assertEqual(response.context["cover_serve_filename"], "default.jpg")
        self.assertIsNone(response.context["cover_static_path"])

    def test_book_detail_book_without_seller_shows_dash(self):
        orphan = make_book(None)
        self.assertEqual(self.client.get(f"/catalog/{orphan.id}/").context["seller_display_name"], "—")

    def test_book_detail_inactive_book_returns_404(self):
        hidden = make_book(self.seller, is_active=False)
        self.assertEqual(self.client.get(f"/catalog/{hidden.id}/").status_code, 404)

    def test_book_detail_missing_book_returns_404(self):
        self.assertEqual(self.client.get("/catalog/999999/").status_code, 404)

    def test_book_detail_lists_reviews_for_book(self):
        reviewer = make_buyer()
        order = make_order(reviewer, [(self.book, 1)], status="delivered")
        review = make_review(reviewer, order.order_items.first(), rating=5)
        other_order = make_order(reviewer, [(make_book(self.seller), 1)])
        make_review(reviewer, other_order.order_items.first(), rating=1)
        response = self.client.get(f"/catalog/{self.book.id}/")
        self.assertEqual(list(response.context["reviews"]), [review])

    def test_book_detail_matching_cover_sets_static_path(self):
        book = make_book(self.seller, title="1984")
        response = self.client.get(f"/catalog/{book.id}/")
        self.assertEqual(response.context["cover_static_path"], "book_images/1984.jpg")


class CompareViewsTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.books = [make_book(self.seller) for _ in range(5)]

    def test_add_to_compare_appends_and_redirects_to_catalog(self):
        response = self.client.get(f"/compare/add/{self.books[0].id}/")
        self.assertRedirects(response, "/catalog/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["compare_list"], [self.books[0].id])

    def test_add_to_compare_redirects_to_referer_when_present(self):
        response = self.client.get(f"/compare/add/{self.books[0].id}/", HTTP_REFERER="/catalog/?q=x")
        self.assertEqual(response["Location"], "/catalog/?q=x")

    def test_add_to_compare_duplicate_is_ignored(self):
        self.client.get(f"/compare/add/{self.books[0].id}/")
        self.client.get(f"/compare/add/{self.books[0].id}/")
        self.assertEqual(self.client.session["compare_list"], [self.books[0].id])

    def test_add_to_compare_caps_list_at_four_items(self):
        for book in self.books:
            self.client.get(f"/compare/add/{book.id}/")
        self.assertEqual(self.client.session["compare_list"], [b.id for b in self.books[:4]])

    def test_remove_from_compare_removes_id_and_redirects(self):
        _set_session(self.client, compare_list=[self.books[0].id, self.books[1].id])
        response = self.client.get(f"/compare/remove/{self.books[0].id}/")
        self.assertRedirects(response, "/compare/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["compare_list"], [self.books[1].id])

    def test_remove_from_compare_unknown_id_is_noop(self):
        _set_session(self.client, compare_list=[self.books[0].id])
        self.client.get("/compare/remove/999999/")
        self.assertEqual(self.client.session["compare_list"], [self.books[0].id])

    def test_clear_compare_empties_list(self):
        _set_session(self.client, compare_list=[self.books[0].id])
        response = self.client.get("/compare/clear/")
        self.assertRedirects(response, "/compare/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["compare_list"], [])

    def test_compare_products_empty_session_renders_no_books(self):
        response = self.client.get("/compare/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["compared_books"], [])

    def test_compare_products_rating_display_with_and_without_reviews(self):
        reviewed, unreviewed = self.books[0], self.books[1]
        buyer = make_buyer()
        order = make_order(buyer, [(reviewed, 1)], status="delivered")
        make_review(buyer, order.order_items.first(), rating=4)
        order2 = make_order(buyer, [(reviewed, 1)], status="delivered")
        make_review(buyer, order2.order_items.first(), rating=5)
        _set_session(self.client, compare_list=[reviewed.id, unreviewed.id])
        rows = self.client.get("/compare/").context["compared_books"]
        self.assertEqual([r["book"] for r in rows], [reviewed, unreviewed])
        self.assertEqual(rows[0]["rating_display"], "4.5 / 5")
        self.assertEqual(rows[1]["rating_display"], "No Ratings")
        self.assertEqual(rows[0]["price_dollars"], reviewed.base_price_cents / 100)
        self.assertEqual(rows[0]["stock_quantity"], 5)
        self.assertTrue(rows[0]["cover_serve_url"].endswith("/book_covers/default.jpg"))

    def test_compare_products_skips_inactive_and_missing_books(self):
        hidden = make_book(self.seller, is_active=False)
        _set_session(self.client, compare_list=[hidden.id, 999999, self.books[2].id])
        rows = self.client.get("/compare/").context["compared_books"]
        self.assertEqual([r["book"] for r in rows], [self.books[2]])


class GeneralCartAndCheckoutViewTests(PassItOnTestCase):
    """Legacy placeholder views mounted at /cart/ and /checkout/ (the named URLs resolve to /buyer/...)."""

    def test_general_cart_anonymous_redirects_to_login(self):
        response = self.client.get("/cart/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))

    def test_general_checkout_anonymous_redirects_to_login(self):
        response = self.client.get("/checkout/")
        self.assertEqual(response.status_code, 302)

    # BUG: General.views.cart renders catalog/cart.html, which does not exist.
    @unittest.expectedFailure
    def test_general_cart_authenticated_renders_page(self):
        self.login(make_buyer())
        self.assertEqual(self.client.get("/cart/").status_code, 200)

    # BUG: General.views.checkout renders catalog/cart.html, which does not exist.
    @unittest.expectedFailure
    def test_general_checkout_authenticated_renders_page(self):
        self.login(make_buyer())
        self.assertEqual(self.client.get("/checkout/").status_code, 200)


class LogoutViewTests(PassItOnTestCase):
    def test_logout_anonymous_redirects_to_login(self):
        response = self.client.get("/logout/")
        self.assertRedirects(response, "/login/?next=/logout/", fetch_redirect_response=False)

    def test_logout_clears_session_and_expires_auth_cookies(self):
        self.login_via_form(make_buyer())
        response = self.client.get("/logout/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        for name in ("sessionid", "csrftoken", "access_token", "refresh_token"):
            self.assertIn(name, response.cookies)
            self.assertEqual(response.cookies[name]["max-age"], 0)


class StewardContributeViewTests(PassItOnTestCase):
    def setUp(self):
        self.user = self.login(make_buyer())

    def _post(self, **overrides):
        data = {"contributor_name": "Pat", "contributor_city": "Austin", "amount_dollars": "10.00", "message": "hi"}
        data.update(overrides)
        return self.client.post("/steward/contribute/", data)

    def test_steward_contribute_anonymous_redirects_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/steward/contribute/").status_code, 302)

    def test_steward_contribute_get_renders_form(self):
        response = self.client.get("/steward/contribute/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "steward/steward_contribute.html")

    def test_steward_contribute_missing_name_and_city_shows_errors(self):
        response = self._post(contributor_name="  ", contributor_city="")
        self.assertEqual(response.status_code, 200)
        self.assertMessage(response, "Contributor name is required.")
        self.assertMessage(response, "City is required.")
        self.assertFalse(StewardContribution.objects.exists())

    def test_steward_contribute_zero_or_negative_amount_rejected(self):
        for amount in ("0", "-5", ""):
            response = self._post(amount_dollars=amount)
            self.assertMessage(response, "greater than zero")
        self.assertFalse(StewardContribution.objects.exists())

    def test_steward_contribute_non_numeric_amount_rejected(self):
        response = self._post(amount_dollars="ten dollars")
        self.assertMessage(response, "Enter a valid dollar amount.")
        self.assertEqual(response.context["post"]["amount_dollars"], "ten dollars")

    # BUG: "NaN" parses as a Decimal and int(NaN) raises an uncaught ValueError (HTTP 500).
    @unittest.expectedFailure
    def test_steward_contribute_nan_amount_rejected_gracefully(self):
        response = self._post(amount_dollars="NaN")
        self.assertEqual(response.status_code, 200)

    def test_steward_contribute_success_rounds_half_up_and_records(self):
        response = self._post(amount_dollars="10.005")
        self.assertRedirects(response, "/steward/contribute/", fetch_redirect_response=False)
        contribution = StewardContribution.objects.get()
        self.assertEqual(contribution.amount_cents, 1001)
        self.assertEqual(contribution.steward_user, self.user)
        self.assertEqual(contribution.message, "hi")

    def test_steward_contribute_success_truncates_long_fields_and_blank_message_is_none(self):
        self._post(contributor_name="N" * 200, contributor_city="C" * 200, message="   ")
        contribution = StewardContribution.objects.get()
        self.assertEqual(len(contribution.contributor_name), 120)
        self.assertEqual(len(contribution.contributor_city), 120)
        self.assertIsNone(contribution.message)

    def test_steward_contribute_long_message_truncated_to_255(self):
        self._post(message="m" * 300)
        self.assertEqual(len(StewardContribution.objects.get().message), 255)


class FlagBookViewTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.book = make_book(self.seller)
        self.reporter = self.login(make_buyer())

    def test_flag_book_get_redirects_to_detail_without_creating(self):
        response = self.client.get(f"/catalog/{self.book.id}/flag/")
        self.assertRedirects(response, f"/catalog/{self.book.id}/", fetch_redirect_response=False)
        self.assertFalse(FlagReport.objects.exists())

    def test_flag_book_post_creates_report_targeting_seller(self):
        response = self.client.post(
            f"/catalog/{self.book.id}/flag/", {"flag_type": "counterfeit", "details": " fake copy "}
        )
        self.assertRedirects(response, f"/catalog/{self.book.id}/", fetch_redirect_response=False)
        flag = FlagReport.objects.get()
        self.assertEqual(flag.reporter_user, self.reporter)
        self.assertEqual(flag.target_user, self.seller)
        self.assertEqual(flag.target_book, self.book)
        self.assertEqual(flag.flag_type, "counterfeit")
        self.assertEqual(flag.details, "fake copy")
        self.assertEqual(flag.status, "open")
        self.assertMessage(response, "report was submitted")

    def test_flag_book_post_blank_type_defaults_to_other_and_details_none(self):
        self.client.post(f"/catalog/{self.book.id}/flag/", {"flag_type": "  ", "details": ""})
        flag = FlagReport.objects.get()
        self.assertEqual(flag.flag_type, "other")
        self.assertIsNone(flag.details)

    def test_flag_book_post_long_type_truncated_to_80(self):
        self.client.post(f"/catalog/{self.book.id}/flag/", {"flag_type": "x" * 100})
        self.assertEqual(len(FlagReport.objects.get().flag_type), 80)

    def test_flag_book_post_inactive_book_returns_404(self):
        hidden = make_book(self.seller, is_active=False)
        self.assertEqual(self.client.post(f"/catalog/{hidden.id}/flag/").status_code, 404)
        self.assertFalse(FlagReport.objects.exists())

    def test_flag_book_anonymous_redirects_to_login(self):
        self.client.logout()
        response = self.client.post(f"/catalog/{self.book.id}/flag/")
        self.assertTrue(response["Location"].startswith("/login/"))


class ServeBookCoverViewTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.images_dir = Path(tmp.name)
        (self.images_dir / "Cover.jpg").write_bytes(b"\xff\xd8fake-jpeg")
        patcher = mock.patch("General.views._BOOK_IMAGES_DIR", self.images_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_serve_book_cover_existing_file_returns_bytes(self):
        response = self.client.get("/book_covers/Cover.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"\xff\xd8fake-jpeg")
        response.close()

    def test_serve_book_cover_missing_file_returns_404(self):
        self.assertEqual(self.client.get("/book_covers/Nope.jpg").status_code, 404)

    def test_serve_book_cover_path_traversal_returns_404(self):
        for path in ("..%2Fsettings.py", "../secret", "sub/Cover.jpg", "a\\b.jpg", "..Cover.jpg"):
            self.assertEqual(self.client.get(f"/book_covers/{path}").status_code, 404, path)

    def test_serve_book_cover_directory_name_returns_404(self):
        (self.images_dir / "folder").mkdir()
        self.assertEqual(self.client.get("/book_covers/folder").status_code, 404)

    def test_serve_book_cover_missing_images_dir_returns_404(self):
        with mock.patch("General.views._BOOK_IMAGES_DIR", self.images_dir / "does-not-exist"):
            self.assertEqual(self.client.get("/book_covers/Cover.jpg").status_code, 404)


class ForgotPasswordEmailViewTests(TestCase):
    def test_forgot_password_email_get_renders_form(self):
        response = self.client.get("/forgot-password/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)

    def test_forgot_password_email_unknown_email_shows_error(self):
        response = self.client.post("/forgot-password/", {"email": "ghost@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("No account found with that email.", response.context["form"].errors["email"])
        self.assertNotIn("password_reset_user_id", self.client.session)

    def test_forgot_password_email_inactive_user_shows_error(self):
        make_buyer(email="frozen@example.com", is_active=False)
        response = self.client.post("/forgot-password/", {"email": "frozen@example.com"})
        self.assertIn("No account found with that email.", response.context["form"].errors["email"])

    def test_forgot_password_email_invalid_format_shows_field_error(self):
        response = self.client.post("/forgot-password/", {"email": "not-an-email"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["email"])

    def test_forgot_password_email_known_email_case_insensitive_sets_session(self):
        user = make_buyer(email="reset.me@example.com")
        response = self.client.post("/forgot-password/", {"email": "RESET.Me@Example.com"})
        self.assertRedirects(response, "/forgot-password/question/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["password_reset_user_id"], user.id)


class ForgotPasswordQuestionViewTests(TestCase):
    def setUp(self):
        self.user = set_security_question(make_buyer())

    def _start(self, user=None):
        _set_session(self.client, password_reset_user_id=(user or self.user).id)

    def _post(self, **overrides):
        data = {"answer": SECURITY_ANSWER, "new_password1": "Brand-new-pass-42", "new_password2": "Brand-new-pass-42"}
        data.update(overrides)
        return self.client.post("/forgot-password/question/", data)

    def test_forgot_password_question_without_session_redirects_to_email_step(self):
        response = self.client.get("/forgot-password/question/")
        self.assertRedirects(response, "/forgot-password/", fetch_redirect_response=False)

    def test_forgot_password_question_session_for_missing_user_returns_404(self):
        _set_session(self.client, password_reset_user_id=999999)
        self.assertEqual(self.client.get("/forgot-password/question/").status_code, 404)

    def test_forgot_password_question_user_without_question_flags_no_question_set(self):
        self._start(make_buyer())
        response = self.client.get("/forgot-password/question/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["no_question_set"])

    def test_forgot_password_question_get_shows_question_text(self):
        self._start()
        response = self.client.get("/forgot-password/question/")
        self.assertFalse(response.context["no_question_set"])
        self.assertEqual(response.context["security_question"], "In what city were you born?")

    def test_forgot_password_question_legacy_key_maps_to_question_text(self):
        """Older accounts store a short key (e.g. 'city'); the view maps it to display text."""
        legacy = set_security_question(make_buyer(), question="city")
        self._start(legacy)
        response = self.client.get("/forgot-password/question/")
        self.assertEqual(response.context["security_question"], "In what city were you born?")

    def test_forgot_password_question_wrong_answer_keeps_old_password(self):
        self._start()
        response = self._post(answer="Shelbyville")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Incorrect security answer.", response.context["form"].errors["answer"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_forgot_password_question_mismatched_passwords_rejected(self):
        self._start()
        response = self._post(new_password2="Different-pass-99")
        self.assertIn("Passwords do not match.", response.context["form"].errors["new_password2"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    # BUG: no AUTH_PASSWORD_VALIDATORS are configured, so validate_password() accepts "123" and the
    # reset flow allows passwords shorter than the 8-character minimum enforced at registration.
    @unittest.expectedFailure
    def test_forgot_password_question_weak_password_rejected_as_form_error(self):
        """A 3-character password should be rejected like it is at registration."""
        self._start()
        response = self._post(new_password1="123", new_password2="123")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_forgot_password_question_correct_answer_case_insensitive_resets_password(self):
        self._start()
        response = self._post(answer="  SPRINGFIELD ")
        self.assertRedirects(response, "/forgot-password/done/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-new-pass-42"))
        self.assertNotIn("password_reset_user_id", self.client.session)


class ForgotPasswordDoneViewTests(TestCase):
    def test_forgot_password_done_renders(self):
        response = self.client.get("/forgot-password/done/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "auth/forgot_password_done.html")


class AccountSecurityViewTests(PassItOnTestCase):
    def test_account_security_get_without_question_has_empty_initial(self):
        self.login(make_buyer())
        response = self.client.get("/account/security/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("security_question", response.context["form"].initial)

    def test_account_security_get_with_question_prefills_initial(self):
        self.login(set_security_question(make_buyer()))
        response = self.client.get("/account/security/")
        self.assertEqual(response.context["form"].initial["security_question"], "In what city were you born?")

    def test_account_security_post_valid_updates_hash_and_redirects(self):
        user = self.login(make_buyer())
        response = self.client.post(
            "/account/security/",
            {"security_question": "What is your mother's maiden name?", "security_answer": " Smith "},
        )
        self.assertRedirects(response, "/account/security/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.security_question, "What is your mother's maiden name?")
        self.assertTrue(check_password("smith", user.security_answer_hash))

    def test_account_security_post_invalid_rerenders_with_errors(self):
        user = self.login(make_buyer())
        response = self.client.post(
            "/account/security/", {"security_question": "Not a choice", "security_answer": "   "}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("security_question", response.context["form"].errors)
        self.assertIn("security_answer", response.context["form"].errors)
        user.refresh_from_db()
        self.assertIsNone(user.security_answer_hash)

    # BUG: account_security has no @login_required; anonymous access raises AttributeError (HTTP 500).
    @unittest.expectedFailure
    def test_account_security_anonymous_redirects_to_login(self):
        response = self.client.get("/account/security/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))
