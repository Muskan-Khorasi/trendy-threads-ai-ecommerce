from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Order
from orders.services import reduce_stock
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

        with patch("orders.views.reduce_stock", side_effect=reduce_then_fail):
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
