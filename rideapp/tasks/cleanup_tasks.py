"""
Ride Cleanup Tasks
"""
from celery import shared_task
from rides.models import Ride
from django.utils import timezone
from datetime import timedelta

@shared_task
def cleanup_stale_rides():
    """Cancel rides that have been searching for more than 60 seconds."""
    cutoff = timezone.now() - timedelta(seconds=60)
    Ride.objects.filter(status=Ride.STATUS_SEARCHING_DRIVER, requested_at__lt=cutoff).update(status=Ride.STATUS_CANCELLED)
