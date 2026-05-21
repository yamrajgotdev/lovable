"""
Internal metrics endpoint for monitoring backend health and performance.
"""
import time
from django.http import JsonResponse
from django.views import View
from django.conf import settings
from django.core.cache import cache
from rides.models import Ride

class MetricsView(View):
    """
    GET /metrics/
    Returns basic system health metrics in JSON format.
    """
    def get(self, request):
        data = {
            "timestamp": time.time(),
            "status": "healthy",
            "db_connections": "ok",
            "redis_status": "ok" if settings.REDIS_AVAILABLE else "degraded",
            "stats": {
                "active_rides": Ride.objects.filter(status=Ride.STATUS_RIDE_STARTED).count(),
                "pending_rides": Ride.objects.filter(status=Ride.STATUS_SEARCHING_DRIVER).count(),
            }
        }
        return JsonResponse(data)
