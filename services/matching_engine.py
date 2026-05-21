"""
Driver Matching Engine
Sequential dispatch to nearest 3 drivers, then broadcast if all reject/timeout.
"""
import logging
import time
from typing import List, Dict, Optional, Tuple
from math import radians, cos, sin, sqrt, atan2
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from drivers.models import Driver
from rides.models import Ride
from rideapp.redis_utils import safe_cache_set, safe_cache_get, safe_cache_add, GracefulCache

logger = logging.getLogger('rides.matching')


class DriverMatchingEngine:
    """
    Driver matching with sequential dispatch strategy.
    
    Flow:
    1. Find nearest 3 ONLINE drivers
    2. Send request to driver 1 (8 second timeout)
    3. If rejected/timeout, send to driver 2 (8 second timeout)
    4. If rejected/timeout, send to driver 3 (8 second timeout)
    5. If all 3 fail, broadcast to ALL nearby drivers
    """

    DISPATCH_TIMEOUT_SECONDS = 8
    MAX_SEQUENTIAL_DRIVERS = 3
    BROADCAST_RADIUS_KM = 5
    LOCK_DURATION_SECONDS = 30  # Redis lock duration for preventing double assignment

    @classmethod
    def find_nearest_drivers(cls, pickup_lat: float, pickup_lng: float, 
                            vehicle_type: str = None, limit: int = 10) -> List[Dict]:
        """
        Find nearest ONLINE drivers to pickup location.
        
        Returns:
            List of drivers sorted by distance (nearest first)
        """
        # Get all online drivers
        drivers_query = Driver.objects.filter(
            status=Driver.STATUS_ONLINE,
            is_approved=True,
            current_lat__isnull=False,
            current_lng__isnull=False
        )
        
        if vehicle_type:
            drivers_query = drivers_query.filter(vehicle_type=vehicle_type)
        
        drivers = list(drivers_query)
        
        # Calculate distance for each
        drivers_with_distance = []
        for driver in drivers:
            distance = cls._haversine_distance(
                pickup_lat, pickup_lng,
                driver.current_lat, driver.current_lng
            )
            drivers_with_distance.append({
                'driver': driver,
                'distance_km': distance
            })
        
        # Sort by distance
        drivers_with_distance.sort(key=lambda x: x['distance_km'])
        
        # Return as list
        return drivers_with_distance[:limit]

    @classmethod
    def request_ride(cls, ride_id: int, pickup_lat: float, pickup_lng: float,
                    vehicle_type: str = None) -> Dict:
        """
        Main entry point for requesting a ride.
        Implements sequential dispatch logic.
        
        Returns:
            Dict with assignment result
        """
        result = {
            'success': False,
            'driver_assigned': False,
            'driver_id': None,
            'message': '',
            'broadcast_sent': False
        }
        
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            result['message'] = 'Ride not found'
            return result
        
        # Update ride status to searching via state machine
        from rides.services.state_machine import RideStateMachine
        success, msg, ride = RideStateMachine.transition(
            ride_id=ride.id,
            new_status=Ride.STATUS_SEARCHING_DRIVER,
            actor_type='system'
        )
        if not success:
            logger.error(f"Failed to transition ride {ride_id} to SEARCHING_DRIVER: {msg}")
            result['message'] = msg
            return result
        
        # Find nearest 3 drivers
        nearest_drivers = cls.find_nearest_drivers(
            pickup_lat, pickup_lng, vehicle_type, limit=cls.MAX_SEQUENTIAL_DRIVERS
        )
        
        if not nearest_drivers:
            # No drivers found - broadcast immediately
            logger.info(f"Ride {ride_id}: No drivers in sequential pool, broadcasting")
            broadcast_result = cls._broadcast_to_all(ride, pickup_lat, pickup_lng, vehicle_type)
            result['broadcast_sent'] = True
            result['message'] = 'No drivers nearby, broadcasted to all'
            return result
        
        logger.info(
            f"Ride {ride_id}: Found {len(nearest_drivers)} drivers for sequential dispatch. "
            f"Nearest: {nearest_drivers[0]['distance_km']:.2f}km"
        )
        
        # Try sequential dispatch to top 3
        for idx, driver_info in enumerate(nearest_drivers):
            driver = driver_info['driver']
            distance = driver_info['distance_km']
            
            # Try to acquire lock on driver
            if not cls._acquire_driver_lock(driver.id, ride_id):
                logger.info(f"Ride {ride_id}: Driver {driver.id} is locked, skipping")
                continue
            
            # Send dispatch request
            logger.info(
                f"Ride {ride_id}: Sending dispatch to driver {driver.id} "
                f"({idx+1}/{len(nearest_drivers)}), distance: {distance:.2f}km"
            )
            
            dispatch_result = cls._send_dispatch_request(
                driver, ride, pickup_lat, pickup_lng, distance
            )
            
            if dispatch_result.get('accepted'):
                # Driver accepted!
                logger.info(f"Ride {ride_id}: Driver {driver.id} ACCEPTED")
                
                # Assign driver to ride via state machine
                from rides.services.state_machine import RideStateMachine
                success, msg, ride = RideStateMachine.transition(
                    ride_id=ride.id,
                    new_status=Ride.STATUS_DRIVER_ASSIGNED,
                    actor_type='driver',
                    actor_id=driver.id,
                    metadata={'driver_id': driver.id}
                )
                
                if not success:
                    logger.error(f"Failed to assign driver {driver.id} to ride {ride_id}: {msg}")
                    continue
                
                # Update driver state
                from drivers.state_machine import DriverStateMachine
                DriverStateMachine.assign_dispatch(driver.id, ride.id)
                
                # Clear lock
                cls._release_driver_lock(driver.id)
                
                # Notify passenger
                cls._notify_passenger_driver_assigned(ride, driver)
                
                # ALSO notify driver IMMEDIATELY - instant push (no polling needed)
                from rides.services.notification_service import NotificationService
                NotificationService.notify_driver_ride_request(
                    driver, ride, distance_km, f"assigned:{ride.id}"
                )
                
                result['success'] = True
                result['driver_assigned'] = True
                result['driver_id'] = driver.id
                result['message'] = f'Driver {driver.name} assigned'
                return result
            
            # Driver rejected or timeout
            logger.info(
                f"Ride {ride_id}: Driver {driver.id} "
                f"{'rejected' if dispatch_result.get('rejected') else 'timeout'}"
            )
            cls._release_driver_lock(driver.id)
        
        # All 3 drivers failed - broadcast to all
        logger.info(f"Ride {ride_id}: All sequential drivers failed, broadcasting")
        broadcast_result = cls._broadcast_to_all(ride, pickup_lat, pickup_lng, vehicle_type)
        
        result['broadcast_sent'] = True
        result['message'] = 'All nearby drivers busy, broadcasted to all'
        return result

    @classmethod
    def _send_dispatch_request(cls, driver: Driver, ride: Ride, 
                              pickup_lat: float, pickup_lng: float,
                              distance_km: float) -> Dict:
        """
        Send dispatch request to a single driver with timeout.
        
        Returns:
            {'accepted': bool, 'rejected': bool, 'timeout': bool}
        """
        # Generate unique request ID
        request_id = f"dispatch:{ride.id}:{driver.id}:{int(time.time())}"
        
        # Store request in Redis
        safe_cache_set(request_id, {
            'ride_id': ride.id,
            'driver_id': driver.id,
            'status': 'pending',  # pending, accepted, rejected
            'pickup_lat': pickup_lat,
            'pickup_lng': pickup_lng,
            'distance_km': distance_km,
            'timestamp': timezone.now().isoformat()
        }, timeout=cls.DISPATCH_TIMEOUT_SECONDS + 5)
        
        # Send notification via notification service
        from rides.services.notification_service import NotificationService
        try:
            NotificationService.notify_driver_ride_request(
                driver, ride, distance_km, request_id
            )
        except Exception as e:
            logger.error(f"Error sending ride request notification: {e}")
        
        # Also send WebSocket notification for real-time
        cls._notify_driver_dispatch(driver.id, {
            'type': 'ride_request',
            'request_id': request_id,
            'ride_id': ride.id,
            'pickup_lat': pickup_lat,
            'pickup_lng': pickup_lng,
            'pickup_address': ride.pickup_address,
            'drop_address': ride.drop_address,
            'distance_km': round(distance_km, 2),
            'estimated_fare': float(ride.estimated_fare) if ride.estimated_fare else None,
            'vehicle_type': ride.vehicle_type,
            'timeout_seconds': cls.DISPATCH_TIMEOUT_SECONDS
        })
        
        # Wait for response with timeout
        start_time = time.time()
        while time.time() - start_time < cls.DISPATCH_TIMEOUT_SECONDS:
            response = safe_cache_get(request_id)
            if response:
                status = response.get('status')
                if status == 'accepted':
                    return {'accepted': True, 'rejected': False, 'timeout': False}
                elif status == 'rejected':
                    return {'accepted': False, 'rejected': True, 'timeout': False}
            
            time.sleep(0.5)  # Check every 500ms
        
        # Timeout
        return {'accepted': False, 'rejected': False, 'timeout': True}

    @classmethod
    def handle_driver_response(cls, request_id: str, driver_id: int, 
                              accepted: bool) -> Dict:
        """
        Handle driver response to dispatch request.
        Called when driver accepts or rejects via API.
        """
        request_data = safe_cache_get(request_id)
        
        if not request_data:
            return {'success': False, 'message': 'Request expired or not found'}
        
        if request_data.get('driver_id') != driver_id:
            return {'success': False, 'message': 'Driver ID mismatch'}
        
        # Update request status
        request_data['status'] = 'accepted' if accepted else 'rejected'
        request_data['responded_at'] = timezone.now().isoformat()
        safe_cache_set(request_id, request_data, timeout=30)
        
        return {
            'success': True,
            'message': f'Ride {"accepted" if accepted else "rejected"}'
        }

    @classmethod
    def _broadcast_to_all(cls, ride: Ride, pickup_lat: float, pickup_lng: float,
                         vehicle_type: str = None) -> Dict:
        """
        Broadcast ride request to all nearby drivers.
        Used when sequential dispatch to top 3 fails.
        """
        # Find all drivers in broadcast radius
        nearby_drivers = cls.find_nearest_drivers(
            pickup_lat, pickup_lng, vehicle_type, limit=50
        )
        
        broadcast_count = 0
        for driver_info in nearby_drivers:
            driver = driver_info['driver']
            distance = driver_info['distance_km']
            
            # Skip drivers already tried in sequential phase
            if distance < 5:  # Skip very close ones already tried
                continue
            
            # Send broadcast notification via notification service
            from rides.services.notification_service import NotificationService
            try:
                NotificationService.notify_driver_broadcast_ride(driver, ride, distance)
            except Exception as e:
                logger.error(f"Error sending broadcast notification: {e}")
            
            # Also send WebSocket
            cls._notify_driver_broadcast(driver.id, {
                'type': 'broadcast_ride_request',
                'ride_id': ride.id,
                'pickup_lat': pickup_lat,
                'pickup_lng': pickup_lng,
                'pickup_address': ride.pickup_address,
                'drop_address': ride.drop_address,
                'distance_km': round(distance, 2),
                'estimated_fare': float(ride.estimated_fare) if ride.estimated_fare else None,
                'vehicle_type': ride.vehicle_type
            })
            broadcast_count += 1
        
        logger.info(f"Ride {ride.id}: Broadcast to {broadcast_count} drivers")
        
        return {
            'broadcast_count': broadcast_count,
            'message': f'Broadcast to {broadcast_count} drivers'
        }

    @classmethod
    def _acquire_driver_lock(cls, driver_id: int, ride_id: int) -> bool:
        """
        Acquire Redis lock on driver to prevent double assignment.
        Uses atomic ADD operation.
        """
        lock_key = f'driver_lock:{driver_id}'
        # Try to add - will fail if key exists
        added = safe_cache_add(lock_key, {
            'ride_id': ride_id,
            'locked_at': timezone.now().isoformat()
        }, timeout=cls.LOCK_DURATION_SECONDS)
        return added

    @classmethod
    def _release_driver_lock(cls, driver_id: int):
        """Release lock on driver."""
        from rideapp.redis_utils import GracefulCache
        lock_key = f'driver_lock:{driver_id}'
        GracefulCache.delete(lock_key)

    @classmethod
    def _haversine_distance(cls, lat1: float, lon1: float, 
                           lat2: float, lon2: float) -> float:
        """Calculate distance between two points in km."""
        R = 6371  # Earth radius in km
        lat1_rad, lat2_rad = radians(lat1), radians(lat2)
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        
        a = sin(dlat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c

    @classmethod
    def _notify_driver_dispatch(cls, driver_id: int, notification: dict):
        """Send WebSocket notification to specific driver."""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'driver_notifications_{driver_id}',
                {
                    'type': 'ride_request',
                    'notification': notification
                }
            )
        except Exception as e:
            logger.error(f"Error notifying driver {driver_id}: {e}")

    @classmethod
    def _notify_driver_broadcast(cls, driver_id: int, notification: dict):
        """Send broadcast notification to driver."""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'driver_notifications_{driver_id}',
                {
                    'type': 'broadcast_ride',
                    'notification': notification
                }
            )
        except Exception as e:
            logger.error(f"Error broadcasting to driver {driver_id}: {e}")

    @classmethod
    def _notify_passenger_driver_assigned(cls, ride: Ride, driver: Driver):
        """Notify passenger that driver is assigned."""
        # Send formatted notification
        from rides.services.notification_service import NotificationService
        try:
            NotificationService.notify_passenger_driver_assigned(ride)
        except Exception as e:
            logger.error(f"Error sending driver assigned notification: {e}")
        
        # Also send WebSocket
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'ride_{ride.id}',
                {
                    'type': 'ride_update',
                    'status': 'driver_assigned',
                    'driver': {
                        'id': driver.id,
                        'name': driver.name,
                        'vehicle_type': driver.vehicle_type,
                        'vehicle_number': driver.vehicle_number,
                        'rating': driver.rating
                    }
                }
            )
        except Exception as e:
            logger.error(f"Error notifying passenger: {e}")
