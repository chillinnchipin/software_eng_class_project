# PassItOnBooks test suite

768 tests: unit, integration, and use-case tests for the four Django apps (`General`, `Buyer`, `Seller`, `Admin`).
They use Django's built-in `unittest`-style test runner (`django.test.TestCase`). There are no extra test frameworks.
No application code was modified to write them.

Current result: **768 tests, OK (expected failures = 38)**. Line coverage of application code is **96.4%** (95.3% when branches are included).

## How to run

All commands run from the project root inside the venv.

```bash
# one-time setup (Python 3.12)
python3.12 -m venv .venv            # or: uv venv .venv --python 3.12
.venv/bin/pip install -r tests/requirements.txt   # app requirements + coverage

# FULL SUITE (the single documented command)
.venv/bin/python manage.py test tests --settings=tests.settings

# subsets
.venv/bin/python manage.py test tests.unit         --settings=tests.settings
.venv/bin/python manage.py test tests.integration  --settings=tests.settings
.venv/bin/python manage.py test tests.use_cases    --settings=tests.settings

# one file / class / test
.venv/bin/python manage.py test tests.unit.test_views_buyer --settings=tests.settings
.venv/bin/python manage.py test tests.use_cases.test_buyer_use_cases.UC04BuyerBuysBook --settings=tests.settings

# faster on multi-core machines
.venv/bin/python manage.py test tests --settings=tests.settings --parallel auto

# coverage
.venv/bin/python -m coverage run --rcfile=tests/.coveragerc manage.py test tests --settings=tests.settings
.venv/bin/python -m coverage report --rcfile=tests/.coveragerc      # or: html --rcfile=tests/.coveragerc
```

With Docker, add `-r tests/requirements.txt` to the image's pip install. Then run `python manage.py test tests --settings=tests.settings` in the container (the default entrypoint starts the server instead).

`tests/settings.py` imports `config.settings.settings` and changes two things for tests: a fast MD5 password hasher and silenced logging. Django creates a throw-away in-memory SQLite DB and runs all migrations, including the one that seeds `StewardPool`. The real `db.sqlite3` is never touched.

## Layout and conventions

| Path | Contents |
|---|---|
| `tests/helpers.py` | Factories (`make_buyer`, `make_seller`, `make_admin`, `make_steward`, `make_book`, `make_order`, `make_return`, `make_review`, `make_flag`, `add_cart_item`, `make_address`, `make_payment_method`, `ensure_pool`, …) and `PassItOnTestCase` (`login`, `login_via_form`, `assertMessage`) |
| `tests/unit/test_models.py` | Every model: `__str__`, properties, manager, constraints, defaults, `on_delete`, admin display helper |
| `tests/unit/test_forms.py` | `General/forms.py`, the duplicate forms in `General/views.py`, `SellerWebhookForm` |
| `tests/unit/test_utils.py` | `General/steward.py`, `Buyer/cart_helpers.py`, `Seller/webhook_notify.py`, private view helpers, `staff_required`, `NoCacheMiddleware`, `create_admin` command |
| `tests/unit/test_views_{general,buyer,seller,admin}.py` | One test class per view: every view, branch and error path |
| `tests/integration/test_auth_permissions.py` | Register → approval → login, JWT, role access matrix, freeze, forgot-password, session cart merge |
| `tests/integration/test_cart_checkout.py` | Cart → address → payment → checkout, multi-seller, atomic rollback, steward progress and free book, webhooks |
| `tests/integration/test_inventory.py` | Seller listing lifecycle ↔ catalog ↔ cart ↔ admin freeze ↔ stock stats |
| `tests/integration/test_reviews_returns.py` | Delivery → review → ratings everywhere; return → seller receipt → credit/restock/refund |
| `tests/integration/test_admin_approval.py` | Approval queues, approve endpoints, freeze/unfreeze, flags → admin reports |
| `tests/use_cases/test_{buyer,seller,admin}_use_cases.py` | One `UC<NN>…` class per use case, driven through the real URLs |

- **Names** follow `test_<unit>_<scenario>_<expected result>`. Non-obvious tests have one-line docstrings.
- **Known bugs** are tests that assert the *intended* behaviour, decorated with `@unittest.expectedFailure` and a `# BUG:` comment. Each one was checked with the decorator removed: it fails because of the bug, not because of setup. Once a bug is fixed, the runner reports "unexpected success"; remove the decorator then.
- **Isolation:** tests make no network calls (`requests.post` is mocked, and `transaction.on_commit` webhooks only run when explicitly captured). Cover uploads go to a temp `BASE_DIR`, never the repo's `media/book_images/`.

## Use-case traceability

| UC | Use case | Use-case test class (`tests/use_cases/…`) | # tests | Supporting integration tests |
|---|---|---|---|---|
| 01 | Buyer creates account | `UC01BuyerCreatesAccount` (`test_buyer_use_cases.py`) | 7 (1 xfail) | `test_auth_permissions.py`, `test_admin_approval.py` |
| 02 | Buyer signs into account | `UC02BuyerSignsIn` (`test_buyer_use_cases.py`) | 7 | `test_auth_permissions.py` |
| 03 | Buyer browses/searches for a book | `UC03BuyerBrowsesAndSearches` (`test_buyer_use_cases.py`) | 10 | `test_inventory.py` |
| 04 | Buyer buys a book | `UC04BuyerBuysBook` (`test_buyer_use_cases.py`) | 7 | `test_cart_checkout.py` |
| 05 | Buyer reviews a book | `UC05BuyerReviewsBook` (`test_buyer_use_cases.py`) | 7 (2 xfail) | `test_reviews_returns.py` |
| 06 | Buyer returns a book | `UC06BuyerReturnsBook` (`test_buyer_use_cases.py`) | 6 (1 xfail) | `test_reviews_returns.py` |
| 07 | Seller creates account | `UC07SellerCreatesAccount` (`test_seller_use_cases.py`) | 5 | `test_auth_permissions.py`, `test_admin_approval.py` |
| 08 | Seller signs into account | `UC08SellerSignsIn` (`test_seller_use_cases.py`) | 5 | `test_auth_permissions.py` |
| 09 | Seller adds books to storefront | `UC09SellerAddsBooks` (`test_seller_use_cases.py`) | 6 | `test_inventory.py` |
| 10 | Seller removes books from storefront | `UC10SellerRemovesBooks` (`test_seller_use_cases.py`) | 6 | `test_inventory.py` |
| 11 | Seller views orders | `UC11SellerViewsOrders` (`test_seller_use_cases.py`) | 9 (2 xfail) | `test_cart_checkout.py` (multi-seller) |
| 12 | Admin approves new user accounts | `UC12AdminApprovesAccounts` (`test_admin_use_cases.py`) | 8 | `test_admin_approval.py` |
| 13 | Admin checks for suspicious activity | `UC13AdminChecksSuspiciousActivity` (`test_admin_use_cases.py`) | 10 (1 xfail) | `test_admin_approval.py` |

Every use case has passing tests: both a happy path and negative/edge cases.

## Assumptions

1. **Python 3.12:** the host had only Python 3.14 and the Docker daemon wasn't responding. `.venv` was created with a uv-managed CPython 3.12.15. Nothing in the suite depends on that interpreter specifically.
2. **"Seller removes books" means unpublishing.** There is no delete endpoint. Removal is `manage_inventory` POST with `is_active` unchecked, which hides the listing and keeps order history intact.
3. **"Admin checks for suspicious activity"** means the flag-report page (`/staff/reports/`), the dashboard flag panels and open-flag count, the audit log, and freezing users or books. `/staff/moderation/abuse/` is broken (bug 21), so it is covered only by an expected failure.
4. **"Admin approves new accounts"** covers both buyer and seller approval. `/login/` blocks unapproved buyers. Unapproved sellers can log in but are routed to the buyer area.
5. **Returns:** the buyer files a request, and each seller in the order confirms "book received". That credits the buyer's store credit, restocks inventory, and marks the return `refunded` once every seller has confirmed. Admin has a read-only view.
6. **Inferring intended behaviour for expected failures:** where the code is ambiguous, the intended rule comes from the app's own UI and messages. Examples: reviews are only offered for delivered orders, returns only for paid/shipped/delivered orders, and the "your sales total was adjusted" message.
7. **Steward features** (contributions, progress, free book) are not a listed use case. They are covered in unit and integration tests because checkout depends on them.
8. **Coverage scope:** the four app packages plus `passiton/`, excluding migrations and the `wsgi.py`/`asgi.py` deployment entry points. Templates are exercised by rendering but are not measured.

## Bugs found (38 expected-failure tests, 24 distinct defects)

| # | Area | Defect | Effect | Tests (`expectedFailure`) |
|---|---|---|---|---|
| 1 | `Buyer/views.py` checkout | Calls `request.user.get_full_name()`, which the custom `User` (AbstractBaseUser) doesn't have | **Checkout returns 500 when the shipping address has no label**, and the label is optional | `unit/test_views_buyer.py::BuyerCheckoutPostTests::test_checkout_post_unlabelled_address_places_order` |
| 2 | `Seller/webhook_notify.py` | Uses `order.orderitem` / prefetch `"orderitem"`, but the related_name is `order_items` | Seller order webhooks are **never sent**. The error is swallowed by `except Exception` | `unit/test_utils.py::WebhookPayloadHelperTests::test_build_order_placed_payload_lists_only_this_sellers_titles`, `unit/test_utils.py::SendOrderPlacedWebhooksTests::test_send_webhooks_enabled_seller_receives_post`, `integration/test_cart_checkout.py::OrderWebhookFlowTests::test_checkout_with_seller_webhook_enabled_posts_order_placed_event` |
| 3 | `Seller/views.py` `return_request_detail` | The approve/deny branches are nested under `action == "book_received"` | Sellers can **never approve or reject** a return | `unit/test_views_seller.py::ReturnRequestDetailViewTests::test_return_detail_approve_sets_status_approved`, `…::test_return_detail_deny_sets_status_rejected`, `integration/test_reviews_returns.py::ReturnFlowTests::test_return_seller_denies_request_sets_status_rejected` |
| 4 | `Seller/views.py` `sales_overview` | `OuterRef("id")` is the OrderItem id but is compared with the order id | Returned lines stay in per-book sales | `unit/test_views_seller.py::SalesOverviewViewTests::test_sales_overview_excludes_lines_from_received_returns`, `integration/test_reviews_returns.py::ReturnFlowTests::test_return_seller_receipt_excludes_returned_lines_from_sales_overview` |
| 5 | `Seller/views.py` `_seller_sales_total_cents` | Ignores `SellerReturnReceipt`, although the success message says the total was adjusted | The dashboard total is not reduced after a return | `integration/test_reviews_returns.py::ReturnFlowTests::test_return_seller_receipt_reduces_dashboard_total_sales` |
| 6 | `Seller/views.py` `orders` | The queryset is rebuilt after filtering | `?status=OPEN` is silently ignored | `unit/test_views_seller.py::SellerOrdersViewTests::test_orders_open_filter_excludes_delivered`, `use_cases/test_seller_use_cases.py::UC11SellerViewsOrders::test_seller_orders_open_filter_excludes_delivered` |
| 7 | `Seller/views.py` `update_order_status` | Redirects to `" seller_order_details"` (leading space) | An invalid status returns 500 (`NoReverseMatch`) | `unit/test_views_seller.py::UpdateOrderStatusViewTests::test_update_status_invalid_status_redirects_with_error`, `use_cases/test_seller_use_cases.py::UC11SellerViewsOrders::test_seller_invalid_status_redirects_back_with_error` |
| 8 | `Seller/views.py` `seller_profile` | `messages.sucess` typo | Every successful profile save returns 500 | `unit/test_views_seller.py::SellerProfileViewTests::test_profile_update_success_saves_and_redirects` |
| 9 | `Seller/views.py` `seller_profile` | A blank phone becomes `None`, then `None[:50]` | 500 (`TypeError`) | `unit/test_views_seller.py::SellerProfileViewTests::test_profile_update_blank_phone_saves_none` |
| 10 | `Buyer/views.py` `review_submission` | Unguarded `int(request.POST["rating"])` | A non-numeric rating returns 500 | `unit/test_views_buyer.py::ReviewSubmissionViewTests::test_review_non_numeric_rating_redirects_without_review`, `use_cases/test_buyer_use_cases.py::UC05BuyerReviewsBook::test_review_non_numeric_rating_redirects_without_error` |
| 11 | `Buyer/views.py` `review_submission` | No order-status check (the UI shows reviews only for delivered orders) | Reviews accepted before delivery | `unit/test_views_buyer.py::ReviewSubmissionViewTests::test_review_undelivered_order_is_refused`, `use_cases/test_buyer_use_cases.py::UC05BuyerReviewsBook::test_review_undelivered_order_rejected_server_side` |
| 12 | `Buyer/views.py` `return_request_view` | No order-status check (`order_detail` allows returns only for paid/shipped/delivered) | Returns accepted for cancelled/pending orders | `unit/test_views_buyer.py::ReturnRequestViewTests::test_return_cancelled_order_is_refused`, `use_cases/test_buyer_use_cases.py::UC06BuyerReturnsBook::test_return_request_cancelled_order_rejected` |
| 13 | `Buyer/views.py` `_parse_steward_contribution_dollars` | `Decimal("NaN") < 0` raises `InvalidOperation` | A steward contribution of "NaN" at checkout returns 500 | `unit/test_utils.py::StewardContributionParsingTests::test_parse_contribution_nan_invalid` |
| 14 | `General/views.py` `steward_contribute` | `int(Decimal("NaN"))` raises `ValueError` | An amount of "NaN" returns 500 | `unit/test_views_general.py::StewardContributeViewTests::test_steward_contribute_nan_amount_rejected_gracefully` |
| 15 | `General/views.py` `register` | Logs a new buyer in immediately | Bypasses the admin-approval gate that `/login/` enforces | `use_cases/test_buyer_use_cases.py::UC01BuyerCreatesAccount::test_register_buyer_pending_approval_not_logged_in` |
| 16 | `General/views.py` `account_security` | No `@login_required` | Anonymous visit returns 500 (`AttributeError`) | `unit/test_views_general.py::AccountSecurityViewTests::test_account_security_anonymous_redirects_to_login` |
| 17 | `passiton/settings.py` | No `AUTH_PASSWORD_VALIDATORS` | Forgot-password reset accepts "123", although registration requires 8+ characters | `unit/test_views_general.py::ForgotPasswordQuestionViewTests::test_forgot_password_question_weak_password_rejected_as_form_error` |
| 18 | `General/views.py` `email_verification` | Renders `auth/email_verification.html`; the file is `auth/email_verif.html` | 500 (`TemplateDoesNotExist`) | `unit/test_views_general.py::EmailVerificationViewTests::test_email_verification_get_renders_page` |
| 19 | `General/views.py` `profile_settings` | `auth/{buyer,seller,admin}_profile.html` don't exist | 500 for every role | `unit/test_views_general.py::ProfileSettingsViewTests::test_profile_settings_{buyer,seller,admin}_get_renders_page` |
| 20 | `General/views.py` `cart` / `checkout` (`/cart/`, `/checkout/`) | Render the missing `catalog/cart.html`. The URL names `cart`/`checkout` are also defined twice and `reverse()` resolves to the Buyer views | 500 at `/cart/` and `/checkout/` | `unit/test_views_general.py::GeneralCartAndCheckoutViewTests::test_general_cart_authenticated_renders_page`, `…::test_general_checkout_authenticated_renders_page` |
| 21 | `Admin/templates/moderation/abuseDetection.html` | `=="HIGH"` with no spaces | `/staff/moderation/abuse/` returns 500 (`TemplateSyntaxError`) | `unit/test_views_admin.py::StaticAdminPagesViewTests::test_abuse_detection_renders`, `use_cases/test_admin_use_cases.py::UC13AdminChecksSuspiciousActivity::test_abuse_detection_page_renders` |
| 22 | `Admin/templates/users/userMonitering.html` | `{$ if … %}` typo leaves an orphan `{% endif %}` | `/staff/users/` returns 500 | `unit/test_views_admin.py::StaticAdminPagesViewTests::test_user_monitoring_renders` |
| 23 | `Admin/templates/disputes/returnDisputes.html` | Unterminated `{% url 'admin_stewards %}` | `/staff/disputes/` returns 500 | `unit/test_views_admin.py::StaticAdminPagesViewTests::test_return_disputes_renders` |
| 24 | `Admin/views.py` `admin_settings` | `ctx["passwrod_error"] - " ".join(errs)` (typo plus minus sign) | A wrong current password returns 500 (`KeyError`) | `unit/test_views_admin.py::AdminSettingsViewTests::test_admin_settings_password_wrong_current_shows_error` |

**Other observations** (no expected-failure test, either because the behaviour is current-by-design or because no test can reach it):
- `/api/token/` issues JWTs to buyers who are not approved, skipping the approval gate. Nothing in the app consumes those tokens yet.
- The admin dashboard always shows the admin's email instead of their name. This is the same missing `get_full_name()` as bug 1, but the code falls back to the email without crashing, so the tests assert the current output.
- `General/views.py:29` references `settings.BASE_dsDIR`, a typo. It only runs if `book_images/` is missing, and would then crash on import.
- The anonymous session-cart branches in `add_to_cart`, `remove_cart_item`, `update_cart_item`, `buyer_cart` and `Buyer.views.home` are dead code, because all these views are `@login_required`. Session carts are only merged via `merge_session_cart_into_db`, which is tested.
- `add_to_compare` sets `request.session.mofified` (typo). It's harmless because assigning the key already marks the session modified.

## Coverage summary

Measured with coverage.py 7.16.2 (`branch = True`; see `tests/.coveragerc`).

| Module | Stmts | Miss | Line cover | Cover incl. branches |
|---|---:|---:|---:|---:|
| `Admin/views.py` | 247 | 0 | 100.0% | 98.3% |
| `Admin/management/commands/create_admin.py` | 29 | 0 | 100.0% | 100.0% |
| `Buyer/views.py` | 709 | 34 | 95.2% | 94.1% |
| `Buyer/cart_helpers.py` | 64 | 0 | 100.0% | 100.0% |
| `Buyer/models.py` | 147 | 0 | 100.0% | 100.0% |
| `General/views.py` | 411 | 8 | 98.1% | 98.0% |
| `General/forms.py` | 52 | 1 | 98.1% | 95.2% |
| `General/models.py` | 126 | 0 | 100.0% | 100.0% |
| `General/steward.py` | 87 | 0 | 100.0% | 100.0% |
| `General/middleware.py` | 10 | 0 | 100.0% | 100.0% |
| `Seller/views.py` | 492 | 20 | 95.9% | 95.4% |
| `Seller/forms.py`, `Seller/models.py` | 28 | 0 | 100.0% | 100.0% |
| `Seller/webhook_notify.py` | 85 | 29 | 65.9% | 56.6% |
| `passiton/settings.py` | 33 | 1 | 97.0% | 89.7% |
| admin/apps/urls/models (remaining) | 91 | 0 | 100.0% | 100.0% |
| **TOTAL** | **2611** | **93** | **96.4%** | **95.3%** |

Almost all of the uncovered remainder can't be reached by any test:
- `webhook_notify.py` lines 76–85 and 135–157 sit behind bug 2.
- `Seller/views.py` lines 755–772 are the dead approve/deny branches (bug 3).
- The `Buyer/views.py` gaps are the anonymous-cart branches behind `@login_required`.
