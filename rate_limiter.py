"""
Rate Limiting for Driver Location Updates
Uses Redis for distributed rate limiting.
"""
import time
import logging
from typing import Optional, Tuple
from functools import wraps
from django.http import JsonResponse

from rideapp.redis_utils import GracefulCache

logger = logging.getLogger('drivers.rate_limiter')

# Rate limit configuration
DRIVER_LOCATION_RATE_LIMIT = 5  # Updates per second max
DRIVER_LOCATION_BURST = 10  # Burst allowance
DRIVER_LOCATION_WINDOW = 60  # Time window in seconds

# Redis key patterns
RATE_LIMIT_KEY = "rate_limit:driver_updates:{driver_id}"


class DriverLocationRateLimiter:
    """
    Sliding window rate limiter for driver location updates.
    Uses Redis for distributed rate limiting across multiple app servers.
    """

    @staticmethod
    def is_allowed(driver_id: int) -> Tuple[bool, Optional[int]]:
        """
        Check if driver location update is allowed.

        Args:
            driver_id: Driver ID

        Returns:
            Tuple of (allowed: bool, retry_after: Optional[int])
            retry_after is seconds until next allowed update if not allowed
        """
        try:
            key = RATE_LIMIT_KEY.format(driver_id=driver_id)
            now = int(time.time())
            window_start = now - DRIVER_LOCATION_WINDOW

            # Get current request timestamps from Redis list
            request_data = GracefulCache.get(key)

            if request_data is None:
                request_data = {'timestamps': [], 'count': 0}

            timestamps = request_data.get('timestamps', [])

            # Filter timestamps within window
            valid_timestamps = [ts for ts in timestamps if ts > window_start]

            # Check if under rate limit
            if len(valid_timestamps) < DRIVER_LOCATION_RATE_LIMIT:
                # Allow request
                valid_timestamps.append(now)
                GracefulCache.set(
                    key,
                    {'timestamps': valid_timestamps, 'count': len(valid_timestamps)},
                    timeout=DRIVER_LOCATION_WINDOW
                )
                return True, None

            # Rate limit exceeded
            oldest_valid = min(valid_timestamps)
            retry_after = int(oldest_valid + DRIVER_LOCATION_WINDOW - now)
            retry_after = max(1, retry_after)

            logger.warning(f"Rate limit exceeded for driver {driver_id}")
            return False, retry_after

        except Exception as e:
            logger.error(f"Rate limit check failed for driver {driver_id}: {e}")
            # Fail open - allow update if Redis unavailable
            return True, None

    @staticmethod
    def get_remaining_quota(driver_id: int) -> dict:
        """Get remaining rate limit quota for driver."""
        try:
            key = RATE_LIMIT_KEY.format(driver_id=driver_id)
            now = int(time.time())
            window_start = now - DRIVER_LOCATION_WINDOW

            request_data = GracefulCache.get(key)

            if request_data is None:
                return {
                    'limit': DRIVER_LOCATION_RATE_LIMIT,
                    'remaining': DRIVER_LOCATION_RATE_LIMIT,
                    'window_seconds': DRIVER_LOCATION_WINDOW,
                    'reset_timestamp': now + DRIVER_LOCATION_WINDOW
                }

            timestamps = request_data.get('timestamps', [])
            valid_count = len([ts for ts in timestamps if ts > window_start])

            return {
                'limit': DRIVER_LOCATION_RATE_LIMIT,
                'remaining': max(0, DRIVER_LOCATION_RATE_LIMIT - valid_count),
                'window_seconds': DRIVER_LOCATION_WINDOW,
                'reset_timestamp': min(timestamps) + DRIVER_LOCATION_WINDOW if timestamps else now + DRIVER_LOCATION_WINDOW
            }

        except Exception as e:
            logger.error(f"Error getting quota for driver {driver_id}: {e}")
            return {
                'limit': DRIVER_LOCATION_RATE_LIMIT,
                'remaining': DRIVER_LOCATION_RATE_LIMIT,
                'error': str(e)
            }

    @staticmethod
    def reset_quota(driver_id: int) -> bool:
        """Reset rate limit quota for driver (for testing)."""
        try:
            key = RATE_LIMIT_KEY.format(driver_id=driver_id)
            GracefulCache.delete(key)
            return True
        except Exception as e:
            logger.error(f"Error resetting quota for driver {driver_id}: {e}")
            return False


def rate_limit_driver_location(view_func):
    """
    Decorator to rate limit driver location updates.
    Returns 429 Too Many Requests if rate limit exceeded.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # Extract driver_id from request
        driver_id = None
        if hasattr(request, 'user') and hasattr(request.user, 'driver'):
            driver_id = request.user.driver.id
        elif 'driver_id' in kwargs:
            driver_id = kwargs['driver_id']

        if driver_id:
            allowed, retry_after = DriverLocationRateLimiter.is_allowed(driver_id)
            if not allowed:
                return JsonResponse({
                    'error': 'Rate limit exceeded',
                    'retry_after_seconds': retry_after,
                    'message': f'Too many location updates. Please wait {retry_after}s before updating again.'
                }, status=429)

        return view_func(request, *args, **kwargs)
    return wrapper


class WebSocketRateLimiter:
    """
    Rate limiting for WebSocket connections.
    Tracks connections per IP and per driver.
    """

    MAX_CONNECTIONS_PER_IP = 5
    MAX_CONNECTIONS_PER_DRIVER = 2

    @staticmethod
    def track_connection(identifier: str, connection_type: str = 'ip') -> bool:
        """
        Track WebSocket connection.

        Args:
            identifier: IP address or driver ID
            connection_type: 'ip' or 'driver'

        Returns:
            True if connection allowed, False if limit exceeded
        """
        try:
            key = f"ws:connections:{connection_type}:{identifier}"
            current = GracefulCache.get(key) or 0

            max_connections = (
                WebSocketRateLimiter.MAX_CONNECTIONS_PER_IP
                if connection_type == 'ip'
                else WebSocketRateLimiter.MAX_CONNECTIONS_PER_DRIVER
            )

            if current >= max_connections:
                logger.warning(f"WebSocket connection limit exceeded for {connection_type} {identifier}")
                return False

            GracefulCache.set(key, current + 1, timeout=3600)  # 1 hour TTL
            return True

        except Exception as e:
            logger.error(f"Error tracking WebSocket connection: {e}")
            return True  # Fail open

    @staticmethod
    def release_connection(identifier: str, connection_type: str = 'ip'):
        """Release WebSocket connection tracking."""
        try:
            key = f"ws:connections:{connection_type}:{identifier}"
            current = GracefulCache.get(key) or 0
            if current > 0:
                GracefulCache.set(key, current - 1, timeout=3600)
        except Exception as e:
            logger.error(f"Error releasing WebSocket connection: {e}")
