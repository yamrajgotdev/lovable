import logging
import time
from typing import Any, Optional
from django.core.cache import cache
from rideapp.redis_utils import GracefulCache

logger = logging.getLogger("rides4u.cache")


class RideCacheService:
    """Read-through versioned cache for ride read endpoints."""

    # Versioned keys prevent stale data. 1-hour TTL prevents memory bloat.
    TTL_SECONDS = 3600 

    @staticmethod
    def _version_key(ride_id: Any) -> str:
        return f"ride:{ride_id}:version"

    @staticmethod
    def _data_key(ride_id: Any, version: int) -> str:
        return f"ride:v{version}:{ride_id}:data"

    @classmethod
    def get_version(cls, ride_id: Any) -> int:
        """Get current version from Redis. Defaults to 1 if not exists."""
        key = cls._version_key(ride_id)
        version = GracefulCache.get(key)
        if version is None:
            # Initialize version if it doesn't exist
            version = 1
            GracefulCache.set(key, version, timeout=86400 * 7) # 1 week
        return int(version)

    @classmethod
    def increment_version(cls, ride_id: Any) -> int:
        """Increment version in Redis. Effectively invalidates all old cache entries."""
        key = cls._version_key(ride_id)
        try:
            new_version = cache.incr(key)
        except ValueError:
            # Key doesn't exist, set to 2 (initial was effectively 1)
            new_version = 2
            cache.set(key, new_version, timeout=86400 * 7)
        
        logger.info(f"[CACHE VERSION UP] ride={ride_id} version={new_version}", extra={'event_type': 'cache_version_increment', 'ride_id': ride_id})
        return new_version

    @classmethod
    def get(cls, ride_id: Any) -> Optional[dict]:
        """Fetch data using versioned key strategy."""
        version = cls.get_version(ride_id)
        key = cls._data_key(ride_id, version)
        
        data = GracefulCache.get(key)
        if data is not None:
            logger.info("[CACHE HIT] key=%s", key, extra={'event_type': 'cache_hit', 'ride_id': ride_id})
        else:
            logger.info("[CACHE MISS] key=%s", key, extra={'event_type': 'cache_miss', 'ride_id': ride_id})
        return data

    @classmethod
    def set(cls, ride_id: Any, payload: dict, timeout: int = None) -> bool:
        """Cache data with current version and 1-hour TTL."""
        version = cls.get_version(ride_id)
        key = cls._data_key(ride_id, version)
        return bool(GracefulCache.set(key, payload, timeout=timeout or cls.TTL_SECONDS))

    @classmethod
    def invalidate(cls, ride_id: Any, reason: str) -> None:
        """
        1. Increment version to invalidate cache across all workers.
        2. Explicitly delete current versioned key as an extra safeguard.
        """
        version = cls.get_version(ride_id)
        key = cls._data_key(ride_id, version)
        
        cls.increment_version(ride_id)
        GracefulCache.delete(key)
        
        logger.info("[CACHE INVALIDATE] ride=%s reason=%s key_deleted=%s", 
                    ride_id, reason, key, 
                    extra={'event_type': 'cache_invalidate', 'ride_id': ride_id, 'reason': reason})
