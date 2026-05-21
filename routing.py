from django.urls import re_path
from . import consumers
from rides.consumers import DriverNotificationConsumer

websocket_urlpatterns = [
    re_path(r'ws/driver/location/?$', consumers.DriverLocationConsumer.as_asgi()),
    re_path(r'ws/drivers/nearby/?$', consumers.NearbyDriversConsumer.as_asgi()),
    re_path(r'ws/driver/notifications/?$', DriverNotificationConsumer.as_asgi()),
]
