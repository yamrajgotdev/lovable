"""
Ride Service Layer
Contains all business logic for ride creation and management.
"""
from rides.models import Ride
from django.db import transaction

class RideService:
    @staticmethod
    @transaction.atomic
    def create_ride(passenger, pickup_lat, pickup_lng, drop_lat, drop_lng, vehicle_type, estimated_fare):
        ride = Ride.objects.create(
            passenger=passenger,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            drop_lat=drop_lat,
            drop_lng=drop_lng,
            vehicle_type=vehicle_type,
            estimated_fare=estimated_fare,
            status=Ride.STATUS_SEARCHING_DRIVER
        )
        return ride
