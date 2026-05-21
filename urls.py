from django.urls import path
from .views import (
    DriverRegistrationView, DriverProfileView, ToggleOnlineView,
    UpdateLocationView, NearbyDriversView, DriverDocumentsView,
    DriverStatusView, NearbyRouteDriversView, DriverLocationDetailView,
    LocationPermissionView
)

urlpatterns = [
    path('register/', DriverRegistrationView.as_view(), name='driver_register'),
    path('profile/', DriverProfileView.as_view(), name='driver_profile'),
    path('toggle-online/', ToggleOnlineView.as_view(), name='toggle_online'),
    # Location endpoints (POST to update, GET to retrieve)
    path('location/', UpdateLocationView.as_view(), name='driver_location'),  # POST /api/drivers/location/
    path('location/detail/', DriverLocationDetailView.as_view(), name='driver_location_detail'),  # GET
    path('location-permission/', LocationPermissionView.as_view(), name='driver_location_permission'),  # GET/POST
    path('update-location/', UpdateLocationView.as_view(), name='update_location'),  # Legacy endpoint (backward compat)
    # Nearby driver search
    path('nearby/', NearbyDriversView.as_view(), name='nearby_drivers'),
    path('nearby-route/', NearbyRouteDriversView.as_view(), name='nearby_route_drivers'),  # Route-based matching
    path('documents/', DriverDocumentsView.as_view(), name='driver_documents'),
    path('status/', DriverStatusView.as_view(), name='driver_status'),
]
