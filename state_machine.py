"""
Driver State Machine with Redis-backed Heartbeat System
Manages driver lifecycle: OFFLINE -> AVAILABLE -> DISPATCHED -> ENROUTE -> ARRIVED -> IN_RIDE
"""
import json
import logging
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from django.utils import timezone

from rideapp.redis_utils import GracefulCache

logger = logging.getLogger('drivers.state_machine')

# Redis key patterns
DRIVER_STATUS_KEY = "driver:{driver_id}:status"
DRIVER_HEARTBEAT_KEY = "driver:{driver_id}:heartbeat"
DRIVER_LOCATION_KEY = "drivers:locations"  # Redis GEO key
DRIVER_RIDE_KEY = "driver:{driver_id}:current_ride"

# Timeouts (seconds)
HEARTBEAT_INTERVAL = 10  # Expected heartbeat interval
HEARTBEAT_TIMEOUT = 30  # Mark offline after 30s without heartbeat
DISPATCH_TIMEOUT = 15  # Time to accept dispatch


class DriverState(Enum):
    """Driver lifecycle states."""
    OFFLINE = "offline"
    AVAILABLE = "available"
    DISPATCHED = "dispatched"  # Assigned to ride, waiting for acceptance
    ENROUTE = "enroute"  # Accepted ride, going to pickup
    ARRIVED = "arrived"  # At pickup location
    IN_RIDE = "in_ride"  # Passenger picked up, ride in progress


class DriverStateMachine:
    """
    Manages driver state transitions with Redis persistence.
    """

    VALID_TRANSITIONS = {
        DriverState.OFFLINE: [DriverState.AVAILABLE],
        DriverState.AVAILABLE: [DriverState.OFFLINE, DriverState.DISPATCHED],
        DriverState.DISPATCHED: [DriverState.AVAILABLE, DriverState.ENROUTE],
        DriverState.ENROUTE: [DriverState.AVAILABLE, DriverState.ARRIVED],
        DriverState.ARRIVED: [DriverState.AVAILABLE, DriverState.IN_RIDE],
        DriverState.IN_RIDE: [DriverState.AVAILABLE],
    }

    @staticmethod
    def _get_status_key(driver_id: int) -> str:
        return DRIVER_STATUS_KEY.format(driver_id=driver_id)

    @staticmethod
    def _get_heartbeat_key(driver_id: int) -> str:
        return DRIVER_HEARTBEAT_KEY.format(driver_id=driver_id)

    @staticmethod
    def _get_ride_key(driver_id: int) -> str:
        return DRIVER_RIDE_KEY.format(driver_id=driver_id)

    @classmethod
    def get_state(cls, driver_id: int) -> DriverState:
        """Get current driver state from Redis."""
        try:
            status_key = cls._get_status_key(driver_id)
            state_data = GracefulCache.get(status_key)

            if state_data:
                if isinstance(state_data, dict):
                    return DriverState(state_data.get('state', 'offline'))
                elif isinstance(state_data, str):
                    return DriverState(state_data)

            return DriverState.OFFLINE
        except Exception as e:
            logger.error(f"Error getting state for driver {driver_id}: {e}")
            return DriverState.OFFLINE

    @classmethod
    def set_state(
        cls,
        driver_id: int,
        new_state: DriverState,
        ride_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Set driver state with validation.

        Args:
            driver_id: Driver ID
            new_state: Target state
            ride_id: Associated ride ID (if applicable)
            metadata: Additional state data

        Returns:
            True if transition successful, False otherwise
        """
        try:
            current_state = cls.get_state(driver_id)

            # Validate transition
            if new_state not in cls.VALID_TRANSITIONS.get(current_state, []):
                logger.warning(
                    f"Invalid state transition: {current_state.value} -> {new_state.value} "
                    f"for driver {driver_id}"
                )
                return False

            # Build state data
            state_data = {
                'state': new_state.value,
                'previous_state': current_state.value,
                'timestamp': timezone.now().isoformat(),
                'ride_id': ride_id,
                'metadata': metadata or {}
            }

            # Persist to Redis
            status_key = cls._get_status_key(driver_id)
            GracefulCache.set(status_key, state_data, timeout=300)  # 5 min TTL

            # If associated with ride, store it
            if ride_id:
                ride_key = cls._get_ride_key(driver_id)
                GracefulCache.set(ride_key, {'ride_id': ride_id, 'state': new_state.value}, timeout=3600)

            logger.info(
                f"Driver {driver_id} state: {current_state.value} -> {new_state.value}"
                + (f" (ride={ride_id})" if ride_id else "")
            )
            return True

        except Exception as e:
            logger.exception(f"State transition failed for driver {driver_id}: {e}")
            return False

    @classmethod
    def record_heartbeat(cls, driver_id: int, location: Optional[Dict[str, float]] = None) -> bool:
        """
        Record driver heartbeat.

        Args:
            driver_id: Driver ID
            location: Dict with 'lat', 'lng', optionally 'heading'

        Returns:
            True if heartbeat recorded successfully
        """
        try:
            heartbeat_data = {
                'timestamp': timezone.now().isoformat(),
                'driver_id': driver_id,
                'location': location
            }

            heartbeat_key = cls._get_heartbeat_key(driver_id)
            GracefulCache.set(heartbeat_key, heartbeat_data, timeout=HEARTBEAT_TIMEOUT + 10)

            # Also update GEO location if provided
            if location and 'lat' in location and 'lng' in location:
                cls._update_geolocation(driver_id, location['lat'], location['lng'])

            return True
        except Exception as e:
            logger.error(f"Heartbeat recording failed for driver {driver_id}: {e}")
            return False

    @staticmethod
    def _update_geolocation(driver_id: int, lat: float, lng: float):
        """Update driver location in Redis GEO set."""
        try:
            from django_redis import get_redis_connection
            redis = get_redis_connection("default")
            # GEOADD key longitude latitude member
            redis.geoadd(DRIVER_LOCATION_KEY, (lng, lat, str(driver_id)))
        except Exception as e:
            logger.error(f"GEO location update failed: {e}")

    @classmethod
    def is_heartbeat_fresh(cls, driver_id: int) -> bool:
        """Check if driver has recent heartbeat."""
        try:
            heartbeat_key = cls._get_heartbeat_key(driver_id)
            heartbeat_data = GracefulCache.get(heartbeat_key)

            if not heartbeat_data:
                return False

            if isinstance(heartbeat_data, dict):
                heartbeat_time = datetime.fromisoformat(heartbeat_data['timestamp'])
                age = (timezone.now() - heartbeat_time).total_seconds()
                return age < HEARTBEAT_TIMEOUT

            return False
        except Exception as e:
            logger.error(f"Heartbeat check failed for driver {driver_id}: {e}")
            return False

    @classmethod
    def get_last_heartbeat(cls, driver_id: int) -> Optional[Dict[str, Any]]:
        """Get last heartbeat data for driver."""
        try:
            heartbeat_key = cls._get_heartbeat_key(driver_id)
            return GracefulCache.get(heartbeat_key)
        except Exception as e:
            logger.error(f"Error retrieving heartbeat for driver {driver_id}: {e}")
            return None

    @classmethod
    def can_accept_ride(cls, driver_id: int) -> bool:
        """
        Check if driver can accept a new ride.
        Must be AVAILABLE and have fresh heartbeat.
        """
        state = cls.get_state(driver_id)
        heartbeat_fresh = cls.is_heartbeat_fresh(driver_id)

        if state != DriverState.AVAILABLE:
            # Self-heal stale state cache: if DB still says online, recover to AVAILABLE.
            try:
                from drivers.models import Driver
                driver = Driver.objects.only("is_online", "last_location_update").get(id=driver_id)
                if driver.is_online:
                    cls.mark_available(driver_id)
                    state = cls.get_state(driver_id)
            except Exception as e:
                logger.debug(f"Driver {driver_id} state recovery skipped: {e}")

        if state != DriverState.AVAILABLE:
            logger.debug(f"Driver {driver_id} cannot accept ride: state={state.value}")
            return False

        if not heartbeat_fresh:
            logger.warning(f"Driver {driver_id} cannot accept ride: stale heartbeat")
            return False

        return True

    @classmethod
    def mark_offline(cls, driver_id: int) -> bool:
        """Mark driver as offline."""
        return cls.set_state(driver_id, DriverState.OFFLINE)

    @classmethod
    def mark_available(cls, driver_id: int) -> bool:
        """Mark driver as available for rides."""
        # Reset stale ride association when driver returns to available.
        # This prevents old dispatch keys from blocking new incoming rides.
        try:
            ride_key = cls._get_ride_key(driver_id)
            GracefulCache.delete(ride_key)
        except Exception as e:
            logger.debug(f"Failed clearing stale ride key for driver {driver_id}: {e}")
        return cls.set_state(driver_id, DriverState.AVAILABLE)

    @classmethod
    def assign_dispatch(cls, driver_id: int, ride_id: int) -> bool:
        """
        Assign ride to driver (dispatch).
        Sets state to DISPATCHED.
        """
        return cls.set_state(
            driver_id,
            DriverState.DISPATCHED,
            ride_id=ride_id,
            metadata={'dispatch_time': timezone.now().isoformat()}
        )

    @classmethod
    def accept_ride(cls, driver_id: int, ride_id: int) -> bool:
        """
        Driver accepts ride.
        Transition from DISPATCHED to ENROUTE.
        """
        current_state = cls.get_state(driver_id)
        current_ride = cls.get_current_ride(driver_id)

        if current_state != DriverState.DISPATCHED:
            logger.warning(f"Driver {driver_id} cannot accept: state={current_state.value}")
            return False

        # Handle type mismatch: current_ride might be int, ride_id might be string UUID
        if str(current_ride) != str(ride_id):
            logger.warning(f"Driver {driver_id} ride mismatch: expected={ride_id} ({type(ride_id)}), got={current_ride} ({type(current_ride)})")
            return False

        return cls.set_state(
            driver_id,
            DriverState.ENROUTE,
            ride_id=ride_id,
            metadata={'accepted_at': timezone.now().isoformat()}
        )

    @classmethod
    def mark_arrived(cls, driver_id: int, ride_id: int) -> bool:
        """Driver marks arrival at pickup."""
        return cls.set_state(
            driver_id,
            DriverState.ARRIVED,
            ride_id=ride_id,
            metadata={'arrived_at': timezone.now().isoformat()}
        )

    @classmethod
    def start_ride(cls, driver_id: int, ride_id: int) -> bool:
        """Driver starts ride (passenger picked up)."""
        return cls.set_state(
            driver_id,
            DriverState.IN_RIDE,
            ride_id=ride_id,
            metadata={'started_at': timezone.now().isoformat()}
        )

    @classmethod
    def complete_ride(cls, driver_id: int, ride_id: int) -> bool:
        """Complete ride and return to available."""
        # Clear ride association
        ride_key = cls._get_ride_key(driver_id)
        GracefulCache.delete(ride_key)

        return cls.set_state(
            driver_id,
            DriverState.AVAILABLE,
            metadata={'completed_ride': ride_id, 'completed_at': timezone.now().isoformat()}
        )

    @classmethod
    def get_current_ride(cls, driver_id: int) -> Optional[int]:
        """Get current ride ID for driver."""
        try:
            ride_key = cls._get_ride_key(driver_id)
            ride_data = GracefulCache.get(ride_key)

            if ride_data and isinstance(ride_data, dict):
                return ride_data.get('ride_id')
            return None
        except Exception as e:
            logger.error(f"Error getting current ride for driver {driver_id}: {e}")
            return None

    @classmethod
    def get_state_summary(cls, driver_id: int) -> Dict[str, Any]:
        """Get complete driver state summary."""
        state = cls.get_state(driver_id)
        heartbeat = cls.get_last_heartbeat(driver_id)
        current_ride = cls.get_current_ride(driver_id)

        summary = {
            'driver_id': driver_id,
            'state': state.value,
            'can_accept_ride': cls.can_accept_ride(driver_id),
            'heartbeat_fresh': cls.is_heartbeat_fresh(driver_id),
            'current_ride_id': current_ride,
        }

        if heartbeat:
            summary['last_heartbeat'] = heartbeat.get('timestamp')
            summary['last_location'] = heartbeat.get('location')

        return summary


class HeartbeatMonitor:
    """
    Background task to check driver heartbeats and mark stale drivers offline.
    """

    @classmethod
    def check_all_drivers(cls, driver_ids: list) -> Dict[str, list]:
        """
        Check heartbeats for all drivers.

        Returns:
            Dict with 'offline_marked' and 'in_ride_stale' lists
        """
        results = {
            'offline_marked': [],
            'in_ride_stale': [],
            'errors': []
        }

        for driver_id in driver_ids:
            try:
                state = DriverStateMachine.get_state(driver_id)
                heartbeat_fresh = DriverStateMachine.is_heartbeat_fresh(driver_id)

                if not heartbeat_fresh:
                    if state == DriverState.IN_RIDE:
                        # Critical: Driver lost during ride
                        results['in_ride_stale'].append(driver_id)
                        logger.critical(f"Driver {driver_id} lost heartbeat during ride!")
                    elif state != DriverState.OFFLINE:
                        # Mark non-offline drivers as offline
                        DriverStateMachine.mark_offline(driver_id)
                        results['offline_marked'].append(driver_id)
                        logger.info(f"Driver {driver_id} marked offline (stale heartbeat)")

            except Exception as e:
                results['errors'].append((driver_id, str(e)))
                logger.error(f"Error checking driver {driver_id}: {e}")

        return results

    @classmethod
    def handle_stale_in_ride_driver(cls, driver_id: int, ride_id: int) -> bool:
        """
        Handle driver who went stale during active ride.
        Triggers ride cancellation and driver replacement.
        """
        logger.critical(f"Handling stale driver {driver_id} in ride {ride_id}")

        # Mark driver offline
        DriverStateMachine.mark_offline(driver_id)

        # TODO: Trigger ride cancellation and reassignment
        # This should be handled by a Celery task

        return True
