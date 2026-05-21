from django.urls import path
from . import consumers

websocket_urlpatterns = [
    # Ride-specific connection (Unified Chat endpoint)
    path('ws/chat/<int:ride_id>/', consumers.ChatConsumer.as_asgi()),
    path('ws/chat/<int:ride_id>', consumers.ChatConsumer.as_asgi()),

    # Restore Ride tracking route to fix 404
    path('ws/ride/<int:ride_id>/', consumers.RideConsumer.as_asgi()),
    path('ws/ride/<int:ride_id>', consumers.RideConsumer.as_asgi()),

    # Driver notifications
    path('ws/driver/notifications/', consumers.DriverNotificationConsumer.as_asgi()),

    # Waiting time updates
    path('ws/waiting/<int:ride_id>/', consumers.WaitingTimeConsumer.as_asgi()),

    # Driver stats (replaces polling)
    path('ws/driver/stats/', consumers.DriverStatsConsumer.as_asgi()),
    
    # Driver location updates (real-time)
    path('ws/driver/location/', consumers.DriverLocationConsumer.as_asgi()),
    
    path('ws/notifications/', consumers.UserNotificationConsumer.as_asgi()),
]
