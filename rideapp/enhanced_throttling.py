"""
Enhanced rate limiting with stricter protection for critical endpoints.
"""
import os
import time
import logging
from rest_framework.throttling import SimpleRateThrottle, AnonRateThrottle
from rest_framework.permissions import AllowAny
from django.core.cache import cache
from django.conf import settings


def _is_public_view(view) -> bool:
    """Check if view is publicly accessible."""
    permission_classes = getattr(view, "permission_classes", []) or []
    for perm in permission_classes:
        if perm is AllowAny:
            return True
        if isinstance(perm, type) and issubclass(perm, AllowAny):
            return True
    return False


class _BasePublicThrottle(SimpleRateThrottle):
    """Base throttle for public endpoints."""
    
    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            return None
        if not _is_public_view(view):
            return None
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class PublicAPIBurstRateThrottle(_BasePublicThrottle):
    """Burst rate limiting for public APIs."""
    scope = "public_api_burst"


class PublicAPIRateThrottle(_BasePublicThrottle):
    """Sustained rate limiting for public APIs."""
    scope = "public_api"


class RideRequestThrottle(SimpleRateThrottle):
    """
    Stricter rate limiting for ride requests.
    Prevents abuse of ride creation endpoint.
    """
    scope = "ride_request"
    rate = "5/minute"  # Rule: max 5 ride requests per user per minute.
    
    def get_cache_key(self, request, view):
        # Apply only to ride creation (POST /api/rides/ or similar)
        if request.method != 'POST':
            return None
        if not request.path.rstrip('/').endswith('/rides') and not request.path.rstrip('/').endswith('/ride'):
            return None
            
        # Throttle by user if authenticated, otherwise by IP
        if request.user and request.user.is_authenticated:
            ident = f"user_{request.user.pk}"
        else:
            ident = self.get_ident(request)
        
        return f"throttle_{self.scope}_{ident}"


class DriverAcceptThrottle(SimpleRateThrottle):
    """
    Rate limiting for driver ride acceptance.
    Prevents drivers from spamming accept requests.
    """
    scope = "driver_accept"
    rate = "100/minute"  # Increased limit ride acceptance attempts
    
    def get_cache_key(self, request, view):
        if request.method != 'POST' or '/accept' not in request.path:
            return None
            
        if request.user and request.user.is_authenticated:
            return f"throttle_{self.scope}_user_{request.user.pk}"
        return None  # Don't throttle unauthenticated


class DriverLocationUpdateThrottle(SimpleRateThrottle):
    """
    Rate limiting for driver location updates.
    Prevents excessive location updates from drivers.
    """
    scope = "driver_location"
    rate = "60/minute"  # Max 1 update per second on average
    
    def get_cache_key(self, request, view):
        if request.method != 'POST' or '/location' not in request.path:
            return None
            
        if request.user and request.user.is_authenticated:
            return f"throttle_{self.scope}_user_{request.user.pk}"
        return None


class IPThrottle(SimpleRateThrottle):
    """
    IP-based throttling for all endpoints.
    Provides general protection against IP-based abuse.
    """
    scope = "ip"
    rate = "100/minute"  # General limit per IP
    
    def get_cache_key(self, request, view):
        return f"throttle_ip_{self.get_ident(request)}"


class WebhookThrottle(SimpleRateThrottle):
    """
    Special throttling for webhook endpoints.
    More lenient but still protected.
    """
    scope = "webhook"
    rate = "60/minute"
    
    def get_cache_key(self, request, view):
        # Throttle by IP for webhooks
        return f"throttle_webhook_{self.get_ident(request)}"


class BruteForceIPThrottle(SimpleRateThrottle):
    """
    Aggressive throttling for authentication endpoints.
    Protects against brute force attacks.
    """
    scope = "brute_force"
    rate = "50/minute"  # Increased limit for auth endpoints
    
    def get_cache_key(self, request, view):
        # Apply only to auth endpoints
        auth_paths = ['/api/auth/', '/api/login', '/api/signup', '/api/otp/']
        if any(request.path.startswith(path) for path in auth_paths):
            return f"throttle_bruteforce_{self.get_ident(request)}"
        return None


class PaymentRequestThrottle(SimpleRateThrottle):
    """
    Rate limiting for payment-related requests.
    Protects against payment fraud attempts.
    """
    scope = "payment"
    rate = "10/minute"
    
    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return f"throttle_payment_user_{request.user.pk}"
        return f"throttle_payment_ip_{self.get_ident(request)}"


# Advanced throttling with sliding window
class SlidingWindowThrottle(SimpleRateThrottle):
    """
    Sliding window rate limiting implementation.
    More accurate than fixed window but slightly more expensive.
    """
    
    def allow_request(self, request, view):
        """Implement sliding window rate limiting."""
        if self.rate is None:
            return True
        
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        
        self.history = self.cache.get(self.key, [])
        self.now = self.timer()
        
        # Remove entries outside the window
        window_start = self.now - self.duration
        while self.history and self.history[-1] <= window_start:
            self.history.pop()
        
        if len(self.history) >= self.num_requests:
            return self.throttle_failure()
        
        return self.throttle_success()


class WebSocketConnectionThrottle:
    """
    Throttling for WebSocket connections.
    Applied at connection time to prevent connection floods.
    """
    
    @staticmethod
    def is_allowed(scope):
        """Check if WebSocket connection should be allowed."""
        from rideapp.redis_utils import safe_cache_get, safe_cache_set
        
        # Get client IP
        client_ip = scope.get('client', ('', 0))[0]
        if not client_ip:
            return True
        
        key = f"ws_throttle_{client_ip}"
        
        # Check current count
        current = safe_cache_get(key, default=0)
        max_connections = int(os.environ.get('WS_MAX_CONNECTIONS_PER_IP', 5))
        
        if current >= max_connections:
            return False
        
        # Increment counter (expires in 1 minute)
        safe_cache_set(key, current + 1, timeout=60)
        return True
    
    @staticmethod
    def release_connection(scope):
        """Release a WebSocket connection slot."""
        from rideapp.redis_utils import safe_cache_get, safe_cache_set
        
        client_ip = scope.get('client', ('', 0))[0]
        if not client_ip:
            return
        
        key = f"ws_throttle_{client_ip}"
        current = safe_cache_get(key, default=0)
        
        if current > 0:
            safe_cache_set(key, current - 1, timeout=60)


class WSRateLimiter:
    """
    Redis-based sliding window rate limiter for WebSockets.
    Tracks IP + User ID to prevent spam and reconnect storms.
    """
    
    @staticmethod
    def is_allowed(user_id: str, client_ip: str, action: str, limit: int, window: int) -> bool:
        """
        Check if action is allowed within sliding window.
        """
        key = f"ws_limit:{action}:{user_id}:{client_ip}"
        now = time.time()
        
        try:
            import redis
            from django.conf import settings
            r = redis.from_url(settings.REDIS_URL)
            
            # Atomic sliding window using Redis sorted sets
            pipe = r.pipeline()
            # Remove old entries (older than window)
            pipe.zremrangebyscore(key, 0, now - window)
            # Add new entry
            pipe.zadd(key, {str(now): now})
            # Count entries in current window
            pipe.zcard(key)
            # Set TTL on the set to ensure it's cleaned up if no activity
            pipe.expire(key, window + 1)
            
            results = pipe.execute()
            count = results[2]
            
            return count <= limit
        except Exception as e:
            # Fallback to allowing if Redis is down, but log error
            logging.getLogger("rides4u").error(
                f"WSRateLimiter failed: {e}",
                extra={'event_type': 'ws_rate_limit_error'}
            )
            return True


# Export all throttle classes for use in settings
__all__ = [
    'PublicAPIBurstRateThrottle',
    'PublicAPIRateThrottle',
    'RideRequestThrottle',
    'DriverAcceptThrottle',
    'DriverLocationUpdateThrottle',
    'IPThrottle',
    'WebhookThrottle',
    'BruteForceIPThrottle',
    'PaymentRequestThrottle',
    'SlidingWindowThrottle',
    'WebSocketConnectionThrottle',
]
