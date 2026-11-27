import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent

# Settings are read once at import time, so each case loads them in a fresh
# process with its own environment.
LOAD_SETTINGS = """
import json
import django
django.setup()
from django.conf import settings
print(json.dumps({
    "DEBUG": settings.DEBUG,
    "SESSION_COOKIE_SECURE": settings.SESSION_COOKIE_SECURE,
    "CSRF_COOKIE_SECURE": settings.CSRF_COOKIE_SECURE,
    "SECURE_SSL_REDIRECT": settings.SECURE_SSL_REDIRECT,
    "SECURE_PROXY_SSL_HEADER": settings.SECURE_PROXY_SSL_HEADER,
}))
"""

REAL_LOOKING_KEY = 'test-only-key-0123456789abcdefghijklmnopqrstuvwxyz'


def load_settings(debug: str, secret_key: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    env['DEBUG'] = debug
    # Explicit env vars win over .env, so the developer's .env can't leak in.
    env['SECRET_KEY'] = secret_key
    return subprocess.run(
        [sys.executable, '-c', LOAD_SETTINGS],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class SecretKeyCheckTests(SimpleTestCase):
    def assert_fails_with_clear_error(self, result: subprocess.CompletedProcess) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('SECRET_KEY is missing or still a placeholder', result.stderr)

    def test_default_placeholder_key_fails_when_debug_off(self):
        result = load_settings('False', 'django-insecure-change-me-in-production')
        self.assert_fails_with_clear_error(result)

    def test_env_example_placeholder_key_fails_when_debug_off(self):
        result = load_settings('False', 'your-django-secret-key-here')
        self.assert_fails_with_clear_error(result)

    def test_empty_key_fails_when_debug_off(self):
        result = load_settings('False', '')
        self.assert_fails_with_clear_error(result)

    def test_placeholder_key_allowed_when_debug_on(self):
        result = load_settings('True', 'your-django-secret-key-here')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['DEBUG'])


class ProductionSecurityTests(SimpleTestCase):
    def test_secure_flags_on_when_debug_off(self):
        result = load_settings('False', REAL_LOOKING_KEY)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertFalse(values['DEBUG'])
        self.assertTrue(values['SESSION_COOKIE_SECURE'])
        self.assertTrue(values['CSRF_COOKIE_SECURE'])
        self.assertTrue(values['SECURE_SSL_REDIRECT'])
        self.assertEqual(values['SECURE_PROXY_SSL_HEADER'], ['HTTP_X_FORWARDED_PROTO', 'https'])

    def test_secure_flags_off_when_debug_on(self):
        result = load_settings('True', REAL_LOOKING_KEY)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertFalse(values['SESSION_COOKIE_SECURE'])
        self.assertFalse(values['CSRF_COOKIE_SECURE'])
        self.assertFalse(values['SECURE_SSL_REDIRECT'])
        self.assertIsNone(values['SECURE_PROXY_SSL_HEADER'])

    def test_debug_defaults_to_false(self):
        # A local .env usually sets DEBUG, so check the declared default directly.
        source = (BASE_DIR / 'config' / 'settings.py').read_text(encoding='utf-8')
        self.assertIn("DEBUG=(bool, False)", source)
