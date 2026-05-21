import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rideapp.settings')
django.setup()
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from authsystem.channels_auth import TokenAuthMiddlewareStack
from rides.routing import websocket_urlpatterns as ride_ws
from drivers.routing import websocket_urlpatterns as driver_ws

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": TokenAuthMiddlewareStack(
        URLRouter(
            driver_ws + ride_ws
        )
    ),
})
