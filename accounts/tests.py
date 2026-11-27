from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

User = get_user_model()


class AuthFlowTests(TestCase):
    def test_register_creates_user(self):
        resp = self.client.post(
            reverse("accounts:register"),
            {
                "first_name": "Test",
                "last_name": "User",
                "email": "test@example.com",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            },
        )
        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertTrue(User.objects.filter(email="test@example.com").exists())

    def test_login_open_redirect_prevented(self):
        user = User.objects.create_user(
            username="testuser", email="test@example.com", password="pass123"
        )
        resp = self.client.post(
            reverse("accounts:login"),
            {"username": "testuser", "password": "pass123"},
            QUERY_STRING="next=https://evil.com",
        )
        self.assertRedirects(resp, reverse("products:home"))


class LoginRedirectTests(TestCase):
    def setUp(self) -> None:
        User.objects.create_user(
            username="shopper", email="shopper@example.com", password="pass123"
        )

    def _login_with_next(self, next_url: str):
        return self.client.post(
            reverse("accounts:login"),
            {"username": "shopper", "password": "pass123"},
            QUERY_STRING=f"next={next_url}",
        )

    def test_backslash_next_redirects_home(self):
        # Browsers read /\evil.example.com as //evil.example.com.
        resp = self._login_with_next("/\evil.example.com")
        self.assertRedirects(resp, reverse("products:home"), fetch_redirect_response=False)

    def test_absolute_external_next_redirects_home(self):
        resp = self._login_with_next("https://evil.example.com")
        self.assertRedirects(resp, reverse("products:home"), fetch_redirect_response=False)

    def test_local_next_still_works(self):
        resp = self._login_with_next(reverse("accounts:profile"))
        self.assertRedirects(resp, reverse("accounts:profile"), fetch_redirect_response=False)
