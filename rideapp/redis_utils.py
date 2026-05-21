"""
Redis utilities with retry logic, graceful fallbacks, and connection management.
"""
import os
import logging
import time
from functools import wraps
from typing import Any, Optional
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger('rides4u.redis')

# Configuration
REDIS_MAX_RETRIES = int(os.environ.get('REDIS_MAX_RETRIES', 3))
REDIS_RETRY_DELAY = float(os.environ.get('REDIS_RETRY_DELAY', 0.5))
REDIS_TIMEOUT = int(os.environ.get('REDIS_TIMEOUT', 5))


class RedisUnavailable(Exception):
    """Raised when Redis is unavailable after retries."""
    pass


def redis_retry(max_retries=REDIS_MAX_RETRIES, delay=REDIS_RETRY_DELAY, fallback_return=None):
    """Decorator for Redis operations with retry and fallback."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()
                    # Don't warn for key not found (normal cache miss) or None values
                    is_cache_miss = "not found" in error_str or "key" in error_str and "none" in error_str

                    if attempt < max_retries:
                        if not is_cache_miss:
                            logger.warning(f"Redis operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}")
                        time.sleep(delay * (2 ** attempt))
                    else:
                        if is_cache_miss:
                            # Cache miss is normal, return fallback without error log
                            if fallback_return is not None:
                                return fallback_return
                            return None
                        logger.error(f"Redis operation failed after {max_retries + 1} attempts: {e}")
                        if fallback_return is not None:
                            logger.info(f"Returning fallback value: {fallback_return}")
                            return fallback_return
                        raise RedisUnavailable(f"Redis unavailable: {e}")

            raise last_exception
        return wrapper
    return decorator


class GracefulCache:
    """Cache wrapper that gracefully handles Redis failures."""
    
    @staticmethod
    @redis_retry(fallback_return=None)
    def get(key: str) -> Any:
        """Get value from cache with fallback to None on failure."""
        return cache.get(key)
    
    @staticmethod
    @redis_retry(fallback_return=False)
    def set(key: str, value: Any, timeout: int = None) -> bool:
        """Set value in cache with fallback to False on failure."""
        return cache.set(key, value, timeout=timeout)
    
    @staticmethod
    @redis_retry(fallback_return=0)
    def delete(key: str) -> int:
        """Delete key from cache with fallback to 0 on failure."""
        return cache.delete(key)
    
    @staticmethod
    @redis_retry(fallback_return=False)
    def add(key: str, value: Any, timeout: int = None) -> bool:
        """Add key only if it doesn't exist."""
        return cache.add(key, value, timeout=timeout)
    
    @staticmethod
    @redis_retry(fallback_return=0)
    def incr(key: str, delta: int = 1) -> int:
        """Increment key value. Returns new value or 0 if key doesn't exist."""
        try:
            return cache.incr(key, delta=delta)
        except ValueError:
            # Key doesn't exist, initialize it
            cache.set(key, delta)
            return delta
    
    @staticmethod
    @redis_retry(fallback_return=0)
    def decr(key: str, delta: int = 1) -> int:
        """Decrement key value. Returns new value or 0 if key doesn't exist."""
        try:
            return cache.decr(key, delta=delta)
        except ValueError:
            # Key doesn't exist, initialize to 0 (or negative delta)
            new_val = max(0, -delta)
            cache.set(key, new_val)
            return new_val
    
    @staticmethod
    @redis_retry(fallback_return=[])
    def get_many(keys: list) -> dict:
        """Get multiple keys at once."""
        return cache.get_many(keys)
    
    @staticmethod
    @redis_retry(fallback_return=False)
    def set_many(data: dict, timeout: int = None) -> bool:
        """Set multiple keys at once."""
        return cache.set_many(data, timeout=timeout)


def check_redis_health():
    """Check if Redis connection is healthy."""
    try:
        cache.set('health_check', 'ok', timeout=5)
        value = cache.get('health_check')
        return value == 'ok', "healthy"
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return False, str(e)


def safe_cache_get(key: str, default=None, timeout: int = 300):
    """Safely get from cache with default fallback."""
    try:
        value = GracefulCache.get(key)
        return value if value is not None else default
    except RedisUnavailable:
        return default


def safe_cache_set(key: str, value: Any, timeout: int = 300):
    """Safely set cache with error handling."""
    try:
        return GracefulCache.set(key, value, timeout=timeout)
    except RedisUnavailable:
        return False


def safe_cache_add(key: str, value: Any, timeout: int = 300) -> bool:
    """Safely add to cache only if key doesn't exist. Returns True if added."""
    try:
        return GracefulCache.add(key, value, timeout=timeout)
    except RedisUnavailable:
        return False


# Driver location TTL management
DRIVER_LOCATION_TTL_SECONDS = int(os.environ.get('DRIVER_LOCATION_TTL_SECONDS', 300))  # 5 minutes
DRIVER_ONLINE_TTL_SECONDS = int(os.environ.get('DRIVER_ONLINE_TTL_SECONDS', 600))  # 10 minutes


def is_driver_present(driver_id: int) -> bool:
    """Check if driver has a valid location entry in cache (not expired)."""
    key = f"driver_location:{driver_id}"
    return safe_cache_get(key) is not None


def set_driver_location_ttl(driver_id: int, lat: float, lng: float):
    """Set driver location with TTL expiration."""
    key = f"driver_location:{driver_id}"
    data = {
        'lat': lat,
        'lng': lng,
        'timestamp': time.time(),
        'driver_id': driver_id
    }
    return safe_cache_set(key, data, timeout=DRIVER_LOCATION_TTL_SECONDS)


def get_driver_location_ttl(driver_id: int) -> Optional[dict]:
    """Get driver location if not expired."""
    key = f"driver_location:{driver_id}"
    return safe_cache_get(key)


def cleanup_expired_driver_locations(driver_ids: list) -> int:
    """Clean up expired driver location entries."""
    cleaned = 0
    for driver_id in driver_ids:
        key = f"driver_location:{driver_id}"
        try:
            if GracefulCache.get(key) is None:
                cleaned += 1
        except RedisUnavailable:
            break
    return cleaned


def set_driver_online_status(driver_id: int, is_online: bool):
    """Track driver online status with TTL and update database."""
    # Update the database is_online field
    try:
        from drivers.models import Driver
        Driver.objects.filter(id=driver_id).update(is_online=is_online)
    except Exception as db_err:
        logger.error(f"Failed to update driver {driver_id} is_online in database: {db_err}")
    
    # Update Redis cache
    key = f"driver_online:{driver_id}"
    if is_online:
        return safe_cache_set(key, {
            'driver_id': driver_id,
            'online_since': time.time()
        }, timeout=DRIVER_ONLINE_TTL_SECONDS)
    else:
        try:
            GracefulCache.delete(key)
            return True
        except RedisUnavailable:
            return False


def get_online_drivers_count() -> int:
    """Get count of drivers marked as online in Redis using non-blocking SCAN."""
    # This is approximate - for exact counts, query the database
    try:
        # Pattern match for driver_online keys
        # Note: This requires direct Redis access, not just Django cache
        import redis
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        r = redis.from_url(redis_url, socket_timeout=REDIS_TIMEOUT)
        count = 0
        for _ in r.scan_iter(match='driver_online:*', count=100):
            count += 1
        return count
    except Exception as e:
        logger.error(f"Failed to get online drivers count: {e}")
        return 0


# Redis GEO functions for driver location-based dispatch
DRIVERS_GEO_KEY = "drivers:locations"
DRIVERS_LOC_HASH_KEY = "drivers:locations:hash"  # Fallback for older Redis
_geo_supported = None


def _check_geo_support() -> bool:
    """Check if Redis server supports GEO commands (3.2+)."""
    global _geo_supported
    if _geo_supported is not None:
        return _geo_supported
    try:
        import redis
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        r = redis.from_url(redis_url, socket_timeout=REDIS_TIMEOUT)
        # Try a test GEOADD command
        r.execute_command('GEOADD', 'test:geo', '0', '0', 'test')
        r.execute_command('ZREM', 'test:geo', 'test')
        _geo_supported = True
        logger.info("Redis GEO commands are supported (Redis 3.2+)")
    except Exception as e:
        _geo_supported = False
        logger.warning(f"Redis GEO not supported (using hash fallback): {e}")
    return _geo_supported


def _haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Calculate haversine distance between two points in km."""
    import math
    R = 6371  # Earth radius in km
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlng/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def add_driver_to_geo(driver_id: int, lat: float, lng: float) -> bool:
    """Add driver to Redis GEO index for location-based search."""
    try:
        import redis
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        r = redis.from_url(redis_url, socket_timeout=REDIS_TIMEOUT)
        
        if _check_geo_support():
            # Use native GEOADD (Redis 3.2+)
            result = r.execute_command('GEOADD', DRIVERS_GEO_KEY, str(lng), str(lat), str(driver_id))
        else:
            # Fallback: Store in hash for Python-based distance calc
            r.hset(DRIVERS_LOC_HASH_KEY, str(driver_id), f"{lat},{lng}")
            result = 1
        
        logger.debug(f"Added driver {driver_id} to GEO at ({lat}, {lng})")
        return result is not None
    except Exception as e:
        logger.error(f"Failed to add driver {driver_id} to GEO: {e}")
        return False


def remove_driver_from_geo(driver_id: int) -> bool:
    """Remove driver from Redis GEO index when going offline."""
    try:
        import redis
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        r = redis.from_url(redis_url, socket_timeout=REDIS_TIMEOUT)
        
        if _check_geo_support():
            result = r.execute_command('ZREM', DRIVERS_GEO_KEY, str(driver_id))
        else:
            # Fallback: Remove from hash
            result = r.hdel(DRIVERS_LOC_HASH_KEY, str(driver_id))
        
        logger.debug(f"Removed driver {driver_id} from GEO")
        return result > 0
    except Exception as e:
        logger.error(f"Failed to remove driver {driver_id} from GEO: {e}")
        return False


def update_driver_geo_location(driver_id: int, lat: float, lng: float) -> bool:
    """Update driver location in Redis GEO index."""
    # Same as add - GEOADD updates existing members
    return add_driver_to_geo(driver_id, lat, lng)


def find_nearby_drivers_geo(lat: float, lng: float, radius_km: float = 5.0, limit: int = 10) -> list:
    """Find nearby drivers using Redis GEORADIUS or hash fallback."""
    try:
        import redis
        redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379')
        r = redis.from_url(redis_url, socket_timeout=REDIS_TIMEOUT)
        
        if _check_geo_support():
            # Use native GEORADIUS (Redis 3.2+)
            results = r.execute_command(
                'GEORADIUS', DRIVERS_GEO_KEY, 
                str(lng), str(lat), 
                str(radius_km), 'km',
                'WITHDIST', 'COUNT', str(limit), 'ASC'
            )
            drivers = []
            for item in results:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    driver_id = int(item[0])
                    distance = float(item[1])
                    
                    # --- CRITICAL: Check TTL Presence ---
                    if is_driver_present(driver_id):
                        drivers.append({'driver_id': driver_id, 'distance_km': round(distance, 2)})
                    else:
                        # Auto-cleanup stale driver from GEO index
                        r.execute_command('ZREM', DRIVERS_GEO_KEY, str(driver_id))
            return drivers
        else:
            # Fallback: Get all drivers from hash, calculate distance in Python
            all_locs = r.hgetall(DRIVERS_LOC_HASH_KEY)
            drivers = []
            for driver_id_str, loc_str in all_locs.items():
                try:
                    driver_id = int(driver_id_str)
                    d_lat, d_lng = map(float, loc_str.decode().split(','))
                    dist = _haversine_distance(lat, lng, d_lat, d_lng)
                    if dist <= radius_km:
                        drivers.append({'driver_id': driver_id, 'distance_km': round(dist, 2)})
                except Exception:
                    continue
            drivers.sort(key=lambda x: x['distance_km'])
            return drivers[:limit]
            
    except Exception as e:
        logger.error(f"Failed to search nearby drivers: {e}")
        return []


# Cache key patterns
class CacheKeys:
    """Standardized cache key patterns."""
    
    @staticmethod
    def route(origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float) -> str:
        return f"route:{origin_lat:.5f}:{origin_lng:.5f}:{dest_lat:.5f}:{dest_lng:.5f}"
    
    @staticmethod
    def geocode(address: str) -> str:
        import hashlib
        digest = hashlib.sha256(address.encode()).hexdigest()[:16]
        return f"geocode:{digest}"
    
    @staticmethod
    def autocomplete(query: str) -> str:
        import hashlib
        digest = hashlib.sha256(query.encode()).hexdigest()[:16]
        return f"autocomplete:{digest}"
    
    @staticmethod
    def driver_location(driver_id: int) -> str:
        return f"driver_location:{driver_id}"
    
    @staticmethod
    def ride_lock(ride_id: int) -> str:
        return f"ride_lock:{ride_id}"
    
    @staticmethod
    def rate_limit_ip(ip: str, endpoint: str) -> str:
        return f"rate_limit:{ip}:{endpoint}"
    
    @staticmethod
    def webhook_nonce(nonce: str) -> str:
        return f"webhook_nonce:{nonce}"
