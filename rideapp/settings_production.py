"""
Production settings for rides4u - Ride Hailing Platform

Usage:
    DJANGO_SETTINGS_MODULE=rideapp.settings_production gunicorn rideapp.wsgi
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ──────────────────────────────────────────────────
# SECURITY CRITICAL VALUES
# ──────────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable must be set in production")

DEBUG = False

AUTH_USER_MODEL = 'authsystem.User'

# ──────────────────────────────────────────────────
# ALLOWED HOSTS — real VPS IPs / domains only
# ──────────────────────────────────────────────────
ALLOWED_HOSTS = [
    'rides4u.in',
    'www.rides4u.in',
    '16.171.165.184',   # VPS public IP
    'localhost',        # needed for health checks from the same host
    '127.0.0.1',
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'core',
    'authsystem',
    'drivers',
    'rides',
    'utils',
    'payments',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'rideapp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'rideapp.wsgi.application'

# ──────────────────────────────────────────────────
# DATABASE — PostgreSQL (required in production)
# ──────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'rides4u'),
        'USER': os.environ.get('DB_USER', 'rides4u_user'),
        'PASSWORD': os.environ.get('DB_PASSWORD'),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'OPTIONS': {
            'connect_timeout': 10,
        },
        'CONN_MAX_AGE': 60,
    }
}

if not DATABASES['default']['PASSWORD']:
    raise ValueError("DB_PASSWORD environment variable must be set in production")

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
    BASE_DIR / 'frontend' / 'dist',
]

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ──────────────────────────────────────────────────
# CORS — strict origin allowlist in production
# Remove CORS_ALLOW_ALL_ORIGINS entirely (default is False)
# ──────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    'https://rides4u.in',
    'https://www.rides4u.in',
]
CSRF_TRUSTED_ORIGINS = [
    'https://rides4u.in',
    'https://www.rides4u.in',
]

# ──────────────────────────────────────────────────
# REST FRAMEWORK — secure defaults, token auth
# ──────────────────────────────────────────────────
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'utils.exception_handler.standardized_exception_handler',
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'authsystem.authentication.TokenAuthentication',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

# ──────────────────────────────────────────────────
# CACHE — Redis required in production
# ──────────────────────────────────────────────────
_cache_url = os.environ.get('CACHE_URL', 'redis://127.0.0.1:6379/1')
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': _cache_url,
    }
}

# ──────────────────────────────────────────────────
# LOGGING — file + console, no PII
# ──────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'rides4u.log',
            'formatter': 'verbose',
        },
        'console': {
            'level': 'WARNING',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['file', 'console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'rides4u': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ──────────────────────────────────────────────────
# SECURITY HEADERS
# ──────────────────────────────────────────────────
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() == 'true'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ──────────────────────────────────────────────────
# APP SETTINGS (loaded from env only)
# ──────────────────────────────────────────────────
OLA_CLIENT_ID = os.environ.get('OLA_CLIENT_ID')
OLA_CLIENT_SECRET = os.environ.get('OLA_CLIENT_SECRET')
OLA_API_KEY = os.environ.get('OLA_API_KEY', OLA_CLIENT_ID)
OLA_API_BASE_URL = os.environ.get('OLA_API_BASE_URL', 'https://api.olamaps.io')

# Firebase phone auth configuration (OTP via Firebase only)
FIREBASE_API_KEY = os.environ.get('FIREBASE_API_KEY')
FIREBASE_PROJECT_ID = os.environ.get('FIREBASE_PROJECT_ID')
FIREBASE_AUTH_DOMAIN = os.environ.get(
    'FIREBASE_AUTH_DOMAIN',
    f"{FIREBASE_PROJECT_ID}.firebaseapp.com" if FIREBASE_PROJECT_ID else None,
)
FIREBASE_MESSAGING_SENDER_ID = os.environ.get('FIREBASE_MESSAGING_SENDER_ID')
FIREBASE_APP_ID = os.environ.get('FIREBASE_APP_ID')
FIREBASE_SERVICE_ACCOUNT_JSON_PATH = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON_PATH', '')

DEV_BYPASS_OTP = False  # Always disabled in production
DEV_OTP = ''

OTP_EXPIRY_MINUTES = 10

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')
RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET')
if not RAZORPAY_WEBHOOK_SECRET:
    raise ValueError("RAZORPAY_WEBHOOK_SECRET environment variable must be set in production")
if RAZORPAY_WEBHOOK_SECRET.lower().startswith(('http://', 'https://')):
    raise ValueError("RAZORPAY_WEBHOOK_SECRET must be the webhook signing secret, not a URL")

PLATFORM_COMMISSION_PERCENT = float(os.environ.get('PLATFORM_COMMISSION_PERCENT', 20))

# Truecaller disabled by default — enable only after backend JWT verification is added
ENABLE_TRUECALLER_LOGIN = os.environ.get('ENABLE_TRUECALLER_LOGIN', 'False').lower() == 'true'
TRUECALLER_APP_KEY = os.environ.get('TRUECALLER_APP_KEY')

FARE_CONFIG = {
    'mini':  {'base_fare': 20, 'per_km': 10,  'per_minute': 1.0},
    'sedan': {'base_fare': 30, 'per_km': 14,  'per_minute': 2.0},
    'suv':   {'base_fare': 40, 'per_km': 18,  'per_minute': 2.5},
    'auto':  {'base_fare': 15, 'per_km':  8,  'per_minute': 1.0},
    'bike':  {'base_fare': 10, 'per_km':  6,  'per_minute': 0.5},
}

DRIVER_SEARCH_RADIUS_KM = 10
ROUTE_CORRIDOR_WIDTH_KM = 2.0
ROUTE_MATCHING_MAX_RESULTS = 10
DRIVER_LOCATION_MAX_AGE_MINUTES = 5
DRIVER_LOCATION_UPDATE_INTERVAL_SECONDS = 5
DRIVER_MAX_LOCATION_FAILURES = 3
ROUTE_DEVIATION_THRESHOLD_METERS = 500
ROUTE_DEVIATION_ALERT_COOLDOWN_SECONDS = 60
