from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Order
from orders.services import create_paid_order, parse_metadata_items, reduce_stock
from orders.views import _create_order_from_checkout_session
from products.models import Brand, Category, Product, ProductVariant


class CheckoutTests(TestCase):
    def test_checkout_redirects_empty_cart(self):
        resp = self.client.get(reverse("orders:checkout"))
        self.assertRedirects(resp, reverse("cart:cart_detail"))


class FakeStripeObject(dict):
    # Stripe objects allow both obj["key"] and obj.key.
    __getattr__ = dict.__getitem__


def make_variant(size: str, stock_qty: int) -> ProductVariant:
    brand, _ = Brand.objects.get_or_create(name="Test", country="UK")
    cat, _ = Category.objects.get_or_create(name="Test", slug="test")
    product, _ = Product.objects.get_or_create(
        slug="stock-prod", defaults={"name": "Stock Product", "brand": brand, "category": cat, "description": "Test"}
    )
    sku = f"STOCK-{size}"
    return ProductVariant.objects.create(
        product=product, size=size, color="Black", sku=sku, price=Decimal("10.00"), stock_qty=stock_qty
    )


def checkout_session(session_id: str, items: str, amount_total: int = 2000) -> FakeStripeObject:
    return FakeStripeObject(
        id=session_id,
        payment_intent="pi_test",
        payment_status="paid",
        metadata={"items": items},
        customer_email="buyer@example.com",
        amount_total=amount_total,
    )


class StockReductionTests(TestCase):
    def setUp(self):
        self.variant = make_variant("M", stock_qty=5)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("orders.views.stripe.Webhook.construct_event")
    def test_webhook_order_reduces_stock(self, construct_event):
        construct_event.return_value = {
            "id": "evt_test",
            "type": "checkout.session.completed",
            "data": {"object": checkout_session("cs_test_1", f"{self.variant.id}:2")},
        }
        resp = self.client.post(reverse("orders:stripe_webhook"), data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 3)

    def test_short_stock_still_saves_order_and_logs_warning(self):
        with self.assertLogs("orders.services", level="WARNING") as logs:
            _create_order_from_checkout_session(checkout_session("cs_test_2", f"{self.variant.id}:8"))
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 0)
        self.assertIn("in stock", logs.output[0])

    def test_failure_rolls_back_order_and_stock(self):
        other = make_variant("L", stock_qty=5)
        items = f"{self.variant.id}:1,{other.id}:1"
        calls = []

        def reduce_then_fail(variant, quantity, order):
            # The first item really reduces stock; the second fails, so it must be rolled back.
            calls.append(variant.id)
            if len(calls) == 1:
                return reduce_stock(variant, quantity, order)
            raise RuntimeError("boom")

        with patch("orders.services.reduce_stock", side_effect=reduce_then_fail):
            with self.assertRaises(RuntimeError):
                _create_order_from_checkout_session(checkout_session("cs_test_3", items))
        self.assertEqual(len(calls), 2)
        self.assertEqual(Order.objects.count(), 0)
        self.variant.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 5)
        self.assertEqual(other.stock_qty, 5)

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("orders.views.stripe.checkout.Session.retrieve")
    def test_success_page_order_reduces_stock(self, retrieve):
        retrieve.return_value = checkout_session("cs_test_4", "")
        self.client.post(reverse("cart:cart_add", args=[self.variant.id]), {"quantity": 3})
        self.client.get(reverse("orders:checkout_success"), {"session_id": "cs_test_4"})
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 2)


ADDRESS = {
    "line1": "1 Test Street",
    "line2": "Flat 2",
    "city": "London",
    "state": "",
    "postal_code": "N1 1AA",
    "country": "GB",
}


class ShippingAddressTests(TestCase):
    def setUp(self):
        self.variant = make_variant("S", stock_qty=5)

    def assert_address_saved(self, order: Order) -> None:
        self.assertEqual(order.first_name, "Jane")
        self.assertEqual(order.last_name, "Doe Smith")
        self.assertEqual(order.address_line1, "1 Test Street")
        self.assertEqual(order.address_line2, "Flat 2")
        self.assertEqual(order.city, "London")
        self.assertEqual(order.postal_code, "N1 1AA")
        self.assertEqual(order.country, "GB")

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("orders.views.stripe.Webhook.construct_event")
    def test_webhook_saves_shipping_address(self, construct_event):
        session = checkout_session("cs_ship_1", f"{self.variant.id}:1")
        session["collected_information"] = {
            "shipping_details": {"name": "Jane Doe Smith", "address": ADDRESS},
        }
        construct_event.return_value = {
            "id": "evt_ship",
            "type": "checkout.session.completed",
            "data": {"object": session},
        }
        resp = self.client.post(reverse("orders:stripe_webhook"), data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assert_address_saved(Order.objects.get(stripe_session_id="cs_ship_1"))

    def test_webhook_reads_older_api_shipping_field(self):
        session = checkout_session("cs_ship_2", f"{self.variant.id}:1")
        session["shipping_details"] = {"name": "Jane Doe Smith", "address": ADDRESS}
        _create_order_from_checkout_session(session)
        self.assert_address_saved(Order.objects.get(stripe_session_id="cs_ship_2"))

    def test_webhook_fills_address_on_order_from_success_page(self):
        Order.objects.create(
            stripe_session_id="cs_ship_3", email="buyer@example.com", first_name="", last_name="",
            address_line1="", city="", state="", postal_code="", total=Decimal("10.00"), status="paid",
        )
        session = checkout_session("cs_ship_3", f"{self.variant.id}:1")
        session["collected_information"] = {
            "shipping_details": {"name": "Jane Doe Smith", "address": ADDRESS},
        }
        _create_order_from_checkout_session(session)
        self.assertEqual(Order.objects.count(), 1)
        self.assert_address_saved(Order.objects.get(stripe_session_id="cs_ship_3"))

    def test_session_without_address_leaves_fields_blank(self):
        _create_order_from_checkout_session(checkout_session("cs_ship_4", f"{self.variant.id}:1"))
        order = Order.objects.get(stripe_session_id="cs_ship_4")
        self.assertEqual(order.address_line1, "")
        self.assertEqual(order.first_name, "")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_SHIPPING_COUNTRIES=["US", "GB", "CA"])
    @patch("orders.views.stripe.checkout.Session.create")
    def test_checkout_session_asks_for_shipping_address(self, create):
        create.return_value = FakeStripeObject(url="https://checkout.stripe.test/pay")
        self.client.post(reverse("cart:cart_add", args=[self.variant.id]), {"quantity": 1})
        self.client.post(reverse("orders:create_checkout_session"))
        create.assert_called_once()
        self.assertEqual(
            create.call_args.kwargs["shipping_address_collection"],
            {"allowed_countries": ["US", "GB", "CA"]},
        )


class CreatePaidOrderTests(TestCase):
    def setUp(self):
        self.variant = make_variant("M", stock_qty=5)

    def test_creates_order_with_items_and_reduces_stock(self):
        order = create_paid_order(
            [(str(self.variant.id), 2)], email="buyer@example.com", total=Decimal("20.00"),
            stripe_session_id="cs_new_1",
        )
        self.assertEqual(order.status, "paid")
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(order.items.get().quantity, 2)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 3)

    def test_same_payment_returns_existing_order(self):
        first = create_paid_order(
            [(str(self.variant.id), 1)], email="", total=Decimal("10.00"), stripe_session_id="cs_new_2",
        )
        second = create_paid_order(
            [(str(self.variant.id), 1)], email="", total=Decimal("10.00"), stripe_session_id="cs_new_2",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 4)

    def test_skips_unknown_variants_and_zero_quantities(self):
        order = create_paid_order(
            [(str(self.variant.id), 0), ("999999", 1)], email="", total=Decimal("0.00"),
            stripe_payment_intent_id="pi_new_3",
        )
        self.assertEqual(order.items.count(), 0)
        self.assertEqual(order.email, "guest@example.com")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 5)

    def test_saves_shipping_fields(self):
        order = create_paid_order(
            [(str(self.variant.id), 1)], email="", total=Decimal("10.00"), stripe_session_id="cs_new_4",
            shipping={"first_name": "Jane", "city": "London", "country": "GB"},
        )
        self.assertEqual(order.first_name, "Jane")
        self.assertEqual(order.city, "London")
        self.assertEqual(order.country, "GB")


class DuplicateOrderTests(TestCase):
    def setUp(self):
        self.variant = make_variant("M", stock_qty=5)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("orders.views.stripe.Webhook.construct_event")
    def test_webhook_twice_with_same_session_creates_one_order(self, construct_event):
        construct_event.return_value = {
            "id": "evt_dup",
            "type": "checkout.session.completed",
            "data": {"object": checkout_session("cs_dup_1", f"{self.variant.id}:2")},
        }
        url = reverse("orders:stripe_webhook")
        self.client.post(url, data=b"{}", content_type="application/json")
        self.client.post(url, data=b"{}", content_type="application/json")
        self.assertEqual(Order.objects.filter(stripe_session_id="cs_dup_1").count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 3)

    def test_race_returns_existing_order_without_reducing_stock_again(self):
        first = create_paid_order(
            [(str(self.variant.id), 1)], email="", total=Decimal("10.00"), stripe_session_id="cs_race_1",
        )
        # Simulate the success page and webhook both passing the "already exists?" check.
        with patch("orders.services._find_order_for_payment", return_value=None):
            second = create_paid_order(
                [(str(self.variant.id), 1)], email="", total=Decimal("10.00"), stripe_session_id="cs_race_1",
            )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.filter(stripe_session_id="cs_race_1").count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_qty, 4)

    def test_blank_session_ids_are_allowed_more_than_once(self):
        create_paid_order([], email="", total=Decimal("0.00"), stripe_payment_intent_id="pi_a")
        create_paid_order([], email="", total=Decimal("0.00"), stripe_payment_intent_id="pi_b")
        self.assertEqual(Order.objects.filter(stripe_session_id="").count(), 2)


class ParseMetadataItemsTests(TestCase):
    def test_parses_pairs_and_skips_bad_entries(self):
        self.assertEqual(
            parse_metadata_items(" 12:2, 7:x ,nocolon, 9 : 1 "),
            [("12", 2), ("9", 1)],
        )

    def test_empty_string_gives_empty_list(self):
        self.assertEqual(parse_metadata_items(""), [])


@override_settings(STRIPE_SECRET_KEY="sk_test_dummy", STRIPE_PUBLISHABLE_KEY="pk_test_dummy")
class CheckoutPaymentIntentRemovalTests(TestCase):
    def setUp(self):
        self.variant = make_variant("L", stock_qty=5)
        self.client.post(reverse("cart:cart_add", args=[self.variant.id]), {"quantity": 1})

    @patch("orders.views.stripe.PaymentIntent.create")
    def test_checkout_page_does_not_create_payment_intent(self, create):
        resp = self.client.get(reverse("orders:checkout"))
        self.assertEqual(resp.status_code, 200)
        create.assert_not_called()

    def test_checkout_page_posts_to_stripe_checkout(self):
        resp = self.client.get(reverse("orders:checkout"))
        self.assertContains(resp, f'action="{reverse("orders:create_checkout_session")}"')
        self.assertContains(resp, 'method="post"')
        self.assertNotContains(resp, "js.stripe.com")

    @override_settings(STRIPE_SECRET_KEY="")
    def test_checkout_page_without_stripe_keys_shows_message(self):
        resp = self.client.get(reverse("orders:checkout"))
        self.assertContains(resp, "Payment is temporarily unavailable")
        self.assertNotContains(resp, reverse("orders:create_checkout_session"))

    def test_update_payment_intent_url_is_gone(self):
        resp = self.client.post("/orders/update-payment-intent/", data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, 404)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("orders.views.stripe.Webhook.construct_event")
    def test_payment_intent_succeeded_webhook_creates_no_order(self, construct_event):
        intent = FakeStripeObject(
            id="pi_only", amount=1000, metadata={"items": f"{self.variant.id}:1", "email": "a@example.com"}
        )
        construct_event.return_value = {
            "id": "evt_pi",
            "type": "payment_intent.succeeded",
            "data": {"object": intent},
        }
        resp = self.client.post(reverse("orders:stripe_webhook"), data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)

    @patch("orders.views.stripe.PaymentIntent.retrieve")
    def test_success_page_ignores_payment_intent_param(self, retrieve):
        resp = self.client.get(reverse("orders:checkout_success") + "?payment_intent=pi_123")
        self.assertRedirects(resp, reverse("products:home"), fetch_redirect_response=False)
        retrieve.assert_not_called()
        self.assertEqual(Order.objects.count(), 0)


class WebhookUserLinkTests(TestCase):
    def setUp(self):
        self.variant = make_variant("L", stock_qty=5)
        self.user = User.objects.create_user("shopper", email="shopper@example.com", password="pass12345")

    def start_checkout(self, create) -> dict:
        create.return_value = FakeStripeObject(url="https://checkout.stripe.test/pay")
        self.client.post(reverse("cart:cart_add", args=[self.variant.id]), {"quantity": 1})
        self.client.post(reverse("orders:create_checkout_session"))
        create.assert_called_once()
        return create.call_args.kwargs

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("orders.views.stripe.checkout.Session.create")
    def test_checkout_session_sends_logged_in_user(self, create):
        self.client.force_login(self.user)
        kwargs = self.start_checkout(create)
        self.assertEqual(kwargs["client_reference_id"], str(self.user.id))
        self.assertEqual(kwargs["customer_email"], "shopper@example.com")

    @override_settings(STRIPE_SECRET_KEY="sk_test_dummy")
    @patch("orders.views.stripe.checkout.Session.create")
    def test_checkout_session_for_guest_sends_no_user(self, create):
        kwargs = self.start_checkout(create)
        self.assertNotIn("client_reference_id", kwargs)
        self.assertNotIn("customer_email", kwargs)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("orders.views.stripe.Webhook.construct_event")
    def test_webhook_links_order_to_user(self, construct_event):
        session = checkout_session("cs_user_1", f"{self.variant.id}:1")
        session["client_reference_id"] = str(self.user.id)
        construct_event.return_value = {
            "id": "evt_user",
            "type": "checkout.session.completed",
            "data": {"object": session},
        }
        resp = self.client.post(reverse("orders:stripe_webhook"), data=b"{}", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Order.objects.get(stripe_session_id="cs_user_1").user, self.user)

    def test_guest_webhook_order_has_no_user(self):
        _create_order_from_checkout_session(checkout_session("cs_user_2", f"{self.variant.id}:1"))
        self.assertIsNone(Order.objects.get(stripe_session_id="cs_user_2").user)

    def test_unknown_or_bad_reference_is_treated_as_guest(self):
        for session_id, reference in [("cs_user_3", "999999"), ("cs_user_4", "not-a-number")]:
            session = checkout_session(session_id, f"{self.variant.id}:1")
            session["client_reference_id"] = reference
            session["payment_intent"] = f"pi_{session_id}"
            _create_order_from_checkout_session(session)
            self.assertIsNone(Order.objects.get(stripe_session_id=session_id).user)

    def test_webhook_adds_user_to_order_from_success_page(self):
        Order.objects.create(
            stripe_session_id="cs_user_5", email="shopper@example.com", first_name="", last_name="",
            address_line1="", city="", state="", postal_code="", total=Decimal("10.00"), status="paid",
        )
        session = checkout_session("cs_user_5", f"{self.variant.id}:1")
        session["client_reference_id"] = str(self.user.id)
        _create_order_from_checkout_session(session)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.get(stripe_session_id="cs_user_5").user, self.user)
