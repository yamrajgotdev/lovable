"""
Main URL configuration for RideConnect.
Provides both /api/ (frontend-compatible) and /api/v1/ (legacy) endpoints.
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from authsystem.views import UserProfileView
from rides.views import UserRideHistoryView
from payments.views import WebhookView
from rideapp.metrics import MetricsView

urlpatterns = [
    # Root redirect to API info
    path('', RedirectView.as_view(url='/api/v1/core/', permanent=False)),
    
    # Admin
    path('admin/', admin.site.urls),
    
    # Metrics
    path('metrics/', MetricsView.as_view(), name='metrics'),
    
    # New frontend-compatible API at /api/
    path('api/', include('rideapp.api_urls')),
    
    # Legacy v1 API (kept for backward compatibility)
    path('api/v1/core/', include('core.urls')),
    path('api/v1/auth/', include('authsystem.urls')),
    path('api/v1/drivers/', include('drivers.urls')),
    path('api/v1/rides/', include('rides.urls')),
    path('api/v1/maps/', include('utils.urls')),
    path('api/v1/payments/', include('payments.urls')),
    path('api/v1/user/profile/', UserProfileView.as_view(), name='user_profile'),
    path('api/v1/user/rides/', UserRideHistoryView.as_view(), name='user_ride_history'),

    # Public webhook compatibility route for external gateways
    re_path(r'^payments/razorpay/webhook/?$', WebhookView.as_view(), name='razorpay_webhook_public'),
]

from django.conf import settings
from django.conf.urls.static import static

# Serve media files - in DEBUG mode use static(), in production use a dedicated view
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
else:
    # In production, serve media files through Django (for admin access)
    # For high-traffic production, use nginx/apache instead
    from django.views.static import serve
    urlpatterns += [
        path('media/<path:path>', serve, {
            'document_root': settings.MEDIA_ROOT,
            'show_indexes': False,
        }),
    ]
