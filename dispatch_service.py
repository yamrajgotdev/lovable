"""
Redis-based Dispatch Service with Sequential Driver Notification
Uses Redis GEO commands for efficient driver discovery.
"""
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
from math import radians, cos, sin, sqrt, atan2
from django.utils import timezone

from rides.models import Ride
from drivers.models import Driver
from drivers.state_machine import DriverStateMachine, DriverState
from rideapp.redis_utils import GracefulCache

logger = logging.getLogger('rides.dispatch')

# Redis key patterns
RIDE_DISPATCH_QUEUE_KEY = "ride:{ride_id}:dispatch_queue"
RIDE_DISPATCH_LOCK_KEY = "ride:{ride_id}:dispatch_lock"
DRIVERS_LOCATIONS_KEY = "drivers:locations"
RIDE_ACCEPTED_KEY = "ride:{ride_id}:accepted"
RIDE_REJECTED_DRIVERS_KEY = "ride:{ride_id}:rejected"
DISPATCH_TIMEOUT_SECONDS = 15  # Time to wait for driver response


def _distance_km(lat1, lon1, lat2, lon2) -> float:
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    r = 6371
    dlat = radians(float(lat2) - float(lat1))
    dlon = radians(float(lon2) - float(lon1))
    a = sin(dlat / 2) ** 2 + cos(radians(float(lat1))) * cos(radians(float(lat2))) * sin(dlon / 2) ** 2
    return r * (2 * atan2(sqrt(a), sqrt(1 - a)))


class DispatchService:
    """
    Manages ride dispatch with sequential driver notification.
    Uses Redis GEO for location-based driver discovery.
    """

    @staticmethod
    def _get_dispatch_queue_key(ride_id: int) -> str:
        return RIDE_DISPATCH_QUEUE_KEY.format(ride_id=ride_id)

    @staticmethod
    def _get_dispatch_lock_key(ride_id: int) -> str:
        return RIDE_DISPATCH_LOCK_KEY.format(ride_id=ride_id)

    @staticmethod
    def _get_accepted_key(ride_id: int) -> str:
        return RIDE_ACCEPTED_KEY.format(ride_id=ride_id)

    @staticmethod
    def _get_rejected_key(ride_id: int) -> str:
        return RIDE_REJECTED_DRIVERS_KEY.format(ride_id=ride_id)

    @classmethod
    def find_nearby_drivers(
        cls,
        pickup_lat: float,
        pickup_lng: float,
        radius_km: float = 5.0,
        limit: int = 10,
        vehicle_type: str = None
    ) -> List[Dict]:
        """
        Find available drivers near pickup location using Redis GEOSEARCH.
        Filters by vehicle type to match ride requirements.

        Args:
            pickup_lat: Pickup latitude
            pickup_lng: Pickup longitude
            radius_km: Search radius in kilometers
            limit: Maximum results
            vehicle_type: Required vehicle type (bike, auto, erickshaw, mini, etc.)

        Returns:
            List of driver dicts with id, distance
        """
        try:
            from django_redis import get_redis_connection
            redis = get_redis_connection("default")

            # GEOSEARCH key FROMLONLAT lng lat BYRADIUS radius KM [ASC]
            results = redis.geosearch(
                DRIVERS_LOCATIONS_KEY,
                longitude=pickup_lng,
                latitude=pickup_lat,
                radius=radius_km,
                unit='km',
                sort='asc',  # Closest first
                withdist=True,
            )

            # Get driver IDs from results
            driver_ids = []
            for item in results:
                if isinstance(item, tuple):
                    driver_id = int(item[0])
                    distance = float(item[1])
                else:
                    driver_id = int(item)
                    distance = 0.0
                driver_ids.append((driver_id, distance))

            # Fetch driver details and filter by vehicle type + availability
            driver_ids_list = [d[0] for d in driver_ids]
            drivers_map = {}
            if driver_ids_list:
                from drivers.models import Driver
                driver_queryset = Driver.objects.filter(
                    id__in=driver_ids_list,
                    is_approved=True,
                    approval_status='approved'
                )
                # Filter by vehicle type if specified
                if vehicle_type:
                    driver_queryset = driver_queryset.filter(vehicle_type=vehicle_type)
                drivers_map = {d.id: d for d in driver_queryset}

            drivers_with_scores = []
            for driver_id, distance in driver_ids:
                driver = drivers_map.get(driver_id)
                if not driver:
                    continue

                # Check if driver is available via state machine
                if DriverStateMachine.can_accept_ride(driver_id):
                    # Suitability Score: Distance (70%) + Rating (30%)
                    # Lower score is better. Rating is 0-5, we invert it (5-rating).
                    rating = getattr(driver, 'rating', 5.0) or 5.0
                    rating_penalty = (5.0 - rating) * 0.5  # Max penalty 2.5
                    suitability_score = distance + rating_penalty
                    
                    drivers_with_scores.append({
                        'driver_id': driver_id,
                        'distance_km': round(distance, 2),
                        'vehicle_type': driver.vehicle_type,
                        'rating': rating,
                        'score': suitability_score
                    })

            # Sort by suitability score instead of just distance
            drivers_with_scores.sort(key=lambda x: x['score'])
            
            # Limit results after sorting
            drivers = drivers_with_scores[:limit]

            logger.info(f"find_nearby_drivers: found {len(drivers)} drivers for vehicle_type={vehicle_type} (ranked by suitability)")
            return drivers

        except Exception as e:
            logger.error(f"GEO search failed: {e}")
            # Fallback: return empty list, will use alternative dispatch
            return []

    @classmethod
    def create_dispatch_queue(
        cls,
        ride: Ride,
        radius_km: float = 5.0
    ) -> List[int]:
        """
        Create ordered dispatch queue for a ride.
        Only includes drivers with matching vehicle type.

        Args:
            ride: Ride instance
            radius_km: Search radius

        Returns:
            List of driver IDs in dispatch order
        """
        try:
            pickup_lat = ride.pickup_lat
            pickup_lng = ride.pickup_lng

            if not pickup_lat or not pickup_lng:
                logger.error(f"Invalid pickup location for ride {ride.id}")
                return []

            # Get ride's required vehicle type
            required_vehicle_type = ride.vehicle_type

            # Find nearby drivers using GEO with vehicle type filter
            nearby_drivers = cls.find_nearby_drivers(
                pickup_lat, pickup_lng, radius_km,
                vehicle_type=required_vehicle_type
            )

            logger.info(f"create_dispatch_queue: ride={ride.id} vehicle_type={required_vehicle_type} drivers_found={len(nearby_drivers)}")

            # Extract driver IDs in order of proximity
            driver_ids = [d['driver_id'] for d in nearby_drivers]

            # Store dispatch queue in Redis
            queue_key = cls._get_dispatch_queue_key(ride.id)
            queue_data = {
                'ride_id': ride.id,
                'driver_ids': driver_ids,
                'created_at': timezone.now().isoformat(),
                'current_index': 0,
                'status': 'searching'
            }
            GracefulCache.set(queue_key, queue_data, timeout=1800)  # 30 min

            logger.info(f"Dispatch queue created for ride {ride.id}: {driver_ids}")
            return driver_ids

        except Exception as e:
            logger.exception(f"Failed to create dispatch queue for ride {ride.id}: {e}")
            return []

    @classmethod
    def get_next_driver_for_dispatch(cls, ride_id: int) -> Optional[int]:
        """
        Get next driver in dispatch queue.

        Returns:
            Driver ID or None if queue exhausted
        """
        try:
            queue_key = cls._get_dispatch_queue_key(ride_id)
            queue_data = GracefulCache.get(queue_key)

            if not queue_data:
                logger.warning(f"No dispatch queue for ride {ride_id}")
                return None

            driver_ids = queue_data.get('driver_ids', [])
            current_index = queue_data.get('current_index', 0)

            # Get rejected drivers
            rejected_key = cls._get_rejected_key(ride_id)
            rejected_data = GracefulCache.get(rejected_key)
            rejected_ids = rejected_data.get('driver_ids', []) if rejected_data else []

            # Find next available driver
            while current_index < len(driver_ids):
                driver_id = driver_ids[current_index]
                queue_data['current_index'] = current_index + 1
                GracefulCache.set(queue_key, queue_data, timeout=1800)

                if driver_id in rejected_ids:
                    current_index += 1
                    continue

                # Check if still available
                if DriverStateMachine.can_accept_ride(driver_id):
                    return driver_id

                current_index += 1

            return None

        except Exception as e:
            logger.error(f"Error getting next driver for ride {ride_id}: {e}")
            return None

    @classmethod
    def notify_driver(cls, ride: Ride, driver_id: int) -> bool:
        """
        Notify driver of ride dispatch.
        Sets driver state to DISPATCHED.
        """
        try:
            # Check if ride already accepted
            accepted_key = cls._get_accepted_key(ride.id)
            if GracefulCache.get(accepted_key):
                logger.info(f"Ride {ride.id} already accepted, skipping notification")
                return False

            # Set driver state
            if not DriverStateMachine.assign_dispatch(driver_id, ride.id):
                logger.warning(f"Failed to assign dispatch to driver {driver_id}")
                return False

            driver = Driver.objects.get(id=driver_id)
            distance_km = _distance_km(
                driver.current_lat,
                driver.current_lng,
                ride.pickup_lat,
                ride.pickup_lng,
            )
            from rides.services.notification_service import NotificationService
            NotificationService.notify_driver_ride_request(
                driver,
                ride,
                distance_km,
                request_id=f"{ride.id}:{driver_id}",
            )

            logger.info(f"Driver {driver_id} notified for ride {ride.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to notify driver {driver_id}: {e}")
            return False

    @classmethod
    def acquire_accept_lock(cls, ride_id: int, driver_id: int, timeout: int = 10) -> bool:
        """
        Try to acquire lock for accepting ride.
        Prevents race conditions where multiple drivers accept simultaneously.

        Args:
            ride_id: Ride ID
            driver_id: Driver attempting to accept
            timeout: Lock timeout in seconds

        Returns:
            True if lock acquired (acceptance allowed)
        """
        try:
            lock_key = cls._get_dispatch_lock_key(ride_id)

            # Try to set lock with NX (only if not exists)
            from django_redis import get_redis_connection
            redis = get_redis_connection("default")

            lock_value = json.dumps({
                'driver_id': driver_id,
                'timestamp': timezone.now().isoformat()
            })

            # SET key value EX seconds NX
            acquired = redis.set(lock_key, lock_value, ex=timeout, nx=True)

            if acquired:
                logger.info(f"Driver {driver_id} acquired accept lock for ride {ride_id}")
                return True
            else:
                # Check if this driver already has the lock
                existing = redis.get(lock_key)
                if existing:
                    existing_data = json.loads(existing)
                    if existing_data.get('driver_id') == driver_id:
                        return True  # Already has lock

                logger.warning(f"Driver {driver_id} could not acquire lock for ride {ride_id}")
                return False

        except Exception as e:
            logger.error(f"Lock acquisition failed: {e}")
            # Fail open if Redis unavailable
            return True

    @classmethod
    def release_accept_lock(cls, ride_id: int):
        """Explicitly release the acceptance lock for a ride."""
        try:
            lock_key = cls._get_dispatch_lock_key(ride_id)
            from django_redis import get_redis_connection
            redis = get_redis_connection("default")
            redis.delete(lock_key)
            logger.info(f"Released accept lock for ride {ride_id}")
        except Exception as e:
            logger.error(f"Failed to release lock for ride {ride_id}: {e}")

    @classmethod
    def record_driver_rejection(cls, ride_id: int, driver_id: int) -> bool:
        """Record driver rejection to skip in future notifications."""
        try:
            rejected_key = cls._get_rejected_key(ride_id)
            rejected_data = GracefulCache.get(rejected_key) or {'driver_ids': []}

            if driver_id not in rejected_data['driver_ids']:
                rejected_data['driver_ids'].append(driver_id)
                GracefulCache.set(rejected_key, rejected_data, timeout=1800)

            return True
        except Exception as e:
            logger.error(f"Failed to record rejection: {e}")
            return False

    @classmethod
    def mark_ride_accepted(cls, ride_id: int, driver_id: int) -> bool:
        """
        Mark ride as accepted by driver.
        Prevents other drivers from accepting.
        """
        try:
            accepted_key = cls._get_accepted_key(ride_id)
            GracefulCache.set(accepted_key, {
                'driver_id': driver_id,
                'accepted_at': timezone.now().isoformat()
            }, timeout=3600)

            # Update driver state
            DriverStateMachine.accept_ride(driver_id, ride_id)

            logger.info(f"Ride {ride_id} accepted by driver {driver_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark ride accepted: {e}")
            return False

    @classmethod
    def is_ride_accepted(cls, ride_id: int) -> Optional[int]:
        """
        Check if ride has been accepted.

        Returns:
            Driver ID if accepted, None otherwise
        """
        try:
            accepted_key = cls._get_accepted_key(ride_id)
            data = GracefulCache.get(accepted_key)

            if data and isinstance(data, dict):
                return data.get('driver_id')
            return None
        except Exception as e:
            logger.error(f"Error checking ride status: {e}")
            return None

    @classmethod
    def cancel_dispatch(cls, ride_id: int) -> bool:
        """Cancel dispatch and cleanup Redis keys."""
        try:
            keys_to_delete = [
                cls._get_dispatch_queue_key(ride_id),
                cls._get_dispatch_lock_key(ride_id),
                cls._get_accepted_key(ride_id),
                cls._get_rejected_key(ride_id),
            ]

            for key in keys_to_delete:
                GracefulCache.delete(key)

            logger.info(f"Dispatch cancelled for ride {ride_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel dispatch: {e}")
            return False

    @classmethod
    def clear_ride_acceptance(cls, ride_id: int) -> bool:
        """Clear ride acceptance status when driver cancels - allows finding new driver."""
        try:
            accepted_key = cls._get_accepted_key(ride_id)
            GracefulCache.delete(accepted_key)
            logger.info(f"Cleared ride acceptance for ride {ride_id} - ready for new driver")
            return True
        except Exception as e:
            logger.error(f"Failed to clear ride acceptance: {e}")
            return False


class SequentialDispatchTask:
    """
    Celery task for batch driver dispatch.
    Notifies drivers in small batches with timeout.
    """

    @staticmethod
    def dispatch_ride(ride_id: int, radius_km: float = 5.0):
        """
        Start batch dispatch for a ride.
        Notifies top drivers in parallel for faster matching.
        """
        try:
            ride = Ride.objects.get(id=ride_id, status=Ride.STATUS_SEARCHING_DRIVER)
        except Ride.DoesNotExist:
            logger.warning(f"Ride {ride_id} not found or not in searching state")
            return

        # Create dispatch queue
        driver_ids = DispatchService.create_dispatch_queue(ride, radius_km)

        if not driver_ids:
            logger.warning(f"No available drivers for ride {ride_id}")
            # TODO: Trigger no-drivers-available handling
            return

        # Start batch notifications
        # In a full-fledged app, we notify a small batch (e.g. 2-3) of nearest drivers simultaneously
        # This is more efficient than strictly one-by-one.
        from rides.tasks import notify_driver_batch, handle_no_driver_acceptance

        # We notify top 2 drivers immediately
        batch_size = 2
        initial_batch = driver_ids[:batch_size]
        
        notify_driver_batch.delay(ride_id, initial_batch, batch_index=0)

    @staticmethod
    def process_driver_response(ride_id: int, driver_id: int, response: str):
        """
        Process driver response (accept/reject/timeout).
        """
        if response == 'accept':
            # Try to acquire lock
            if DispatchService.acquire_accept_lock(ride_id, driver_id):
                # Check if not already accepted
                if not DispatchService.is_ride_accepted(ride_id):
                    DispatchService.mark_ride_accepted(ride_id, driver_id)
                    return {'status': 'accepted', 'driver_id': driver_id}
                else:
                    return {'status': 'already_accepted'}
            else:
                return {'status': 'accept_failed'}

        elif response == 'reject':
            DispatchService.record_driver_rejection(ride_id, driver_id)
            DriverStateMachine.set_state(driver_id, DriverState.AVAILABLE)
            return {'status': 'rejected'}

        elif response == 'timeout':
            DispatchService.record_driver_rejection(ride_id, driver_id)
            DriverStateMachine.set_state(driver_id, DriverState.AVAILABLE)
            return {'status': 'timeout'}

        return {'status': 'unknown'}
