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
