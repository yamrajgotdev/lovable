"""
API URL configuration for frontend compatibility.
This module provides URL patterns at /api/ that match the frontend's expectations.
"""
import datetime
import secrets
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from django.utils import timezone

# Import views for API endpoints
from authsystem.views import UserProfileView, LogoutView, VerifyFirebaseView, CheckPhoneView
from drivers.views import (
    DriverRegistrationView, DriverProfileView, ToggleOnlineView,
    UpdateLocationView, NearbyDriversView, DriverStatusView,
    DriverLocationDetailView
)
from rides.views import (
    RequestRideView, RideStatusView, AcceptRideView,
    CancelRideView,
    PassengerRidesView, DriverRidesView, FeedbackView,
    AvailableRidesView, DriverRideHistoryView
)
from rides.chat_views import ChatMessagesView, SendMessageView, MarkReadView
from rides.notification_views import NotificationListView, NotificationMarkReadView
from rides.views_actions import ReachedDestinationView, SubmitRatingView as SubmitRatingActionView
from rides.support_ticket_views import (
    SupportTicketListView, CreateSupportTicketView,
    UserRidesForTicketView, TicketTopicsView,
    AdminSupportTicketListView, AdminTicketResponseView
)
from utils.views import AutocompleteView, ReverseGeocodeView, RouteView
from payments.views import (
    CreateOrderView, ConfirmCashView, PaymentStatusView, DriverWalletView, WebhookView,
    ConfirmCashCollectionView, InitiateOnlinePaymentView, VerifyOnlinePaymentView, PaymentStatusCheckView,
    SetPaymentMethodView
)

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.conf import settings
from django.db.models import Q
from typing import Optional

from authsystem.models import AuthToken
from authsystem.serializers import UserSerializer
from drivers.models import Driver
from rides.pricing import get_vehicle_fare_config, calculate_fare


def map_ride_status(status_value: str) -> str:
    from rides.services.state_machine import RideStateMachine
    raw = str(status_value or "").strip()
    if not raw:
        return ""
    
    # Check if it's already a public status
    if raw.lower() in [v.lower() for v in RideStateMachine.PUBLIC_STATUS_MAP.values()]:
        return raw.lower()

    # Convert internal constant name or value to public status
    if raw.startswith("STATUS_"):
        raw = RideStateMachine._to_status_value(raw)
    elif raw.upper() in set(RideStateMachine.KEY_TO_VALUE.values()):
        raw = raw.upper()
    
    return RideStateMachine.to_public_status(raw)


def map_payment_method(method_value: Optional[str]) -> Optional[str]:
    if not method_value:
        return None
    mapping = {
        'cash': 'cash',
        'CASH': 'cash',
        'razorpay_online': 'online',
        'online': 'online',
        'ONLINE': 'online',
    }
    return mapping.get(method_value, method_value.lower() if method_value else None)


def map_payment_status(status_value: Optional[str]) -> Optional[str]:
    if not status_value:
        return 'pending'
    mapping = {
        'pending': 'pending',
        'PENDING': 'pending',
        'cash_collected': 'paid',
        'paid': 'paid',
        'PAID': 'paid',
        'SUCCESS': 'paid',
        'success': 'paid',
        'failed': 'failed',
        'FAILED': 'failed',
    }
    return mapping.get(status_value, status_value.lower() if status_value else 'pending')


def map_payment_message(status_value: Optional[str]) -> Optional[str]:
    status_mapped = map_payment_status(status_value)
    messages = {
        'pending': 'Payment is pending confirmation.',
        'paid': 'Payment successful.',
        'failed': 'Payment failed. Please retry.',
    }
    return messages.get(status_mapped)


def _phone_last_10(raw_phone: str) -> str:
    digits = ''.join(ch for ch in str(raw_phone or '') if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def _resolve_or_create_token(request) -> str:
    current = getattr(request, 'auth', None)
    if current and getattr(current, 'key', None):
        return current.key

    token, _ = AuthToken.objects.update_or_create(
        user=request.user,
        defaults={
            'key': secrets.token_hex(32),
            'expires_at': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=15),
        },
    )
    return token.key


class SendOTPView(APIView):
    """Deprecated: Firebase Web SDK handles OTP send directly."""
    permission_classes = [AllowAny]

    def post(self, request):
        return Response(
            {
                'success': False,
                'message': 'This endpoint is deprecated. Use Firebase Web SDK signInWithPhoneNumber flow.',
                'code': 'FIREBASE_OTP_REQUIRED',
            },
            status=status.HTTP_410_GONE,
        )


class VerifyOTPFrontendView(APIView):
    """Deprecated: use /api/auth/verify-firebase with Firebase ID token."""
    permission_classes = [AllowAny]

    def post(self, request):
        return Response(
            {
                'success': False,
                'message': 'This endpoint is deprecated. Use /api/auth/verify-firebase with a Firebase ID token.',
                'code': 'FIREBASE_OTP_REQUIRED',
            },
            status=status.HTTP_410_GONE,
        )


class MeView(APIView):
    """GET /api/me - current user profile for frontend with active ride"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Check for active ride to show popup on home screen.
        # Only show after ride is accepted.
        from rides.models import Ride
        active_cutoff = timezone.now() - datetime.timedelta(hours=12)
        active_ride = Ride.objects.filter(
            passenger=user,
            status__in=[
                Ride.STATUS_REQUESTED,
                Ride.STATUS_SEARCHING_DRIVER,
                Ride.STATUS_DRIVER_ASSIGNED,
                Ride.STATUS_DRIVER_ARRIVING,
                Ride.STATUS_ARRIVED,
                Ride.STATUS_OTP_VERIFIED,
                Ride.STATUS_STARTED,
                Ride.STATUS_REACHED_DESTINATION,
                Ride.STATUS_PAYMENT_REQUIRED,
                Ride.STATUS_PAYMENT_CONFIRMED,
            ]
        ).filter(
            requested_at__gte=active_cutoff
        ).order_by('-requested_at', '-id').first()
        
        active_ride_data = None
        if active_ride:
            active_ride_data = {
                'id': str(active_ride.id),
                'status': map_ride_status(active_ride.status),
                'status_raw': active_ride.status,
                'pickup': active_ride.pickup_address or 'Current Location',
                'drop': active_ride.drop_address or 'Destination',
                'driver_name': active_ride.driver.name if active_ride.driver else None,
                'vehicle_type': active_ride.vehicle_type,
                # Pre-calculated route data (saved at booking)
                'expected_route_polyline': active_ride.expected_route_polyline,
                'driver_to_pickup_polyline': active_ride.driver_to_pickup_polyline,
                'route_duration_minutes': active_ride.route_duration_minutes,
            }

        return Response({
            'user': {
                'id': str(user.id),
                'name': user.name or '',
                'phone': user.phone_number or '',
                'role': 'driver' if user.is_driver else 'passenger',
                'rating': 5.0,
                'email': user.email or '',
            },
            'active_ride': active_ride_data
        })


from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page

class QuoteView(APIView):
    """POST /api/rides/quote - get fare quotes for different vehicle types with multiple route options"""
    permission_classes = [AllowAny]

    @method_decorator(cache_page(5))  # Cache for 5 seconds only (routes change frequently)
    def post(self, request):
        pickup = request.data.get('pickup', {})
        drop = request.data.get('drop', {})
        promo_input = request.data.get('promo') or request.data.get('promo_code') or ''
        
        pickup_lat = pickup.get('lat')
        pickup_lng = pickup.get('lng')
        drop_lat = drop.get('lat')
        drop_lng = drop.get('lng')
        
        if not all([pickup_lat, pickup_lng, drop_lat, drop_lng]):
            return Response({
                'success': False,
                'message': 'Missing pickup or drop coordinates'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            pickup_lat = float(pickup_lat)
            pickup_lng = float(pickup_lng)
            drop_lat = float(drop_lat)
            drop_lng = float(drop_lng)
        except ValueError:
            return Response({
                'success': False,
                'message': 'Invalid coordinates'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        from utils.ola_maps import ola_maps
        from rides.promotions import get_promo_preview
        
        # Fetch single suitable route from Ola
        route_data = ola_maps.get_route(pickup_lat, pickup_lng, drop_lat, drop_lng, preference='suitable')
        
        if not route_data:
            return Response(
                {
                    'success': False,
                    'detail': 'Ola route unavailable. Please try again.',
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        distance_km = route_data.get('distance_km', 0)
        duration_minutes = route_data.get('duration_minutes', 0)
        
        # Generate quotes for all vehicle types
        quotes = []
        for vehicle in ['bike', 'auto', 'erickshaw']:
            config = get_vehicle_fare_config(vehicle)
            if not config:
                continue
            base = config['base_fare']
            per_km = config['per_km']
            per_minute = config.get('per_minute', 1.0)
            min_fare = config.get('minimum_fare', 0)
            tax_percentage = config.get('tax_percentage', 5.0)  # Get configurable tax (default 5%)
            
            subtotal = base + (distance_km * per_km) + (duration_minutes * per_minute)
            tax = (subtotal * tax_percentage) / 100  # Use configurable tax percentage
            total_before_discount = subtotal + tax
            if total_before_discount < min_fare:
                total_before_discount = float(min_fare)
            total = total_before_discount
            promo_payload = None

            if promo_input:
                promo_preview = get_promo_preview(
                    str(promo_input),
                    request.user if getattr(request, 'user', None) and request.user.is_authenticated else None,
                    vehicle_type=vehicle,
                    fare_amount=total_before_discount,
                )
                promo_payload = {
                    'valid': promo_preview.get('valid', False),
                    'code': promo_preview.get('code'),
                    'message': promo_preview.get('message'),
                    'discount': promo_preview.get('discount', 0),
                }
                if promo_preview.get('valid'):
                    total = float(promo_preview.get('fare_after_discount', total_before_discount))
            
            quotes.append({
                'vehicle': vehicle,
                'base': base,
                'tax': round(tax, 2),
                'perKm': per_km,
                'distanceKm': round(distance_km, 2),
                'eta': max(2, round(duration_minutes * (0.8 if vehicle == 'bike' else 1.0))),
                'total': round(total, 0),
                'fareBeforeDiscount': round(total_before_discount, 2),
                'discount': round(float(total_before_discount) - float(total), 2),
                'promoCode': promo_payload.get('code') if promo_payload else None,
                'promoMessage': promo_payload.get('message') if promo_payload else None,
                'polyline': route_data.get('geometry', ''),
            })

        if not quotes:
            return Response(
                {
                    'success': False,
                    'detail': 'Fare configuration unavailable. Please try again shortly.',
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({'quotes': quotes})


class RequestRideFrontendView(APIView):
    """POST /api/rides/request - matches frontend expectation"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data.copy()
        pickup = data.get('pickup', {})
        drop = data.get('drop', {})
        
        # Map frontend format to backend format
        data['pickup_lat'] = pickup.get('lat')
        data['pickup_lng'] = pickup.get('lng')
        data['pickup_address'] = pickup.get('address', '')
        data['drop_lat'] = drop.get('lat')
        data['drop_lng'] = drop.get('lng')
        data['drop_address'] = drop.get('address', '')
        data['vehicle_type'] = data.get('vehicle', 'auto')
        
        request._full_data = data
        return RequestRideView().post(request)


class RideDetailView(APIView):
    """GET /api/rides/<id> - get ride details with realtime driver location"""
    permission_classes = [IsAuthenticated]

    def get(self, request, ride_id):
        from rides.models import Ride
        
        try:
            ride = Ride.objects.select_related('driver', 'driver__location', 'passenger').get(id=ride_id)
        except Ride.DoesNotExist:
            return Response({'success': False, 'message': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)
        
        # Security check
        if ride.passenger != request.user and (not ride.driver or ride.driver.user != request.user):
            return Response({'success': False, 'message': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        # Refresh driver-to-pickup polyline if missing and driver is assigned
        if ride.status == Ride.STATUS_DRIVER_ASSIGNED and not ride.driver_to_pickup_polyline and ride.driver:
            if ride.driver.current_lat and ride.driver.current_lng:
                from utils.ola_maps import ola_maps
                driver_route = ola_maps.get_route(
                    ride.driver.current_lat, ride.driver.current_lng,
                    ride.pickup_lat, ride.pickup_lng
                )
                if driver_route:
                    ride.driver_to_pickup_polyline = driver_route.get('geometry', '')
                    ride.save(update_fields=['driver_to_pickup_polyline'])

        # Use persisted fare components first to avoid drift/zero-fare regressions.
        fare_cfg = get_vehicle_fare_config(ride.vehicle_type) or {}
        base_fare = float(ride.base_fare or fare_cfg.get('base_fare', 0) or 0)
        per_km = float(fare_cfg.get('per_km', 0) or 0)
        fare_total = float(ride.final_fare or ride.estimated_fare or 0)
        fare_before_discount = float(ride.fare_before_discount or ride.estimated_fare or fare_total or 0)
        discount = float(ride.promo_discount_amount or 0)
        
        public_status = map_ride_status(ride.status)
        if ride.driver_id and public_status in {"requested", "searching_driver", "searching"}:
            public_status = "accepted"

        return Response({
            'ok': True,
            'ride': {
                'id': str(ride.id),
                'status': public_status,
                'status_raw': ride.status,
                'pickup': {'lat': ride.pickup_lat, 'lng': ride.pickup_lng, 'address': ride.pickup_address},
                'drop': {'lat': ride.drop_lat, 'lng': ride.drop_lng, 'address': ride.drop_address},
                'vehicle': ride.vehicle_type,
                'fare': {
                    'base': base_fare,
                    'tax': 0,
                    'perKm': per_km,
                    'total': fare_total,
                    'beforeDiscount': fare_before_discount,
                    'discount': discount,
                    'promoCode': ride.promo_code_snapshot or None,
                },
                'distance_km': float(ride.distance_km or 0),
                'code': ride.otp[:4] if ride.otp else '0000',
                'driver': {
                    'name': ride.driver.name,
                    'phone': ride.driver.user.phone_number if ride.driver.user else '',
                    'plate': ride.driver.vehicle_number or '',
                    'rating': ride.driver.rating,
                    'location': (
                        {
                            'lat': ride.driver.current_lat,
                            'lng': ride.driver.current_lng,
                            'heading': float(getattr(getattr(ride.driver, 'location', None), 'heading', 0) or 0),
                        }
                        if ride.driver.current_lat is not None and ride.driver.current_lng is not None
                        else None
                    )
                } if ride.driver else None,
                'passenger': {
                    'name': ride.passenger.name or 'User',
                    'phone': ride.passenger.phone_number
                },
                'createdAt': ride.requested_at.isoformat() if ride.requested_at else '',
                'polyline': ride.expected_route_polyline,
                'driverToPickupPolyline': ride.driver_to_pickup_polyline,
                # Pre-calculated route data (saved at booking/acceptance)
                'expected_route_polyline': ride.expected_route_polyline,
                'driver_to_pickup_polyline': ride.driver_to_pickup_polyline,
                'route_duration_minutes': ride.route_duration_minutes,
                'route_steps': ride.route_steps,
                'driver_to_pickup_distance_km': ride.driver_to_pickup_distance_km,
                'driver_to_pickup_duration_minutes': ride.driver_to_pickup_duration_minutes,
                'paymentMethod': map_payment_method(ride.payment_method),
                'paymentStatus': map_payment_status(ride.payment_status),
                'paymentMessage': map_payment_message(ride.payment_status),
            }
        })


class CancelRideFrontendView(APIView):
    """POST /api/rides/<id>/cancel - cancel a ride"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        data = request.data.copy()
        data['ride_id'] = ride_id
        request._full_data = data
        return CancelRideView().post(request)


class DriverOnlineView(APIView):
    """POST /api/driver/online - toggle online status"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return ToggleOnlineView().post(request)


class DriverStatsView(APIView):
    """GET /api/driver/stats - get driver statistics"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from drivers.models import Driver
        from rides.models import Ride
        from payments.models import DriverWallet, WalletTransaction
        from django.utils import timezone
        
        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get today's earnings (cash + online)
        today = timezone.now().date()
        today_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
        
        completed_rides = Ride.objects.filter(
            driver=driver,
            status='COMPLETED'
        )
        today_rides = completed_rides.filter(completed_at__gte=today_start)
        today_earnings = sum(
            float(ride.final_fare or ride.estimated_fare or 0)
            for ride in today_rides
        )
        
        # Get today's online earnings (wallet credits from Razorpay payments)
        today_credits = WalletTransaction.objects.filter(
            actor='driver',
            actor_id=str(driver.id),
            transaction_type='credit',
            type='ride_payment',
            created_at__gte=today_start
        )
        today_online_earnings = sum(float(tx.amount) for tx in today_credits)
        today_cash_earnings = today_earnings - today_online_earnings
        
        # Wallet balance = online payments available for withdrawal
        wallet, _ = DriverWallet.objects.get_or_create(driver=driver)
        
        return Response({
            'earningsToday': float(today_earnings) if today_earnings else 0,
            'cashEarningsToday': float(today_cash_earnings),
            'onlineEarningsToday': float(today_online_earnings),
            'totalRides': completed_rides.count(),
            'todayRides': today_rides.count(),
            'walletBalance': float(wallet.balance),
            'rating': driver.rating
        })


class AcceptRideFrontendView(APIView):
    """POST /api/driver/rides/<id>/accept - accept a ride"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        data = request.data.copy()
        data['ride_id'] = ride_id
        request._full_data = data
        return AcceptRideView().post(request)


class RejectRideFrontendView(APIView):
    """POST /api/driver/rides/<id>/reject - reject/skip a ride"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        from rideapp.redis_utils import GracefulCache
        from drivers.models import Driver
        
        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            return Response({'success': False, 'message': 'Driver not found'}, status=404)
        
        # Add driver to rejected list for this ride (so they don't get notified again)
        cache_key = f"ride_rejected:{ride_id}"
        rejected = GracefulCache.get(cache_key) or []
        if driver.id not in rejected:
            rejected.append(driver.id)
            GracefulCache.set(cache_key, rejected, timeout=300)  # 5 min ttl
        
        return Response({'success': True, 'ok': True, 'message': 'Ride skipped'})


class ArrivedPickupView(APIView):
    """POST /api/driver/rides/<id>/arrived - driver arrived at pickup"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        import logging
        logger = logging.getLogger('rides4u')

        # Log full request details
        user = request.user
        logger.info(f"[ARRIVED] ========== REQUEST START ==========")
        logger.info(f"[ARRIVED] URL: {request.path}")
        logger.info(f"[ARRIVED] Method: {request.method}")
        logger.info(f"[ARRIVED] User: {user.id} ({user})")
        logger.info(f"[ARRIVED] Ride ID from URL: {ride_id}")
        logger.info(f"[ARRIVED] Request data: {request.data}")
        logger.info(f"[ARRIVED] ========== REQUEST END ==========")

        # Verify driver
        try:
            from drivers.models import Driver
            driver = Driver.objects.get(user=user)
        except Driver.DoesNotExist:
            logger.error(f"[ARRIVED] Driver not found for user {user.id}")
            return Response({'success': False, 'message': 'Driver not found'}, status=status.HTTP_404_NOT_FOUND)

        # Get ride and verify assignment (within transaction for select_for_update)
        from django.db import transaction
        try:
            from rides.models import Ride
            with transaction.atomic():
                ride = Ride.objects.select_for_update().get(id=ride_id)
        except Ride.DoesNotExist:
            logger.error(f"[ARRIVED] Ride {ride_id} not found")
            return Response({'success': False, 'message': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)

        if ride.driver_id != driver.id:
            logger.error(f"[ARRIVED] Driver {driver.id} not assigned to ride {ride_id}")
            return Response({'success': False, 'message': 'Not assigned to this ride'}, status=status.HTTP_403_FORBIDDEN)

        logger.info(f"[ARRIVED] BEFORE: ride {ride_id} status = {ride.status}")

        # Use state machine for proper transition to ARRIVED
        from rides.services.state_machine import RideStateMachine
        success, message, updated_ride = RideStateMachine.transition(
            ride_id=ride_id,
            new_status=Ride.STATUS_ARRIVED,
            actor_type='driver',
            actor_id=driver.id
        )

        if not success:
            logger.error(f"[ARRIVED] State transition failed: {message}")
            return Response({'success': False, 'message': message}, status=status.HTTP_400_BAD_REQUEST)

        # Re-fetch from DB to confirm save
        ride.refresh_from_db()
        logger.info(f"[ARRIVED] AFTER: ride {ride_id} status = {ride.status} (DB value)")

        # Notify passenger
        try:
            from rides.tasks import notify_ride_arrived
            notify_ride_arrived.delay(ride.id, ride.passenger_id)
        except Exception as e:
            logger.error(f"[ARRIVED] Failed to queue notification: {e}")

        # Return ride in frontend format
        response_status = map_ride_status(ride.status)
        logger.info(f"[ARRIVED] RESPONSE: ride={ride_id}, status={response_status}, status_raw={ride.status}")
        
        return Response({
            'success': True,
            'message': 'Arrival confirmed',
            'ride': {
                'id': str(ride.id),
                'status': response_status,
                'status_raw': ride.status,
            }
        })


class StartRideFrontendView(APIView):
    """POST /api/driver/rides/<id>/start - start the ride"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        from rides.models import Ride
        from drivers.models import Driver
        from rides.services.state_machine import RideStateMachine
        from rides.tasks import notify_ride_started
        from django.db import transaction

        code = str(request.data.get('code', '')).strip()
        if len(code) < 4:
            return Response({'success': False, 'message': 'OTP code is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            return Response({'success': False, 'message': 'Driver not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            ride = Ride.objects.select_related('passenger').get(id=ride_id)
        except Ride.DoesNotExist:
            return Response({'success': False, 'message': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)

        if ride.driver_id != driver.id:
            return Response({'success': False, 'message': 'Not assigned to this ride'}, status=status.HTTP_403_FORBIDDEN)

        ride_status = (ride.status or "").upper()
        if ride_status in {Ride.STATUS_COMPLETED, Ride.STATUS_CANCELLED}:
            return Response({'success': False, 'message': 'Ride is already closed'}, status=status.HTTP_400_BAD_REQUEST)
        if ride_status != Ride.STATUS_ARRIVED:
            return Response(
                {'success': False, 'message': f'OTP can only be verified after arrival. Current status: {ride.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        otp_expiry_minutes = int(getattr(settings, 'RIDE_OTP_EXPIRY_MINUTES', 60))
        if ride.requested_at and (datetime.datetime.now(datetime.timezone.utc) - ride.requested_at).total_seconds() > otp_expiry_minutes * 60:
            return Response({'success': False, 'message': 'OTP has expired. Please cancel and rebook.'}, status=status.HTTP_400_BAD_REQUEST)

        expected_code = (ride.otp or '')[:4]
        if not expected_code or expected_code != code:
            return Response({'success': False, 'message': 'Invalid OTP code'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            ok, msg, _ = RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_OTP_VERIFIED,
                actor_type='driver',
                actor_id=driver.id
            )
            if not ok:
                return Response({'success': False, 'message': msg}, status=status.HTTP_400_BAD_REQUEST)

            ok, msg, _ = RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_STARTED,
                actor_type='driver',
                actor_id=driver.id
            )
            if not ok:
                return Response({'success': False, 'message': msg}, status=status.HTTP_400_BAD_REQUEST)

        ride.refresh_from_db()
        notify_ride_started.delay(ride.id, ride.passenger_id)

        return Response({
            'success': True,
            'message': 'Ride started',
            'ride': {
                'id': str(ride.id),
                'status': map_ride_status(ride.status),
                'status_raw': ride.status,
            }
        })


class CompleteRideFrontendView(APIView):
    """POST /api/driver/rides/<id>/complete - complete the ride"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        # Legacy compatibility: "complete" now means "reached destination".
        # Payment and final completion must proceed through PAYMENT_REQUIRED -> PAYMENT_CONFIRMED -> COMPLETED.
        return ReachedDestinationView().post(request, ride_id)


class CollectPaymentView(APIView):
    """POST /api/driver/rides/<id>/collect - collect payment"""
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        from payments.models import Payment
        from payments.services.payment_service import PaymentService
        from rides.models import Ride
        from rides.services.ride_engine import update_ride_status

        payment_method = request.data.get('method', 'cash')

        try:
            ride = Ride.objects.select_related('driver', 'passenger').get(id=ride_id)
        except Ride.DoesNotExist:
            return Response({'success': False, 'message': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)

        if not ride.driver or ride.driver.user_id != request.user.id:
            return Response({'success': False, 'message': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        ride_status_lower = map_ride_status(ride.status)
        if ride_status_lower not in {'payment_required', 'payment_confirmed'}:
            return Response({
                'success': False,
                'message': f'Ride is not ready for collection. Current status: {ride.status}',
            }, status=status.HTTP_400_BAD_REQUEST)

        payment, _ = Payment.objects.get_or_create(
            ride=ride,
            defaults={
                'passenger': ride.passenger,
                'amount': ride.final_fare or ride.estimated_fare or 0,
                'method': payment_method,
                'status': 'pending',
            },
        )

        if payment_method == 'cash':
            payment.method = 'cash'
            payment.save(update_fields=['method', 'updated_at'])
            payment = PaymentService.confirm_cash_payment(ride.id)
            ride.refresh_from_db()
            return Response({
                'ride': {
                    'id': str(ride.id),
                    'status': map_ride_status(ride.status),
                    'paymentMethod': map_payment_method(payment.method),
                    'paymentStatus': map_payment_status(payment.status),
                    'fare': {'total': float(ride.final_fare or ride.estimated_fare or 0)},
                }
            })

        if payment.status != 'paid':
            return Response({
                'success': False,
                'message': 'Online payment is not verified yet. Wait for payment confirmation before finishing the ride.',
            }, status=status.HTTP_400_BAD_REQUEST)

        if (ride.status or "").lower() != 'payment_confirmed':
            update_ride_status(ride, 'payment_confirmed')
            ride.refresh_from_db()

        return Response({
            'ride': {
                'id': str(ride.id),
                'status': map_ride_status(ride.status),
                'paymentMethod': map_payment_method(payment.method),
                'paymentStatus': map_payment_status(payment.status),
                'fare': {'total': float(ride.final_fare or ride.estimated_fare or 0)},
            }
        })


class MapsAutocompleteView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        # Frontend sends 'q', some other parts might send 'input'
        q = request.GET.get('q') or request.GET.get('input', '')
        if not q:
            return Response({
                'success': False,
                'error': 'Query parameter "q" or "input" is required',
                'suggestions': []
            }, status=status.HTTP_400_BAD_REQUEST)

        from utils.ola_maps import ola_maps
        import logging
        logger = logging.getLogger('rides4u')
        
        try:
            # Direct service call, bypass all indirect logic
            predictions = ola_maps.autocomplete(q)
            suggestions = []
            for p in predictions:
                suggestions.append({
                    'description': p.get('description', ''),
                    'place_id': p.get('place_id', ''),
                    'lat': p.get('lat', 0),
                    'lng': p.get('lng', 0)
                })
            
            logger.info(f"maps_autocomplete_success: q={q}, suggestions_count={len(suggestions)}")
            return Response({
                'success': True,
                'suggestions': suggestions,
                'count': len(suggestions)
            })
        except Exception as e:
            logger.error(f"maps_autocomplete_error: q={q}, error={str(e)}")
            return Response({
                'success': False,
                'error': str(e),
                'suggestions': []
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MapsReverseView(APIView):
    """GET /api/maps/reverse - reverse geocode"""
    permission_classes = [AllowAny]

    def get(self, request):
        lat = request.query_params.get('lat')
        lng = request.query_params.get('lng')
        
        if not lat or not lng:
            return Response({
                'success': False,
                'message': 'lat and lng required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Use existing reverse geocode view
        from django.http import QueryDict
        q_params = QueryDict(mutable=True)
        q_params.setlist('lat', [lat])
        q_params.setlist('lng', [lng])
        request._request.GET = q_params
        
        response = ReverseGeocodeView.as_view()(request._request)
        
        if hasattr(response, 'data') and response.data.get('success'):
            result = response.data.get('result', {})
            address = result.get('formatted_address', '')
            if not address:
                # Try to build from components
                components = result.get('address_components', [])
                address = ', '.join([c.get('long_name', '') for c in components[:3]])
            return Response({'address': address or 'Unknown location'})
        
        return Response({'address': 'Unknown location'})


class MapsDirectionsView(APIView):
    """GET /api/maps/directions - get route directions"""
    permission_classes = [AllowAny]

    def get(self, request):
        o = request.query_params.get('o', '')
        d = request.query_params.get('d', '')
        
        try:
            origin_lat, origin_lng = map(float, o.split(','))
            dest_lat, dest_lng = map(float, d.split(','))
        except (ValueError, AttributeError):
            return Response({
                'success': False,
                'message': 'Invalid origin or destination format (expected: lat,lng)'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Use existing route view
        from django.http import QueryDict
        q = QueryDict(mutable=True)
        q.setlist('origin_lat', [str(origin_lat)])
        q.setlist('origin_lng', [str(origin_lng)])
        q.setlist('dest_lat', [str(dest_lat)])
        q.setlist('dest_lng', [str(dest_lng)])
        request._request.GET = q
        
        response = RouteView.as_view()(request._request)
        
        if hasattr(response, 'data') and response.data.get('success'):
            return Response({
                'polyline': response.data.get('geometry', ''),
                'distanceKm': response.data.get('distance_km', 0),
                'durationMin': response.data.get('duration_minutes', 0)
            })
        
        return Response(
            {
                'success': False,
                'detail': 'Ola directions unavailable. Please try again.',
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class PromoValidateView(APIView):
    """POST /api/promos/validate - validate promo code for a route and vehicle"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        promo_input = (request.data.get('promo') or request.data.get('promo_code') or '').strip()
        vehicle = request.data.get('vehicle', 'auto')
        pickup = request.data.get('pickup', {}) or {}
        drop = request.data.get('drop', {}) or {}

        if not promo_input:
            return Response({
                'valid': False,
                'message': 'Promo code is required.'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            pickup_lat = float(pickup.get('lat'))
            pickup_lng = float(pickup.get('lng'))
            drop_lat = float(drop.get('lat'))
            drop_lng = float(drop.get('lng'))
        except (TypeError, ValueError):
            return Response({
                'valid': False,
                'message': 'Invalid pickup/drop coordinates.'
            }, status=status.HTTP_400_BAD_REQUEST)

        from utils.ola_maps import ola_maps
        route_data = ola_maps.get_route(pickup_lat, pickup_lng, drop_lat, drop_lng)
        if not route_data:
            return Response({
                'valid': False,
                'message': 'Route unavailable right now. Try again.'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        distance_km = route_data.get('distance_km', 0)
        duration_minutes = route_data.get('duration_minutes', 0)
        fare_amount = calculate_fare(distance_km, vehicle, duration_minutes)

        from rides.promotions import get_promo_preview
        preview = get_promo_preview(
            promo_input,
            request.user,
            vehicle_type=vehicle,
            fare_amount=fare_amount,
        )

        status_code = status.HTTP_200_OK if preview.get('valid') else status.HTTP_400_BAD_REQUEST
        return Response(preview, status=status_code)


class NearbyDriversFrontendView(APIView):
    """GET /api/drivers/nearby - get nearby drivers"""
    permission_classes = [AllowAny]

    @method_decorator(cache_page(10))  # Cache for 10 seconds
    def get(self, request):
        lat = request.query_params.get('lat')
        lng = request.query_params.get('lng')
        vehicle = request.query_params.get('vehicle')
        
        if not lat or not lng:
            return Response({
                'success': False,
                'message': 'lat and lng required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Use existing nearby drivers view without dropping the required coordinates.
        from django.http import QueryDict
        q_params = QueryDict(mutable=True)
        q_params.setlist('lat', [lat])
        q_params.setlist('lng', [lng])
        q_params.setlist('vehicle_type', [vehicle or ''])
        radius = request.query_params.get('radius')
        if radius:
            q_params.setlist('radius', [radius])
        request._request.GET = q_params
        
        response = NearbyDriversView.as_view()(request._request)
        
        if hasattr(response, 'data') and response.data.get('success'):
            drivers = response.data.get('drivers', [])
            formatted = []
            for d in drivers:
                formatted.append({
                    'id': str(d.get('id')),
                    'lat': d.get('current_lat', d.get('latitude', 0)),
                    'lng': d.get('current_lng', d.get('longitude', 0)),
                    'vehicle': d.get('vehicle_type', vehicle or 'auto'),
                    'heading': d.get('heading', 0)
                })
            return Response({'drivers': formatted})
        
        return Response({'drivers': []})


class RazorpayOrderView(APIView):
    """POST /api/payments/razorpay/order - create Razorpay order"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data.copy()
        ride_id = data.get('rideId')
        if ride_id:
            data['ride_id'] = ride_id
        data.setdefault('payment_method', 'razorpay_online')
        request._full_data = data
        response = CreateOrderView().post(request)
        if response.status_code >= 400:
            return response

        payload = response.data
        return Response({
            'paymentId': payload.get('payment_id'),
            'orderId': payload.get('razorpay_order_id'),
            'amount': payload.get('amount'),
            # Keeps the current QR UI from crashing during local testing.
            'qr': payload.get('razorpay_order_id') or '',
            'keyId': payload.get('razorpay_key_id'),
        }, status=response.status_code)


class CashCodeView(APIView):
    """POST /api/payments/cash/code - get cash payment code"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from rides.models import Ride

        ride_id = request.data.get('rideId') or request.data.get('ride_id')
        if not ride_id:
            return Response({'message': 'rideId is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            ride = Ride.objects.get(id=ride_id, passenger=request.user)
        except Ride.DoesNotExist:
            return Response({'message': 'Ride not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response({'code': (ride.otp or '0000')[:4]})


class PlacesSavedView(APIView):
    """GET /api/places/saved - get saved places"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from core.models import SavedPlace
        places = SavedPlace.objects.filter(user=request.user)
        return Response({
            'places': [
                {
                    'id': str(p.id),
                    'label': p.label,
                    'address': p.address,
                    'lat': float(p.latitude),
                    'lng': float(p.longitude)
                }
                for p in places
            ]
        })

    def post(self, request):
        from core.models import SavedPlace
        data = request.data
        place = SavedPlace.objects.create(
            user=request.user,
            label=data.get('label', ''),
            address=data.get('address', ''),
            latitude=data.get('lat', 0),
            longitude=data.get('lng', 0)
        )
        return Response({'place': {'id': str(place.id)}})


class PlacesSavedDetailView(APIView):
    """DELETE /api/places/saved/<id> - delete saved place"""
    permission_classes = [IsAuthenticated]

    def delete(self, request, place_id):
        from core.models import SavedPlace
        SavedPlace.objects.filter(id=place_id, user=request.user).delete()
        return Response({'ok': True})


class PassengerSignupView(APIView):
    """POST /api/auth/passenger/signup - passenger signup"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        name = str(request.data.get('name', '')).strip()
        phone = request.data.get('phone') or request.data.get('phone_number')
        email = str(request.data.get('email', '')).strip()

        if len(name) < 2:
            return Response({
                'success': False,
                'message': 'Name is required.'
            }, status=status.HTTP_400_BAD_REQUEST)

        if phone and _phone_last_10(phone) != _phone_last_10(request.user.phone_number):
            return Response({
                'success': False,
                'message': 'Phone mismatch with authenticated account.'
            }, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        user.name = name
        update_fields = ['name', 'updated_at']
        if email:
            user.email = email
            update_fields.append('email')
        user.save(update_fields=update_fields)

        return Response({
            'success': True,
            'token': _resolve_or_create_token(request),
            'user': UserSerializer(user).data,
        })


class RiderSignupView(APIView):
    """POST /api/auth/rider/signup - driver/rider signup"""
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        data = request.data
        required_fields = ['name', 'phone', 'dl_number', 'pan_number', 'rc_number', 'plate', 'aadhaar', 'vehicle_type']
        missing = [field for field in required_fields if not str(data.get(field, '')).strip()]
        if missing:
            return Response({
                'success': False,
                'message': f"Missing required fields: {', '.join(missing)}"
            }, status=status.HTTP_400_BAD_REQUEST)

        # Allow signup if authenticated OR if we are in a registration flow where user might not be fully linked yet
        user_instance = None
        if request.user.is_authenticated:
            if _phone_last_10(data.get('phone')) != _phone_last_10(request.user.phone_number):
                return Response({
                    'success': False,
                    'message': 'Phone mismatch with authenticated account.'
                }, status=status.HTTP_400_BAD_REQUEST)
            user_instance = request.user
        else:
            # Fallback for registration flow: find user by phone if not authenticated
            from authsystem.models import User
            user_instance = User.objects.filter(phone_number__icontains=_phone_last_10(data.get('phone'))).first()
            
        # CRITICAL: Ensure we found a real database user and they are NOT Anonymous
        if not user_instance or user_instance.is_anonymous:
             return Response({
                 'success': False, 
                 'message': 'User session not found. Please verify OTP first.'
             }, status=status.HTTP_401_UNAUTHORIZED)
        
        vehicle_type = str(data.get('vehicle_type', '')).strip().lower()
        allowed_vehicle_types = {value for value, _ in Driver.VEHICLE_TYPES}
        if vehicle_type not in allowed_vehicle_types:
            return Response({
                'success': False,
                'message': 'Invalid vehicle_type.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Update the user record
        user_instance.is_driver = True
        user_instance.name = str(data.get('name', '')).strip()
        user_instance.save(update_fields=['is_driver', 'name', 'updated_at'])

        driver_defaults = {
            'name': user_instance.name,
            'vehicle_type': vehicle_type,
            'vehicle_number': str(data.get('plate', '')).strip().upper(),
            'license_number': str(data.get('dl_number', '')).strip(),
            'aadhaar_number': str(data.get('aadhaar', '')).strip(),
            'pan_number': str(data.get('pan_number', '')).strip().upper(),
            'is_approved': False,
            'approval_status': Driver.APPROVAL_PENDING,
            'verification_notes': '',
            'approved_at': None,
            'rejected_at': None,
            'temp_offline_notification_sent': False,
            'status': Driver.STATUS_OFFLINE,
        }

        driver, created = Driver.objects.get_or_create(
            user=user_instance,
            defaults=driver_defaults,
        )

        if not created:
            for field, value in driver_defaults.items():
                setattr(driver, field, value)

        # Document uploads from frontend onboarding form.
        # Validate file size (>1KB) to prevent corrupted/empty uploads
        def validate_doc_file(file, field_name):
            if file.size < 1000:
                logger.warning(f"signup_rejected_small_file: user={user_instance.id} field={field_name} size={file.size}")
                return False
            return True

        if 'dl_photo' in request.FILES:
            if validate_doc_file(request.FILES['dl_photo'], 'dl_photo'):
                driver.license_photo = request.FILES['dl_photo']
        if 'rc_photo' in request.FILES:
            if validate_doc_file(request.FILES['rc_photo'], 'rc_photo'):
                driver.rc_photo = request.FILES['rc_photo']
        if 'aadhaar_photo' in request.FILES:
            if validate_doc_file(request.FILES['aadhaar_photo'], 'aadhaar_photo'):
                driver.aadhaar_photo = request.FILES['aadhaar_photo']
        if 'pan_photo' in request.FILES:
            if validate_doc_file(request.FILES['pan_photo'], 'pan_photo'):
                driver.pan_photo = request.FILES['pan_photo']

        driver.save()

        # Generate a real token for the user so they are logged in after signup
        from authsystem.views import TOKEN_LIFETIME_DAYS
        token, _ = AuthToken.objects.update_or_create(
            user=user_instance,
            defaults={
                'key': secrets.token_hex(32),
                'expires_at': datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=TOKEN_LIFETIME_DAYS),
            },
        )

        return Response({
            'success': True,
            'token': token.key,
            'user': {
                **UserSerializer(user_instance).data,
                'verification_status': 'pending',
            },
            'driver_id': driver.id,
            'new_driver_profile': created,
        })


class SetLanguageView(APIView):
    """PATCH /api/me/language - set user language"""
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        language = request.data.get('language', 'en')
        if language not in {'en', 'hi'}:
            return Response({
                'success': False,
                'message': 'Unsupported language',
            }, status=status.HTTP_400_BAD_REQUEST)
        request.user.language = language
        request.user.save(update_fields=['language', 'updated_at'])
        return Response({
            'user': {
                'id': str(request.user.id),
                'name': request.user.name or '',
                'phone': request.user.phone_number,
                'language': language
            }
        })


class DriverVerificationStatusView(APIView):
    """GET /api/driver/verification/status - get driver verification status"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from drivers.models import Driver
        
        try:
            driver = Driver.objects.get(user=request.user)
            status_val = driver.approval_status or ('approved' if driver.is_approved else 'pending')
            if driver.is_approved:
                status_val = 'approved'
        except Driver.DoesNotExist:
            status_val = 'pending'
            driver = None

        reason = None
        if status_val == 'pending':
            reason = 'Documents under review'
        elif status_val == 'rejected':
            reason = (driver.verification_notes if driver else '') or 'Verification rejected. Please re-submit documents.'
        
        return Response({
            'status': status_val,
            'reason': reason
        })


class EarningsView(APIView):
    """GET /api/driver/earnings - get driver earnings (cash + online) and wallet data"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from drivers.models import Driver
        from rides.models import Ride
        from payments.models import WalletTransaction, DriverWallet
        from django.utils import timezone
        from datetime import timedelta
        from decimal import Decimal
        
        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Driver not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        range_param = request.query_params.get('range', 'week')
        
        # Calculate date range
        today = timezone.now().date()
        if range_param == 'month':
            start_date = today - timedelta(days=30)
        else:
            start_date = today - timedelta(days=7)
        
        # Get rides in range
        rides = Ride.objects.filter(
            driver=driver,
            completed_at__date__gte=start_date,
            status='COMPLETED'
        )
        
        # Calculate total earnings (cash + online)
        total_earnings = sum(float(ride.final_fare or ride.estimated_fare or 0) for ride in rides)
        total_rides = rides.count()
        
        # Calculate online earnings (from wallet credits - Razorpay confirmed payments)
        wallet_credits = WalletTransaction.objects.filter(
            actor='driver',
            actor_id=str(driver.id),
            transaction_type='credit',
            type='ride_payment',
            created_at__date__gte=start_date
        )
        online_earnings = sum(float(tx.amount) for tx in wallet_credits)
        
        # Cash earnings = total - online
        cash_earnings = total_earnings - online_earnings
        
        # Daily breakdown
        daily = []
        for i in range(7 if range_param == 'week' else 30):
            date = today - timedelta(days=i)
            day_rides = rides.filter(completed_at__date=date)
            day_total = sum(float(ride.final_fare or ride.estimated_fare or 0) for ride in day_rides)
            daily.append({
                'date': date.isoformat(),
                'earnings': float(day_total),
                'rides': day_rides.count()
            })
        daily.reverse()
        
        # By vehicle type
        by_vehicle = []
        for vtype in ['auto', 'bike', 'erickshaw']:
            v_rides = rides.filter(vehicle_type=vtype)
            v_total = sum(float(ride.final_fare or ride.estimated_fare or 0) for ride in v_rides)
            by_vehicle.append({
                'vehicle': vtype,
                'earnings': float(v_total),
                'rides': v_rides.count()
            })

        # Get wallet balance (online payments only)
        wallet, _ = DriverWallet.objects.get_or_create(driver=driver)
        
        # Get payouts/withdrawals
        payouts = []
        payout_items = WalletTransaction.objects.filter(
            actor='driver',
            actor_id=str(driver.id),
            transaction_type='debit',
            type='payout',
            created_at__date__gte=start_date
        ).order_by('-created_at')[:20]
        for tx in payout_items:
            payouts.append({
                'id': f'payout_{tx.id}',
                'amount': float(abs(tx.amount)),
                'status': 'paid',
                'date': tx.created_at.isoformat(),
            })
        
        return Response({
            'totals': {
                'earnings': float(total_earnings),
                'rides': total_rides,
                'onlineMinutes': 0,
                'tips': 0,
                'cashEarnings': float(cash_earnings),
                'onlineEarnings': float(online_earnings),
                'walletBalance': float(wallet.balance)
            },
            'daily': daily,
            'byVehicle': by_vehicle,
            'payouts': payouts
        })


class WalletWithdrawView(APIView):
    """POST /api/driver/wallet/withdraw - withdraw from wallet"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from decimal import Decimal, InvalidOperation
        from drivers.models import Driver
        from payments.services.wallet_service import WalletService

        raw_amount = request.data.get('amount', 0)
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            return Response({
                'ok': False,
                'message': 'Invalid amount',
            }, status=status.HTTP_400_BAD_REQUEST)

        if amount <= 0:
            return Response({
                'ok': False,
                'message': 'Amount must be greater than zero',
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            return Response({
                'ok': False,
                'message': 'Driver not found',
            }, status=status.HTTP_404_NOT_FOUND)

        tx = WalletService.debit_wallet(
            driver=driver,
            amount=amount,
            type_choice='payout',
            description=f'Driver payout withdrawal request of {amount}',
        )
        if not tx:
            return Response({
                'ok': False,
                'message': 'Insufficient wallet balance',
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'ok': True,
            'payoutId': f'payout_{tx.id}',
        })


class IncomingRideView(APIView):
    """GET /api/driver/incoming - get incoming ride request for this driver"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from drivers.models import Driver
        from rides.models import Ride
        from rideapp.redis_utils import GracefulCache
        from math import radians, sin, cos, sqrt, atan2
        import logging
        logger = logging.getLogger(__name__)
        
        def haversine_distance(lat1, lng1, lat2, lng2):
            """Calculate distance between two points in km"""
            R = 6371  # Earth's radius in km
            lat1_rad, lat2_rad = radians(float(lat1)), radians(float(lat2))
            dlat = radians(float(lat2) - float(lat1))
            dlng = radians(float(lng2) - float(lng1))
            a = sin(dlat/2)**2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlng/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            return R * c
        
        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            logger.warning(f"[IncomingRide] Driver not found for user {request.user.id}")
            return Response({'ride': None})
        
        # Driver must be online and have location
        logger.info(f"[IncomingRide] Driver {driver.id}: online={driver.is_online}, approved={driver.is_approved}, lat={driver.current_lat}, lng={driver.current_lng}")
        
        if not driver.is_online:
            logger.info(f"[IncomingRide] Driver {driver.id} not online")
            return Response({'ride': None})
            
        if not driver.is_approved:
            logger.info(f"[IncomingRide] Driver {driver.id} not approved")
            return Response({'ride': None})
            
        if driver.current_lat is None or driver.current_lng is None:
            logger.info(f"[IncomingRide] Driver {driver.id} has no location")
            return Response({'ride': None})
        
        # Sync state machine: if driver is online in DB but state machine shows offline, fix it
        from drivers.state_machine import DriverStateMachine, DriverState
        from rides.dispatch_service import DispatchService
        current_state = DriverStateMachine.get_state(driver.id)
        if current_state == DriverState.OFFLINE:
            logger.info(f"[IncomingRide] Syncing driver {driver.id} state from offline to available")
            DriverStateMachine.mark_available(driver.id)
        current_ride_id = DriverStateMachine.get_current_ride(driver.id)
        if current_ride_id:
            try:
                current_ride = Ride.objects.only('id', 'status', 'driver_id').get(id=current_ride_id)
                if (
                    current_ride.driver_id != driver.id or
                    current_ride.status in [Ride.STATUS_COMPLETED, Ride.STATUS_CANCELLED]
                ):
                    logger.info(f"[IncomingRide] Clearing stale current_ride for driver {driver.id}: {current_ride_id}")
                    DriverStateMachine.mark_available(driver.id)
                    current_ride_id = None
            except Ride.DoesNotExist:
                logger.info(f"[IncomingRide] Missing current_ride {current_ride_id} for driver {driver.id}; clearing state")
                DriverStateMachine.mark_available(driver.id)
                current_ride_id = None
        
        # Find rides that are searching for drivers and haven't been rejected by this driver
        # AND respect the sequential dispatch queue
        # Filter rides by matching vehicle type at database level for efficiency
        # Use uppercase status to match model constants
        pending_rides = Ride.objects.filter(
            status__in=[Ride.STATUS_REQUESTED, Ride.STATUS_SEARCHING_DRIVER],
            vehicle_type__iexact=driver.vehicle_type  # Only get rides matching driver's vehicle
        ).filter(
            Q(driver__isnull=True) | Q(driver_id=driver.id)
        ).order_by('-requested_at')[:5]

        logger.info(f"[IncomingRide] Driver {driver.id} ({driver.vehicle_type}): found {len(pending_rides)} pending rides with matching vehicle type")

        for ride in pending_rides:
            # Skip rides that were already accepted by someone else.
            accepted_driver_id = DispatchService.is_ride_accepted(ride.id)
            if accepted_driver_id and str(accepted_driver_id) != str(driver.id):
                continue

            # Check if this driver already rejected this ride.
            rejected_key = DispatchService._get_rejected_key(ride.id)
            rejected_data = GracefulCache.get(rejected_key) or {}
            if isinstance(rejected_data, dict):
                rejected_drivers = rejected_data.get('driver_ids', [])
            elif isinstance(rejected_data, list):
                rejected_drivers = rejected_data
            else:
                rejected_drivers = []
            if driver.id in rejected_drivers:
                logger.info(f"[IncomingRide] Driver {driver.id} already rejected ride {ride.id}")
                continue

            # Primary gate: current dispatched ride in state machine.
            is_dispatched_for_driver = str(current_ride_id) == str(ride.id)

            # Fallback gate: recover when current_ride key is missing/stale.
            if not is_dispatched_for_driver:
                queue_key = DispatchService._get_dispatch_queue_key(ride.id)
                queue_data = GracefulCache.get(queue_key) or {}
                queue_driver_ids = queue_data.get('driver_ids', []) if isinstance(queue_data, dict) else []
                batch_index = GracefulCache.get(f"ride:{ride.id}:current_batch")
                try:
                    batch_index = int(batch_index) if batch_index is not None else 0
                except (TypeError, ValueError):
                    batch_index = 0
                batch_size = 2
                start = batch_index * batch_size
                end = start + batch_size
                active_batch_driver_ids = queue_driver_ids[start:end]

                # If redis queue data is missing, fail-open for first matching ride so online
                # drivers still receive requests instead of seeing none.
                if not queue_driver_ids and current_ride_id is None:
                    is_dispatched_for_driver = True
                elif driver.id in active_batch_driver_ids and current_ride_id is None:
                    is_dispatched_for_driver = True
                    DriverStateMachine.assign_dispatch(driver.id, ride.id)

            if not is_dispatched_for_driver:
                continue
            
            logger.info(f"[IncomingRide] Returning ride {ride.id} to driver {driver.id}")
            
            # Calculate distances
            driver_to_pickup_km = float(ride.driver_to_pickup_distance_km or 0)
            if driver_to_pickup_km <= 0:
                driver_to_pickup_km = haversine_distance(
                    driver.current_lat, driver.current_lng,
                    ride.pickup_lat, ride.pickup_lng
                )
            # Use booked Ola Maps distance captured at ride request time.
            pickup_to_drop_km = float(ride.distance_km or 0)
            
            # Get exact fare-rule breakdown (base + per_km + per_minute) used for display.
            fare_cfg = get_vehicle_fare_config(ride.vehicle_type) or {}
            base_fare = float(fare_cfg.get('base_fare', 0))
            per_km = float(fare_cfg.get('per_km', 0))
            per_minute = float(fare_cfg.get('per_minute', 0))
            duration_minutes = float(ride.route_duration_minutes or 0)
            distance_fare = pickup_to_drop_km * per_km
            time_fare = duration_minutes * per_minute
            subtotal = base_fare + distance_fare + time_fare
            discount_amount = float(ride.promo_discount_amount or 0)
            total_fare = float(ride.estimated_fare or 0)
            
            # Return the first non-rejected ride with full details
            return Response({
                'ride': {
                    'id': str(ride.id),
                    'pickup': {
                        'address': ride.pickup_address or '',
                        'lat': ride.pickup_lat,
                        'lng': ride.pickup_lng,
                    },
                    'drop': {
                        'address': ride.drop_address or '',
                        'lat': ride.drop_lat,
                        'lng': ride.drop_lng,
                    },
                    'vehicle': ride.vehicle_type,
                    'fare': {
                        'total': total_fare,
                        'base': base_fare,
                        'perKm': per_km,
                        'perMinute': per_minute,
                        'distanceKm': round(pickup_to_drop_km, 2),
                        'distanceFare': round(distance_fare, 2),
                        'timeFare': round(time_fare, 2),
                        'subtotal': round(subtotal, 2),
                        'tax': 0,
                        'discount': discount_amount,
                    },
                    'distances': {
                        'driverToPickupKm': round(driver_to_pickup_km, 1),
                        'pickupToDropKm': round(pickup_to_drop_km, 1),
                    },
                    'driver_to_pickup_km': round(driver_to_pickup_km, 2),
                    'pickup_to_drop_km': round(pickup_to_drop_km, 2),
                    # Keep compatibility key aligned to trip distance (not driver->pickup).
                    'distance_km': round(pickup_to_drop_km, 2),
                    'status': ride.status,
                    'otp': ride.otp,
                    # Pre-calculated route data (saved at booking)
                    'expected_route_polyline': ride.expected_route_polyline,
                    'route_duration_minutes': ride.route_duration_minutes,
                    'route_steps': ride.route_steps,
                }
            })
        
        logger.info(f"[IncomingRide] No suitable rides found for driver {driver.id}")
        return Response({'ride': None})


class DriverActiveRideView(APIView):
    """GET /api/driver/active-ride - get driver's currently active ride (accepted through payment_required)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from rides.models import Ride
        from drivers.models import Driver
        
        try:
            driver = Driver.objects.get(user=request.user)
        except Driver.DoesNotExist:
            return Response({'ride': None})
        
        active_cutoff = timezone.now() - datetime.timedelta(hours=12)
        # Get the driver's current active ride
        active_ride = Ride.objects.filter(
            driver=driver,
            status__in=[
                Ride.STATUS_DRIVER_ASSIGNED,
                Ride.STATUS_DRIVER_ARRIVING,
                Ride.STATUS_ARRIVED,
                Ride.STATUS_OTP_VERIFIED,
                Ride.STATUS_STARTED,
                Ride.STATUS_REACHED_DESTINATION,
                Ride.STATUS_PAYMENT_REQUIRED,
            ]
        ).filter(
            requested_at__gte=active_cutoff
        ).order_by('-requested_at').first()
        
        if not active_ride:
            return Response({'ride': None})
        
        return Response({
            'ride': {
                'id': str(active_ride.id),
                'status': map_ride_status(active_ride.status),
                'status_raw': active_ride.status,
                'pickup': {
                    'address': active_ride.pickup_address or '',
                    'lat': active_ride.pickup_lat,
                    'lng': active_ride.pickup_lng,
                },
                'drop': {
                    'address': active_ride.drop_address or '',
                    'lat': active_ride.drop_lat,
                    'lng': active_ride.drop_lng,
                },
                'vehicle': active_ride.vehicle_type,
                'fare': {
                    'total': float(active_ride.final_fare or active_ride.estimated_fare or 0),
                },
                'paymentMethod': map_payment_method(active_ride.payment_method),
                'paymentStatus': map_payment_status(active_ride.payment_status),
                'paymentMessage': map_payment_message(active_ride.payment_status),
                'code': active_ride.otp[:4] if active_ride.otp else '0000',
                'createdAt': active_ride.requested_at.isoformat() if active_ride.requested_at else '',
                'passenger': {
                    'name': active_ride.passenger.name or 'Passenger',
                    'phone': active_ride.passenger.phone_number or '',
                } if active_ride.passenger else None,
                # Pre-calculated route data (saved at booking/acceptance)
                'expected_route_polyline': active_ride.expected_route_polyline,
                'driver_to_pickup_polyline': active_ride.driver_to_pickup_polyline,
                'route_duration_minutes': active_ride.route_duration_minutes,
                'route_steps': active_ride.route_steps,
                'driver_to_pickup_distance_km': active_ride.driver_to_pickup_distance_km,
                'driver_to_pickup_duration_minutes': active_ride.driver_to_pickup_duration_minutes,
            }
        })


class PassengerActiveRideView(APIView):
    """GET /api/passenger/active-ride - get passenger's currently active ride"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from rides.models import Ride
        from drivers.models import Driver
        
        user = request.user
        if user.role != 'passenger':
            return Response({'ride': None})
        
        # Find active ride for this passenger
        active_statuses = [
            Ride.STATUS_REQUESTED,
            Ride.STATUS_SEARCHING,
            Ride.STATUS_DRIVER_ASSIGNED,
            Ride.STATUS_ARRIVED,
            Ride.STATUS_OTP_VERIFIED,
            Ride.STATUS_STARTED,
            Ride.STATUS_REACHED_DESTINATION,
            Ride.STATUS_PAYMENT_REQUIRED,
            Ride.STATUS_PAYMENT_CONFIRMED,
        ]
        
        try:
            active_ride = Ride.objects.filter(
                passenger=user,
                status__in=active_statuses
            ).select_related('driver').first()
        except Ride.DoesNotExist:
            active_ride = None
        
        if not active_ride:
            return Response({'ride': None})
        
        # Build ride data with full details
        ride_data = {
            'id': active_ride.id,
            'pickup_lat': active_ride.pickup_lat,
            'pickup_lng': active_ride.pickup_lng,
            'pickup_address': active_ride.pickup_address,
            'drop_lat': active_ride.drop_lat,
            'drop_lng': active_ride.drop_lng,
            'drop_address': active_ride.drop_address,
            'status': map_ride_status(active_ride.status),
            'vehicle': active_ride.vehicle_type,
            'fare': float(active_ride.final_fare) if active_ride.final_fare else float(active_ride.estimated_fare) if active_ride.estimated_fare else 0,
            'payment_status': map_payment_status(active_ride.payment_status),
            'payment_method': map_payment_method(active_ride.payment_method),
            'polyline': active_ride.route_polyline,
            'driver_to_pickup_polyline': active_ride.driver_to_pickup_polyline,
            'driver': None,
        }
        
        # Add driver info if assigned
        if active_ride.driver:
            ride_data['driver'] = {
                'id': active_ride.driver.id,
                'name': active_ride.driver.name,
                'phone': active_ride.driver.phone,
                'vehicle_type': active_ride.driver.vehicle_type,
                'vehicle_number': active_ride.driver.vehicle_number,
                'current_lat': active_ride.driver.current_lat,
                'current_lng': active_ride.driver.current_lng,
            }
        
        return Response({'ride': ride_data})


class RideHistoryFrontendView(APIView):
    """GET /api/rides/history - get ride history for passenger or driver"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from rides.models import Ride
        from drivers.models import Driver
        
        # Check if user is a driver
        try:
            driver = Driver.objects.get(user=request.user)
            # Get rides where this user was the driver
            rides = Ride.objects.filter(driver=driver).order_by('-requested_at')[:20]
            is_driver = True
        except Driver.DoesNotExist:
            # Get rides where this user was the passenger
            rides = Ride.objects.filter(passenger=request.user).order_by('-requested_at')[:20]
            is_driver = False
        
        ride_list = []
        for ride in rides:
            ride_data = {
                'id': str(ride.id),
                'status': map_ride_status(ride.status),
                'pickup': {
                    'address': ride.pickup_address or '',
                    'lat': ride.pickup_lat,
                    'lng': ride.pickup_lng
                },
                'drop': {
                    'address': ride.drop_address or '',
                    'lat': ride.drop_lat,
                    'lng': ride.drop_lng
                },
                'vehicle': ride.vehicle_type,
                'fare': {
                    'base': 0,
                    'tax': 0,
                    'perKm': 0,
                    'total': float(ride.final_fare or ride.estimated_fare or 0),
                    'beforeDiscount': float(ride.fare_before_discount or ride.estimated_fare or 0),
                    'discount': float(ride.promo_discount_amount or 0),
                    'promoCode': ride.promo_code_snapshot or None,
                },
                'code': ride.otp[:4] if ride.otp else '0000',
                'createdAt': ride.requested_at.isoformat() if ride.requested_at else ''
            }
            
            # Include driver info for passenger view
            if not is_driver and ride.driver:
                ride_data['driver'] = {
                    'name': ride.driver.name,
                    'phone': ride.driver.user.phone_number if ride.driver.user else '',
                    'plate': ride.driver.vehicle_number or '',
                }
            else:
                ride_data['driver'] = None
                
            ride_list.append(ride_data)
        
        return Response({'rides': ride_list})
    
# URL patterns for /api/
urlpatterns = [
    # Root redirect
    path('', RedirectView.as_view(url='/api/v1/core/', permanent=False)),
    
    # Auth endpoints (frontend format)
    # Note: Explicit trailing slash patterns to avoid 301 redirects
    path('auth/send-otp/', SendOTPView.as_view(), name='api_send_otp'),
    path('auth/send-otp', SendOTPView.as_view(), name='api_send_otp_no_slash'),
    path('auth/verify-otp/', VerifyOTPFrontendView.as_view(), name='api_verify_otp'),
    path('auth/verify-otp', VerifyOTPFrontendView.as_view(), name='api_verify_otp_no_slash'),
    re_path(r'^auth/passenger/signup/?$', PassengerSignupView.as_view(), name='api_passenger_signup'),
    re_path(r'^auth/rider/signup/?$', RiderSignupView.as_view(), name='api_rider_signup'),
    re_path(r'^auth/logout/?$', LogoutView.as_view(), name='api_logout'),
    # Check phone existence before OTP
    re_path(r'^auth/check-phone/?$', CheckPhoneView.as_view(), name='api_check_phone'),
    # Firebase OTP verify (new Firebase phone auth flow)
    path('auth/verify-firebase/', VerifyFirebaseView.as_view(), name='api_verify_firebase'),
    path('auth/verify-firebase', VerifyFirebaseView.as_view(), name='api_verify_firebase_no_slash'),
    
    # User profile
    re_path(r'^me/?$', MeView.as_view(), name='api_me'),
    re_path(r'^me/language/?$', SetLanguageView.as_view(), name='api_set_language'),
    
    # Places
    re_path(r'^places/saved/?$', PlacesSavedView.as_view(), name='api_saved_places'),
    re_path(r'^places/saved/(?P<place_id>[0-9]+)/?$', PlacesSavedDetailView.as_view(), name='api_saved_place_detail'),
    
    # Rides
    re_path(r'^rides/quote/?$', QuoteView.as_view(), name='api_ride_quote'),
    re_path(r'^rides/request/?$', RequestRideFrontendView.as_view(), name='api_request_ride'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/?$', RideDetailView.as_view(), name='api_ride_detail'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/cancel/?$', CancelRideFrontendView.as_view(), name='api_cancel_ride'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/accept/?$', AcceptRideFrontendView.as_view(), name='api_accept_ride'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/reject/?$', RejectRideFrontendView.as_view(), name='api_reject_ride'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/arrive/?$', ArrivedPickupView.as_view(), name='api_arrive_pickup'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/start/?$', StartRideFrontendView.as_view(), name='api_start_ride'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/complete/?$', CompleteRideFrontendView.as_view(), name='api_complete_ride'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/reached-destination/?$', ReachedDestinationView.as_view(), name='api_reached_destination'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/rate/?$', SubmitRatingActionView.as_view(), name='api_submit_rating'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/chat/messages/?$', ChatMessagesView.as_view(), name='api_chat_messages'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/chat/send/?$', SendMessageView.as_view(), name='api_chat_send'),
    re_path(r'^rides/(?P<ride_id>[0-9]+)/chat/mark-read/?$', MarkReadView.as_view(), name='api_chat_mark_read'),
    re_path(r'^rides/history/?$', RideHistoryFrontendView.as_view(), name='api_ride_history'),
    re_path(r'^rides/notifications/?$', NotificationListView.as_view(), name='api_ride_notifications'),
    re_path(r'^rides/notifications/mark-read/?$', NotificationMarkReadView.as_view(), name='api_ride_notifications_mark_read'),
    
    # Support Tickets
    re_path(r'^rides/support/topics/?$', TicketTopicsView.as_view(), name='api_support_topics'),
    re_path(r'^rides/support/user-rides/?$', UserRidesForTicketView.as_view(), name='api_support_user_rides'),
    re_path(r'^rides/support/tickets/?$', SupportTicketListView.as_view(), name='api_support_tickets'),
    re_path(r'^rides/support/tickets/create/?$', CreateSupportTicketView.as_view(), name='api_support_ticket_create'),
    re_path(r'^rides/admin/support/tickets/?$', AdminSupportTicketListView.as_view(), name='api_admin_support_tickets'),
    re_path(r'^rides/admin/support/tickets/(?P<ticket_id>[0-9]+)/respond/?$', AdminTicketResponseView.as_view(), name='api_admin_support_ticket_respond'),
    
    # Driver
    re_path(r'^driver/online/?$', DriverOnlineView.as_view(), name='api_driver_online'),
    re_path(r'^driver/status/?$', DriverStatusView.as_view(), name='api_driver_status'),
    re_path(r'^driver/stats/?$', DriverStatsView.as_view(), name='api_driver_stats'),
    re_path(r'^driver/incoming/?$', IncomingRideView.as_view(), name='api_driver_incoming'),
    re_path(r'^driver/active-ride/?$', DriverActiveRideView.as_view(), name='api_driver_active_ride'),
    re_path(r'^passenger/active-ride/?$', PassengerActiveRideView.as_view(), name='api_passenger_active_ride'),
    re_path(r'^driver/verification/status/?$', DriverVerificationStatusView.as_view(), name='api_driver_verification'),
    re_path(r'^driver/earnings/?$', EarningsView.as_view(), name='api_driver_earnings'),
    re_path(r'^driver/wallet/withdraw/?$', WalletWithdrawView.as_view(), name='api_wallet_withdraw'),
    re_path(r'^driver/location/?$', UpdateLocationView.as_view(), name='api_driver_location'),
    re_path(r'^drivers/location/?$', UpdateLocationView.as_view(), name='api_drivers_location'),
    re_path(r'^driver/rides/(?P<ride_id>[0-9]+)/accept/?$', AcceptRideFrontendView.as_view(), name='api_accept_ride'),
    re_path(r'^driver/rides/(?P<ride_id>[0-9]+)/reject/?$', RejectRideFrontendView.as_view(), name='api_reject_ride'),
    re_path(r'^driver/rides/(?P<ride_id>[0-9]+)/arrived/?$', ArrivedPickupView.as_view(), name='api_arrived_pickup'),
    re_path(r'^driver/rides/(?P<ride_id>[0-9]+)/start/?$', StartRideFrontendView.as_view(), name='api_start_ride'),
    re_path(r'^driver/rides/(?P<ride_id>[0-9]+)/complete/?$', CompleteRideFrontendView.as_view(), name='api_complete_ride'),
    re_path(r'^driver/rides/(?P<ride_id>[0-9]+)/collect/?$', CollectPaymentView.as_view(), name='api_collect_payment'),
    
    # Maps
    re_path(r'^maps/autocomplete/?$', MapsAutocompleteView.as_view(), name='api_maps_autocomplete'),
    re_path(r'^maps/reverse/?$', MapsReverseView.as_view(), name='api_maps_reverse'),
    re_path(r'^maps/directions/?$', MapsDirectionsView.as_view(), name='api_maps_directions'),
    
    # Promos
    re_path(r'^promos/validate/?$', PromoValidateView.as_view(), name='api_promo_validate'),
    
    # Nearby drivers
    re_path(r'^drivers/nearby/?$', NearbyDriversFrontendView.as_view(), name='api_nearby_drivers'),
    
    # Payments
    re_path(r'^payments/razorpay/order/?$', RazorpayOrderView.as_view(), name='api_razorpay_order'),
    re_path(r'^payments/status/?$', PaymentStatusView.as_view(), name='api_payment_status'),
    re_path(r'^payments/cash/code/?$', CashCodeView.as_view(), name='api_cash_code'),
    re_path(r'^payments/webhook/?$', WebhookView.as_view(), name='api_payments_webhook'),
    re_path(r'^payments/razorpay/webhook/?$', WebhookView.as_view(), name='api_razorpay_webhook'),
    # NEW: Payment confirmation and online payment endpoints
    re_path(r'^payments/confirm-cash-collection/?$', ConfirmCashCollectionView.as_view(), name='api_confirm_cash_collection'),
    re_path(r'^payments/initiate-online/?$', InitiateOnlinePaymentView.as_view(), name='api_initiate_online'),
    re_path(r'^payments/verify-online/?$', VerifyOnlinePaymentView.as_view(), name='api_verify_online'),
    re_path(r'^payments/status/(?P<ride_id>[0-9]+)/?$', PaymentStatusCheckView.as_view(), name='api_payment_status_check'),
    re_path(r'^payments/set-method/?$', SetPaymentMethodView.as_view(), name='api_set_payment_method'),
    
    # Legacy v1 API (keep for backward compatibility)
    path('v1/?', include('rideapp.legacy_urls')),
]
