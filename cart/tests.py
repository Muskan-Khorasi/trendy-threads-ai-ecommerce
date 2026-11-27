from django.test import TestCase, Client
from django.urls import reverse
from products.models import Product, ProductVariant, Brand, Category


class CartTests(TestCase):
    def setUp(self):
        brand = Brand.objects.create(name="Test", country="UK")
        cat = Category.objects.create(name="Test", slug="test")
        self.product = Product.objects.create(
            name="Test Product", slug="test-prod", brand=brand, category=cat, description="Test"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, size="M", color="Black", sku="TEST-M-BLK", price=29.99, stock_qty=5
        )

    def test_cart_add_validates_quantity(self):
        resp = self.client.post(
            reverse("cart:cart_add", args=[self.variant.id]),
            {"quantity": -5},
        )
        session_cart = self.client.session.get("cart", {})
        self.assertEqual(session_cart.get(str(self.variant.id), {}).get("quantity", 0), 1)

    def test_adding_twice_is_capped_at_stock(self):
        url = reverse("cart:cart_add", args=[self.variant.id])
        self.client.post(url, {"quantity": 3})
        self.client.post(url, {"quantity": 3})
        session_cart = self.client.session.get("cart", {})
        self.assertEqual(session_cart[str(self.variant.id)]["quantity"], 5)

    def test_adding_when_cart_holds_all_stock_adds_nothing(self):
        url = reverse("cart:cart_add", args=[self.variant.id])
        self.client.post(url, {"quantity": 5})
        self.client.post(url, {"quantity": 1})
        session_cart = self.client.session.get("cart", {})
        self.assertEqual(session_cart[str(self.variant.id)]["quantity"], 5)


class CartAddRedirectTests(TestCase):
    def setUp(self):
        brand = Brand.objects.create(name="Test", country="UK")
        cat = Category.objects.create(name="Test", slug="test")
        product = Product.objects.create(
            name="Test Product", slug="test-prod", brand=brand, category=cat, description="Test"
        )
        self.variant = ProductVariant.objects.create(
            product=product, size="M", color="Black", sku="REDIR-M-BLK", price=29.99, stock_qty=5
        )
        self.out_of_stock = ProductVariant.objects.create(
            product=product, size="L", color="Black", sku="REDIR-L-BLK", price=29.99, stock_qty=0
        )
        self.add_url = reverse("cart:cart_add", args=[self.variant.id])
        self.fallback = reverse("products:product_list")

    def test_external_next_redirects_to_product_list(self):
        resp = self.client.post(self.add_url, {"next": "https://evil.example.com"})
        self.assertRedirects(resp, self.fallback, fetch_redirect_response=False)

    def test_backslash_next_redirects_to_product_list(self):
        resp = self.client.post(self.add_url, {"next": "/\evil.example.com"})
        self.assertRedirects(resp, self.fallback, fetch_redirect_response=False)

    def test_local_next_still_works(self):
        resp = self.client.post(self.add_url, {"next": "/products/test-prod/"})
        self.assertRedirects(resp, "/products/test-prod/", fetch_redirect_response=False)

    def test_external_referer_redirects_to_product_list(self):
        resp = self.client.post(self.add_url, HTTP_REFERER="https://evil.example.com/page")
        self.assertRedirects(resp, self.fallback, fetch_redirect_response=False)

    def test_out_of_stock_external_next_redirects_to_product_list(self):
        url = reverse("cart:cart_add", args=[self.out_of_stock.id])
        resp = self.client.post(url, {"next": "https://evil.example.com"})
        self.assertRedirects(resp, self.fallback, fetch_redirect_response=False)
