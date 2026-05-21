"""
Dispatch Service Layer
Handles driver discovery and sequential dispatch logic.
"""
import logging
from django.conf import settings
from rides.models import Ride
from drivers.models import Driver
from utils.route_matching import haversine_distance

logger = logging.getLogger('rides4u.dispatch')

class DispatchService:
    @staticmethod
    def find_nearby_drivers(lat, lng, radius_km=10.0):
        # Implementation of Redis GEO search
        drivers = Driver.objects.filter(is_online=True, is_approved=True)
        nearby = []
        for d in drivers:
            if d.current_lat and d.current_lng:
                dist = haversine_distance(lat, lng, d.current_lat, d.current_lng)
                if dist <= radius_km:
                    nearby.append({'driver_id': d.id, 'distance': dist})
        return sorted(nearby, key=lambda x: x['distance'])

    @staticmethod
    def acquire_accept_lock(ride_id, driver_id, timeout=10):
        from django.core.cache import cache
        lock_key = f"ride_lock:{ride_id}"
        return cache.add(lock_key, driver_id, timeout=timeout)

    @staticmethod
    def mark_ride_accepted(ride_id, driver_id):
        # Clean up dispatch state
        pass
