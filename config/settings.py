"""
Django settings for Trendy Threads ecommerce project.
"""
import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, 'django-insecure-change-me-in-production'),
    ALLOWED_HOSTS=(list, ['localhost', '127.0.0.1']),
    DOMAIN=(str, 'http://localhost:8000'),
    STRIPE_PUBLISHABLE_KEY=(str, ''),
    STRIPE_SECRET_KEY=(str, ''),
    STRIPE_WEBHOOK_SECRET=(str, ''),
    AWS_ACCESS_KEY_ID=(str, ''),
    AWS_SECRET_ACCESS_KEY=(str, ''),
    AWS_REGION=(str, 'us-east-1'),
    BEDROCK_KNOWLEDGE_BASE_ID=(str, ''),
    BEDROCK_MODEL_ARN=(str, ''),
    S3_POLICY_BUCKET=(str, ''),
    CHATBOT_RATE_LIMIT_MESSAGES=(int, 20),
    CHATBOT_RATE_LIMIT_IP_MESSAGES=(int, 60),
    CHATBOT_RATE_LIMIT_WINDOW_SECONDS=(int, 600),
)

BASE_DIR = Path(__file__).resolve().parent.parent
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')

# Known placeholder keys (the default above and the one in .env.example).
PLACEHOLDER_SECRET_KEYS = {
    'django-insecure-change-me-in-production',
    'your-django-secret-key-here',
}
if not DEBUG and (not SECRET_KEY.strip() or SECRET_KEY in PLACEHOLDER_SECRET_KEYS):
    raise ImproperlyConfigured(
        'SECRET_KEY is missing or still a placeholder while DEBUG is False. '
        'Set a real SECRET_KEY in the environment, or set DEBUG=True for local work.'
    )
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'products',
    'cart',
    'orders',
    'chatbot',
    'accounts',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'cart.context_processors.cart',
                'products.context_processors.nav_shop',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

X_FRAME_OPTIONS = 'DENY'
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    # Hosts like Render and Railway end TLS at a proxy; without this the
    # HTTPS redirect loops forever.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CART_SESSION_ID = 'cart'

# Site (for emails and display)
SITE_NAME = 'Trendy Threads'
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='Trendy Threads <noreply@trendythreads.com>')

# Email (console backend in dev; set EMAIL_BACKEND and SMTP in production)
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')

# Stripe (use placeholder keys in .env for local dev)
# DOMAIN: base URL for success/cancel redirects (e.g. http://localhost:8000)
DOMAIN = env('DOMAIN')
STRIPE_PUBLISHABLE_KEY = env('STRIPE_PUBLISHABLE_KEY')
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET')
# Countries Stripe Checkout will accept a shipping address from
STRIPE_SHIPPING_COUNTRIES = ['US', 'GB', 'CA']

# AWS Bedrock Knowledge Base (RAG)
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY')
AWS_REGION = env('AWS_REGION')
BEDROCK_KNOWLEDGE_BASE_ID = env('BEDROCK_KNOWLEDGE_BASE_ID')
# Optional: override the model used for RAG generation (default: Claude 3 Haiku)
BEDROCK_MODEL_ARN = env('BEDROCK_MODEL_ARN')
# Optional: S3 bucket where you uploaded policy .txt files. Not used by the app;
# the Knowledge Base is configured in AWS to use that bucket. Kept for reference.
S3_POLICY_BUCKET = env('S3_POLICY_BUCKET')

# Chatbot rate limits (each message costs a Bedrock call). The IP limit is higher
# because several people can share one IP address.
CHATBOT_RATE_LIMIT_MESSAGES = env('CHATBOT_RATE_LIMIT_MESSAGES')
CHATBOT_RATE_LIMIT_IP_MESSAGES = env('CHATBOT_RATE_LIMIT_IP_MESSAGES')
CHATBOT_RATE_LIMIT_WINDOW_SECONDS = env('CHATBOT_RATE_LIMIT_WINDOW_SECONDS')

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
