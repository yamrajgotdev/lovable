"""
Legacy v1 API URLs - kept for backward compatibility.
These are the original API endpoints under /api/v1/
"""
from django.urls import path, include

urlpatterns = [
    path('core/', include('core.urls')),
    path('auth/', include('authsystem.urls')),
    path('drivers/', include('drivers.urls')),
    path('rides/', include('rides.urls')),
    path('maps/', include('utils.urls')),
    path('payments/', include('payments.urls')),
]
