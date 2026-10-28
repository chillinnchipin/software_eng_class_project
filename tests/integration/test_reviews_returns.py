"""Integration: delivery -> review flow, and return -> seller receipt -> credit/restock flow."""

import unittest

from django.test import Client

from Buyer.models import Order, ReturnRequest, Review, SellerReturnReceipt
from General.models import Inventory
from tests.helpers import (
    PassItOnTestCase,
    add_cart_item,
    make_address,
    make_admin,
    make_book,
    make_buyer,
    make_order,
    make_payment_method,
    make_seller,
)


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


class ReviewFlowTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.seller_client = client_for(self.seller)
        self.book = make_book(self.seller, price_cents=1500)
        self.buyer = self.login(make_buyer())

    def _buy_through_checkout(self):
        add_cart_item(self.buyer, self.book)
        self.client.post("/buyer/checkout/", {
            "shipping_address_id": make_address(self.buyer).id,
            "payment_method_id": make_payment_method(self.buyer).id,
            "steward_contribution": "0",
        })
        return Order.objects.get(user=self.buyer)

    def test_buy_deliver_review_then_visible_on_detail_compare_and_seller_dashboard(self):
        order = self._buy_through_checkout()
        detail = self.client.get(f"/buyer/orders/{order.id}/")
        self.assertFalse(detail.context["can_leave_review"])

        resp = self.seller_client.post(f"/seller/orders/{order.id}/status/update/", {"status": "Delivered"})
        self.assertRedirects(resp, f"/seller/orders/{order.id}/", fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")
        detail = self.client.get(f"/buyer/orders/{order.id}/")
        self.assertTrue(detail.context["can_leave_review"])

        item = order.order_items.get()
        resp = self.client.post(f"/buyer/reviews/submit/{item.id}/", {"rating": "5"})
        self.assertRedirects(resp, f"/buyer/orders/{order.id}/", fetch_redirect_response=False)
        review = Review.objects.get(order_item=item)
        self.assertEqual((review.user, review.book, review.rating), (self.buyer, self.book, 5))

        detail = self.client.get(f"/buyer/orders/{order.id}/")
        self.assertEqual(detail.context["order_items"][0]["saved_rating"], 5)
        self.assertEqual(list(self.client.get(f"/catalog/{self.book.id}/").context["reviews"]), [review])

        self.client.get(f"/compare/add/{self.book.id}/")
        compared = self.client.get("/compare/").context["compared_books"]
        self.assertEqual(compared[0]["rating_display"], "5.0 / 5")

        dash = self.seller_client.get("/seller/dashboard/")
        self.assertEqual((dash.context["seller_avg_rating"], dash.context["seller_review_count"]), (5.0, 1))
        seller_detail = self.seller_client.get(f"/seller/orders/{order.id}/")
        self.assertEqual(seller_detail.context["items"][0].existing_review, review)

    def test_rereview_same_item_updates_rating_instead_of_duplicating(self):
        order = make_order(self.buyer, [(self.book, 1)], status="delivered")
        item = order.order_items.get()
        self.client.post(f"/buyer/reviews/submit/{item.id}/", {"rating": "2"})
        self.client.post(f"/buyer/reviews/submit/{item.id}/", {"rating": "4"})
        self.assertEqual(list(Review.objects.filter(order_item=item).values_list("rating", flat=True)), [4])
        dash = self.seller_client.get("/seller/dashboard/")
        self.assertEqual(dash.context["seller_avg_rating"], 4.0)

    def test_review_out_of_range_rating_ignored(self):
        order = make_order(self.buyer, [(self.book, 1)], status="delivered")
        item = order.order_items.get()
        for bad in ("0", "6"):
            self.client.post(f"/buyer/reviews/submit/{item.id}/", {"rating": bad})
        self.assertFalse(Review.objects.exists())

    def test_review_other_buyers_item_returns_404(self):
        order = make_order(make_buyer(), [(self.book, 1)], status="delivered")
        resp = self.client.post(f"/buyer/reviews/submit/{order.order_items.get().id}/", {"rating": "5"})
        self.assertEqual(resp.status_code, 404)

    def test_multiple_buyers_reviews_average_on_compare_page(self):
        for rating in (3, 4):
            buyer = make_buyer()
            order = make_order(buyer, [(self.book, 1)], status="delivered")
            client_for(buyer).post(f"/buyer/reviews/submit/{order.order_items.get().id}/", {"rating": rating})
        self.client.get(f"/compare/add/{self.book.id}/")
        self.assertEqual(self.client.get("/compare/").context["compared_books"][0]["rating_display"], "3.5 / 5")


class ReturnFlowTests(PassItOnTestCase):
    def setUp(self):
        self.seller = make_seller()
        self.seller_client = client_for(self.seller)
        self.book = make_book(self.seller, price_cents=2000, stock=1)
        self.buyer = self.login(make_buyer())

    def _request_return(self, order, **data):
        payload = {"reason_category": "damaged", "reason_details": "Spine cracked"}
        payload.update(data)
        return self.client.post(f"/buyer/orders/{order.id}/return/", payload)

    def test_return_request_seller_receipt_credits_buyer_restocks_and_completes(self):
        order = make_order(self.buyer, [(self.book, 2)], status="delivered")
        self.assertTrue(self.client.get(f"/buyer/orders/{order.id}/").context["can_request_return"])

        resp = self._request_return(order)
        self.assertRedirects(resp, f"/buyer/orders/{order.id}/", fetch_redirect_response=False)
        rr = ReturnRequest.objects.get(order=order)
        self.assertEqual(rr.reason, "[damaged]\nSpine cracked")
        detail = self.client.get(f"/buyer/orders/{order.id}/")
        self.assertEqual(detail.context["return_status_label"], "Return requested")
        self.assertFalse(detail.context["can_request_return"])

        self.assertEqual(list(self.seller_client.get("/seller/returns/").context["return_requests"]), [rr])
        self.assertEqual(self.seller_client.get("/seller/dashboard/").context["stats"]["pending_returns_count"], 1)
        admin_returns = client_for(make_admin()).get("/staff/returns/").context["returns"]
        self.assertEqual(list(admin_returns), [rr])

        page = self.seller_client.get(f"/seller/returns/{rr.id}/")
        self.assertEqual(page.context["seller_lines_total_display"], "40.00")
        self.assertIsNone(page.context["already_received"])

        resp = self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        self.assertRedirects(resp, f"/seller/returns/{rr.id}/", fetch_redirect_response=False)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 4000)
        self.assertEqual(Inventory.objects.get(book=self.book).quantity_available, 3)
        rr.refresh_from_db()
        self.assertEqual(rr.status, "refunded")
        self.assertEqual(SellerReturnReceipt.objects.get(return_request=rr).amount_credited_cents, 4000)

        self.assertEqual(self.client.get(f"/buyer/orders/{order.id}/").context["return_status_label"],
                         "Return complete")
        self.assertEqual(self.client.get("/buyer/dashboard/").context["credit_balance"], "40.00")
        self.assertEqual(list(self.seller_client.get("/seller/returns/").context["return_requests"]), [])
        page = self.seller_client.get(f"/seller/returns/{rr.id}/")
        self.assertEqual(page.context["amount_credited_display"], "40.00")

    def test_return_seller_receipt_twice_does_not_double_credit(self):
        order = make_order(self.buyer, [(self.book, 1)])
        self._request_return(order)
        rr = ReturnRequest.objects.get(order=order)
        self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        resp = self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        self.assertMessage(resp, "already marked this return as received")
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 2000)
        self.assertEqual(SellerReturnReceipt.objects.count(), 1)

    def test_return_multi_seller_order_finalizes_only_after_every_seller_acknowledges(self):
        seller2 = make_seller()
        book2 = make_book(seller2, price_cents=700, stock=0)
        order = make_order(self.buyer, [(self.book, 1), (book2, 1)])
        self._request_return(order)
        rr = ReturnRequest.objects.get(order=order)

        self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        rr.refresh_from_db()
        self.buyer.refresh_from_db()
        self.assertEqual((rr.status, self.buyer.store_credit_cents), ("requested", 2000))
        self.assertEqual(self.client.get(f"/buyer/orders/{order.id}/").context["return_status_label"],
                         "Return requested")
        # Still pending for seller 2, no longer pending for seller 1.
        seller2_client = client_for(seller2)
        self.assertEqual(list(seller2_client.get("/seller/returns/").context["return_requests"]), [rr])
        self.assertEqual(list(self.seller_client.get("/seller/returns/").context["return_requests"]), [])

        seller2_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        rr.refresh_from_db()
        self.buyer.refresh_from_db()
        self.assertEqual((rr.status, self.buyer.store_credit_cents), ("refunded", 2700))
        self.assertEqual(Inventory.objects.get(book=book2).quantity_available, 1)

    def test_return_duplicate_request_blocked(self):
        order = make_order(self.buyer, [(self.book, 1)])
        self._request_return(order)
        resp = self._request_return(order, reason_details="again")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already been submitted", resp.context["return_error"])
        self.assertEqual(ReturnRequest.objects.filter(order=order).count(), 1)

    def test_return_empty_reason_rejected(self):
        order = make_order(self.buyer, [(self.book, 1)])
        resp = self._request_return(order, reason_category="", reason_details="  ")
        self.assertEqual(resp.context["return_error"], "Please describe your return reason.")
        self.assertFalse(ReturnRequest.objects.exists())

    def test_return_seller_without_lines_in_order_cannot_process_it(self):
        order = make_order(self.buyer, [(self.book, 1)])
        self._request_return(order)
        rr = ReturnRequest.objects.get(order=order)
        stranger = client_for(make_seller())
        self.assertEqual(list(stranger.get("/seller/returns/").context["return_requests"]), [])
        resp = stranger.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        self.assertRedirects(resp, "/seller/returns/", fetch_redirect_response=False)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.store_credit_cents, 0)

    # BUG: return_request_detail only enters its POST branch when action == "book_received", so the
    # "approve"/"deny" branches are unreachable and a seller can never reject a return.
    @unittest.expectedFailure
    def test_return_seller_denies_request_sets_status_rejected(self):
        order = make_order(self.buyer, [(self.book, 1)])
        self._request_return(order)
        rr = ReturnRequest.objects.get(order=order)
        self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "deny"})
        rr.refresh_from_db()
        self.assertEqual(rr.status, "rejected")

    # BUG: after "book received" the success message says the seller's sales total was adjusted, but
    # _seller_sales_total_cents only filters on order status, so dashboard total_sales is unchanged.
    @unittest.expectedFailure
    def test_return_seller_receipt_reduces_dashboard_total_sales(self):
        order = make_order(self.buyer, [(self.book, 1)])
        self.assertEqual(self.seller_client.get("/seller/dashboard/").context["total_sales"], "20.00")
        self._request_return(order)
        rr = ReturnRequest.objects.get(order=order)
        self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        self.assertEqual(self.seller_client.get("/seller/dashboard/").context["total_sales"], "0.00")

    # BUG: sales_overview's Exists() subquery uses OuterRef("id") (the OrderItem id) instead of the
    # order id, so returned lines stay in the per-book sales table whenever the ids differ.
    @unittest.expectedFailure
    def test_return_seller_receipt_excludes_returned_lines_from_sales_overview(self):
        other_seller = make_seller()
        # A two-line order for another seller shifts OrderItem ids away from Order ids.
        make_order(make_buyer(), [(make_book(other_seller), 1), (make_book(other_seller), 1)])
        order = make_order(self.buyer, [(self.book, 1)])
        self.assertNotEqual(order.id, order.order_items.get().id)
        self._request_return(order)
        rr = ReturnRequest.objects.get(order=order)
        self.seller_client.post(f"/seller/returns/{rr.id}/", {"action": "book_received"})
        overview = self.seller_client.get("/seller/dashboard/sales-overview/")
        self.assertEqual(overview.context["book_sales"], [])
