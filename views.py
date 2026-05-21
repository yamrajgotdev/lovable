import logging
from math import radians, cos, sin, sqrt, atan2
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from django.conf import settings
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

logger = logging.getLogger('rides4u')

from .models import Driver, DriverLocation
from .serializers import (
    DriverSerializer, DriverRegistrationSerializer, NearbyDriverSerializer,
    DriverLocationSerializer
)
from drivers.services.driver_state import set_driver_online, set_driver_offline, update_driver_location_geo

from authsystem.views import get_authenticated_user
from utils.route_matching import find_drivers_on_route, decode_polyline
from utils.ola_maps import ola_maps
from rideapp.redis_utils import set_driver_location_ttl, set_driver_online_status
from utils.safety import (
    check_route_deviation, get_driver_active_ride, reset_location_failures,
    process_location_update_failure, validate_location_permission, set_location_permission
)


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def broadcast_driver_location(driver, event='upsert', lat=None, lng=None, heading=None):
    if lat is None:
        lat = driver.current_lat
    if lng is None:
        lng = driver.current_lng

    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    payload = {
        'type': 'nearby_driver_event',
        'event': event,
        'driver': {
            'id': str(driver.id),
            'lat': lat,
            'lng': lng,
            'vehicle': driver.vehicle_type,
            'heading': heading or 0,
        },
    }

    async_to_sync(channel_layer.group_send)('nearby_drivers_stream', payload)

    if lat is not None and lng is not None:
        async_to_sync(channel_layer.group_send)(
            f"driver_{driver.id}",
            {
                'type': 'location_update',
                'latitude': lat,
                'longitude': lng,
                'heading': heading or 0,
                'driver_id': driver.id,
            },
        )


class DriverRegistrationView(APIView):
    """
    POST /api/drivers/register/

    Register the currently authenticated user as a driver.
    Requires authentication — prevents anonymous account creation.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        # Prevent duplicate registrations
        if Driver.objects.filter(user=request.user).exists():
            return Response({
                'success': False,
                'message': 'A driver profile already exists for this account.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Inject the authenticated user into the serializer data
        data = request.data.copy()
        data['user'] = request.user.id

        serializer = DriverRegistrationSerializer(data=data)
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        driver = serializer.save(user=request.user)
        logger.info('driver_registered: user_id=%d driver_id=%d', request.user.id, driver.id)

        return Response({
            'success': True,
            'message': 'Driver registered successfully. Awaiting approval.',
            'driver': DriverSerializer(driver).data
        }, status=status.HTTP_201_CREATED)


class DriverProfileView(APIView):
    """
    GET /api/drivers/profile/

    Returns only the authenticated user's own driver profile.
    The phone_number URL parameter is removed — the profile is always
    the caller's own, preventing IDOR enumeration.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            driver = Driver.objects.get(user=request.user)
            return Response({
                'success': True,
                'driver': DriverSerializer(driver).data
            })
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver profile not found'
            }, status=status.HTTP_404_NOT_FOUND)


class ToggleOnlineView(APIView):
    """
    Toggle driver online/offline status.
    Requires location permission to be granted before going online.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        is_driver_approved = driver.is_approved or driver.approval_status == Driver.APPROVAL_APPROVED
        if not is_driver_approved:
            return Response({
                'success': False,
                'message': 'Driver account is not approved yet'
            }, status=status.HTTP_403_FORBIDDEN)

        if not driver.can_go_online():
            return Response({
                'success': False,
                'message': 'Please complete your profile (license, vehicle number, aadhaar)'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check driver's intent from request (not database state to avoid sync issues)
        wants_to_go_online = request.data.get('online', not driver.is_online)
        
        if wants_to_go_online:
            # Require location permission to go online
            has_location = request.data.get('location_permission_granted', False)
            current_lat = request.data.get('current_lat')
            current_lng = request.data.get('current_lng')
            
            # Validate location is provided and permission is granted
            if not has_location or not current_lat or not current_lng:
                return Response({
                    'success': False,
                    'requires_location': True,
                    'message': 'Location must be enabled to go online. Please allow location access.'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Store location permission and current location
            driver.location_permission_granted = True
            driver.current_lat = float(current_lat)
            driver.current_lng = float(current_lng)
            driver.last_location_update = timezone.now()
            driver.consecutive_location_failures = 0
            
            # Create or update DriverLocation record
            DriverLocation.objects.update_or_create(
                driver=driver,
                defaults={
                    'latitude': driver.current_lat,
                    'longitude': driver.current_lng,
                }
            )

            # IMPORTANT: Save driver location fields before going online
            driver.save(update_fields=[
                'location_permission_granted', 'current_lat', 'current_lng',
                'last_location_update', 'consecutive_location_failures'
            ])

        # Handle going offline (either by intent or current DB state)
        if not wants_to_go_online:
            if driver.is_online:
                set_driver_offline(driver)
                set_driver_online_status(driver.id, False)
                broadcast_driver_location(driver, event='offline')
            return Response({"success": True, "is_online": False})

        # Handle going online
        ok = set_driver_online(driver)

        if not ok:
           return Response({
               "success": False,
               "message": "Missing location or permission"
            }, status=403)

        set_driver_online_status(driver.id, True)
        set_driver_location_ttl(driver.id, driver.current_lat, driver.current_lng)
        broadcast_driver_location(driver, event='upsert')
        return Response({"success": True, "is_online": True})


class HeartbeatView(APIView):
    """
    POST /api/v1/driver/heartbeat/
    Drivers send this every 10s to stay online.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            driver = Driver.objects.get(user=request.user)
            if not driver.is_online:
                return Response({'success': False, 'message': 'Driver is offline'}, status=403)
            
            driver.last_location_update = timezone.now()
            driver.save(update_fields=['last_location_update'])
            
            # Update TTL in Redis
            set_driver_location_ttl(driver.id, driver.current_lat, driver.current_lng)
            try:
                from drivers.state_machine import DriverStateMachine
                DriverStateMachine.record_heartbeat(driver.id, {
                    'lat': driver.current_lat,
                    'lng': driver.current_lng,
                })
            except Exception as e:
                logger.warning(f"Failed to record heartbeat for driver {driver.id}: {e}")
            
            return Response({'success': True})
        except Driver.DoesNotExist:
            return Response({'success': False}, status=404)


class UpdateLocationView(APIView):
    """
    Update driver location.
    Also updates the legacy fields on Driver model for backward compatibility.
    Only online drivers can update their location.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        
        # Parse coordinates
        lat = request.data.get('lat') or request.data.get('latitude')
        lng = request.data.get('lng') or request.data.get('longitude')
        heading = request.data.get('heading')
        speed = request.data.get('speed')
        accuracy = request.data.get('accuracy')

        if not all([lat, lng]):
            return Response({
                'success': False,
                'message': 'lat/latitude and lng/longitude are required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return Response({
                'success': False,
                'message': 'Invalid coordinates'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # Only online drivers can update location
        if not driver.is_online:
            return Response({
                'success': False,
                'message': 'Driver must be online to update location'
            }, status=status.HTTP_403_FORBIDDEN)

        # Update or create DriverLocation (upsert pattern for single row per driver)
        DriverLocation.objects.update_or_create(
            driver=driver,
            defaults={
                'latitude': lat,
                'longitude': lng,
                'heading': float(heading) if heading is not None else None,
                'speed': float(speed) if speed is not None else None,
                'accuracy': float(accuracy) if accuracy is not None else None,
            }
        )

        # Also update legacy fields on Driver model for backward compatibility
        # -----------------------------------------------------
        # GPS SPOOFING DETECTION (CRITICAL SAFETY)
        # -----------------------------------------------------
        if driver.current_lat and driver.current_lng and driver.last_location_update:
            from utils.route_matching import haversine_distance
            dist = haversine_distance(driver.current_lat, driver.current_lng, lat, lng)
            time_diff = (timezone.now() - driver.last_location_update).total_seconds()
            
            if time_diff > 0:
                speed_kmh = (dist / time_diff) * 3600
                # If speed > 150 km/h or impossible jump (2km in 5s)
                if (dist > 2 and time_diff < 5) or speed_kmh > 150:
                    logger.warning(f"SPOOF_DETECTED: driver={driver.id} dist={dist:.2f}km time={time_diff:.1f}s speed={speed_kmh:.1f}km/h")
                    # Optionally flag driver here
        
        driver.current_lat = lat
        driver.current_lng = lng
        driver.last_location_update = timezone.now()
        
        # Reset consecutive failures on successful update
        reset_location_failures(driver)
        
        driver.save(update_fields=['current_lat', 'current_lng', 'last_location_update', 'consecutive_location_failures'])
        set_driver_location_ttl(driver.id, lat, lng)
        set_driver_online_status(driver.id, True)
        try:
            from drivers.state_machine import DriverStateMachine
            DriverStateMachine.record_heartbeat(driver.id, {'lat': lat, 'lng': lng, 'heading': heading})
        except Exception as e:
            logger.warning(f"Failed to record heartbeat for driver {driver.id}: {e}")
        
        # Update Redis GEO for ride dispatch
        update_driver_location_geo(driver, lat, lng)
        
        broadcast_driver_location(driver, event='upsert', lat=lat, lng=lng, heading=heading)

        # Check for route deviation if driver has active ride
        active_ride = get_driver_active_ride(driver)
        deviation_alert = None
        if active_ride:
            is_deviating, deviation_m, alert_created = check_route_deviation(
                driver, lat, lng, active_ride
            )
            if alert_created:
                deviation_alert = {
                    'type': 'route_deviation',
                    'deviation_meters': round(deviation_m, 1),
                    'ride_id': active_ride.id
                }

        response_data = {
            'success': True,
            'message': 'Location updated',
            'location': {
                'lat': lat,
                'lng': lng,
                'heading': heading,
                'speed': speed
            }
        }
        
        if deviation_alert:
            response_data['deviation_alert'] = deviation_alert
            response_data['warning'] = 'Route deviation detected. Admin has been notified.'

        return Response(response_data)


class NearbyDriversView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        lat = request.query_params.get('lat')
        lng = request.query_params.get('lng')
        radius = float(request.query_params.get('radius', settings.DRIVER_SEARCH_RADIUS_KM))
        vehicle_type = request.query_params.get('vehicle_type') or request.query_params.get('vehicle')
        driver_id = request.query_params.get('driver_id')

        # If tracking a specific driver, lat/lng are optional but helpful for bounds
        if not driver_id and (not lat or not lng):
            return Response({
                'success': False,
                'message': 'lat and lng are required (or driver_id for tracking)'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Base queryset
        queryset = Driver.objects.filter(
            Q(is_approved=True) | Q(approval_status=Driver.APPROVAL_APPROVED)
        ).select_related('location').only(
            'id', 'name', 'vehicle_type', 'vehicle_number', 'rating',
            'current_lat', 'current_lng', 'location__heading'
        )

        if driver_id:
            # Track specific driver
            drivers = queryset.filter(id=driver_id)
        else:
            # Nearby search using Redis GEO for speed
            try:
                lat = float(lat)
                lng = float(lng)
            except (TypeError, ValueError):
                return Response({'success': False, 'message': 'Invalid coordinates'}, status=400)

            from rideapp.redis_utils import find_nearby_drivers_geo
            nearby_redis = find_nearby_drivers_geo(lat, lng, radius_km=radius, limit=50)
            
            if nearby_redis:
                nearby_ids = [d['driver_id'] for d in nearby_redis]
                # Maintain Redis order if possible, or just filter
                drivers = queryset.filter(id__in=nearby_ids, is_online=True)
                # Map distances from Redis for serialization
                distances = {d['driver_id']: d['distance_km'] for d in nearby_redis}
            else:
                # Fallback to DB if Redis empty or fails
                lat_delta = radius / 111.0
                lng_delta = radius / (111.0 * cos(radians(lat)))
                drivers = queryset.filter(
                    is_online=True,
                    current_lat__gte=lat - lat_delta,
                    current_lat__lte=lat + lat_delta,
                    current_lng__gte=lng - lng_delta,
                    current_lng__lte=lng + lng_delta
                )
                distances = {}

            if vehicle_type:
                drivers = drivers.filter(vehicle_type=vehicle_type)

        nearby_drivers = []
        for driver in drivers:
            if driver.current_lat is None or driver.current_lng is None:
                continue
                
            dist = distances.get(driver.id)
            if dist is None and lat is not None and lng is not None:
                dist = haversine_distance(lat, lng, driver.current_lat, driver.current_lng)
                if not driver_id and dist > radius:
                    continue
            
            driver_data = NearbyDriverSerializer(driver).data
            driver_data['distance_km'] = round(dist, 2) if dist is not None else 0
            nearby_drivers.append(driver_data)

        if not driver_id:
            nearby_drivers.sort(key=lambda x: x['distance_km'])

        return Response({
            'success': True,
            'drivers': nearby_drivers,
            'count': len(nearby_drivers)
        })


class DriverDocumentsView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        user = request.user
        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        if 'profile_photo' in request.FILES:
            driver.profile_photo = request.FILES['profile_photo']
        if 'license_photo' in request.FILES:
            driver.license_photo = request.FILES['license_photo']
        if 'rc_photo' in request.FILES:
            driver.rc_photo = request.FILES['rc_photo']

        driver.save()

        return Response({
            'success': True,
            'message': 'Documents uploaded successfully'
        })


class DriverStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'success': True,
            'is_approved': driver.is_approved,
            'is_online': driver.is_online
        })


class NearbyRouteDriversView(APIView):
    """
    Debug endpoint: Find drivers along a route corridor.
    GET /api/drivers/nearby-route/?pickup_lat=...&pickup_lng=...&drop_lat=...&drop_lng=...&vehicle_type=...
    
    Returns drivers within the route corridor ranked by ETA.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        # Get query parameters
        pickup_lat = request.query_params.get('pickup_lat')
        pickup_lng = request.query_params.get('pickup_lng')
        drop_lat = request.query_params.get('drop_lat')
        drop_lng = request.query_params.get('drop_lng')
        vehicle_type = request.query_params.get('vehicle_type')
        corridor_width = request.query_params.get('corridor_width')

        # Validate required parameters
        if not all([pickup_lat, pickup_lng, drop_lat, drop_lng]):
            return Response({
                'success': False,
                'message': 'pickup_lat, pickup_lng, drop_lat, and drop_lng are required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            pickup_lat = float(pickup_lat)
            pickup_lng = float(pickup_lng)
            drop_lat = float(drop_lat)
            drop_lng = float(drop_lng)
        except (TypeError, ValueError):
            return Response({
                'success': False,
                'message': 'Invalid coordinates'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Parse optional corridor width
        corridor_width_km = settings.ROUTE_CORRIDOR_WIDTH_KM
        if corridor_width:
            try:
                corridor_width_km = float(corridor_width)
            except (TypeError, ValueError):
                pass  # Use default

        # Get route from Ola Maps
        route_result = ola_maps.get_route(pickup_lat, pickup_lng, drop_lat, drop_lng)
        
        if not route_result or not route_result.get('geometry'):
            return Response({
                'success': False,
                'detail': 'Ola route unavailable. Please try again.'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Decode polyline for response
        route_coords = decode_polyline(route_result.get('geometry', ''))
        
        # Find drivers on the route
        drivers = find_drivers_on_route(
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            drop_lat=drop_lat,
            drop_lng=drop_lng,
            route_polyline=route_result['geometry'],
            vehicle_type=vehicle_type,
            corridor_width_km=corridor_width_km,
            max_results=settings.ROUTE_MATCHING_MAX_RESULTS
        )

        return Response({
            'success': True,
            'method': 'route_corridor',
            'route': {
                'distance_km': round(route_result['distance_km'], 2),
                'duration_minutes': round(route_result['duration_minutes'], 1),
                'waypoints': len(route_coords)
            },
            'corridor_width_km': corridor_width_km,
            'drivers': drivers,
            'count': len(drivers)
        })


class DriverLocationDetailView(APIView):
    """
    Get driver's current location.
    GET /api/drivers/location/
    """
    permission_classes = [AllowAny]

    def get(self, request):
        user = get_authenticated_user(request)
        if not user:
            return Response({
                'success': False,
                'message': 'Authentication required'
            }, status=status.HTTP_401_UNAUTHORIZED)

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        # Get location from DriverLocation model or fallback to Driver
        if hasattr(driver, 'location') and driver.location:
            serializer = DriverLocationSerializer(driver.location)
            return Response({
                'success': True,
                'location': serializer.data
            })
        elif driver.current_lat and driver.current_lng:
            return Response({
                'success': True,
                'location': {
                    'latitude': driver.current_lat,
                    'longitude': driver.current_lng,
                    'heading': None,
                    'speed': None,
                    'accuracy': None,
                    'updated_at': driver.last_location_update
                }
            })
        else:
            return Response({
                'success': False,
                'message': 'No location available'
            }, status=status.HTTP_404_NOT_FOUND)


class LocationPermissionView(APIView):
    """
    Handle driver location permission status.
    POST /api/drivers/location-permission/
    
    Called by frontend after checking navigator.geolocation permission.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        permission_granted = request.data.get('permission_granted', False)
        
        set_location_permission(driver, permission_granted)

        return Response({
            'success': True,
            'permission_granted': permission_granted,
            'message': 'Location permission updated' if permission_granted else 'Location permission denied'
        })

    def get(self, request):
        """Get current location permission status."""
        user = request.user

        try:
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'success': True,
            'permission_granted': driver.location_permission_granted,
            'can_go_online': driver.location_permission_granted and driver.can_go_online()
        })
