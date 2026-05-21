"""
Ride State Machine Service
Enforces strict state transitions and manages ride lifecycle.
"""
import logging
from typing import Optional, Tuple
from django.utils import timezone
from django.db import transaction

from rides.models import Ride
from rideapp.redis_utils import safe_cache_set
from rides.services.ride_cache_service import RideCacheService

logger = logging.getLogger('rides.state_machine')


class RideStateMachine:
    """
    Enforces strict state transitions for rides.

    Canonical flow:
    REQUESTED -> SEARCHING_DRIVER -> DRIVER_ASSIGNED -> DRIVER_ARRIVING
    -> ARRIVED -> OTP_VERIFIED -> STARTED -> REACHED_DESTINATION
    -> PAYMENT_REQUIRED -> PAYMENT_CONFIRMED -> COMPLETED
    """

    STATE_LABELS = {
        Ride.STATUS_REQUESTED: 'Ride Requested',
        Ride.STATUS_SEARCHING_DRIVER: 'Searching for Driver',
        Ride.STATUS_DRIVER_ASSIGNED: 'Driver Assigned',
        Ride.STATUS_DRIVER_ARRIVING: 'Driver En Route',
        Ride.STATUS_ARRIVED: 'Driver Arrived',
        Ride.STATUS_OTP_VERIFIED: 'OTP Verified',
        Ride.STATUS_STARTED: 'Ride in Progress',
        Ride.STATUS_REACHED_DESTINATION: 'Reached Destination',
        Ride.STATUS_PAYMENT_REQUIRED: 'Payment Required',
        Ride.STATUS_PAYMENT_CONFIRMED: 'Payment Confirmed',
        Ride.STATUS_COMPLETED: 'Ride Completed',
        Ride.STATUS_CANCELLED: 'Ride Cancelled',
    }

    STATUS_MAP = {
        'requested': 'STATUS_REQUESTED',
        'searching': 'STATUS_SEARCHING_DRIVER',
        'searching_driver': 'STATUS_SEARCHING_DRIVER',
        'driver_assigned': 'STATUS_DRIVER_ASSIGNED',
        'accepted': 'STATUS_DRIVER_ASSIGNED',
        'driver_arriving': 'STATUS_DRIVER_ARRIVING',
        'driver_arrived': 'STATUS_ARRIVED',
        'arrived': 'STATUS_ARRIVED',
        'otp_verified': 'STATUS_OTP_VERIFIED',
        'started': 'STATUS_STARTED',
        'ride_started': 'STATUS_STARTED',
        'reached_destination': 'STATUS_REACHED_DESTINATION',
        'payment_required': 'STATUS_PAYMENT_REQUIRED',
        'payment_confirmed': 'STATUS_PAYMENT_CONFIRMED',
        'completed': 'STATUS_COMPLETED',
        'cancelled': 'STATUS_CANCELLED',
    }

    KEY_TO_VALUE = {
        'STATUS_REQUESTED': Ride.STATUS_REQUESTED,
        'STATUS_SEARCHING_DRIVER': Ride.STATUS_SEARCHING_DRIVER,
        'STATUS_DRIVER_ASSIGNED': Ride.STATUS_DRIVER_ASSIGNED,
        'STATUS_DRIVER_ARRIVING': Ride.STATUS_DRIVER_ARRIVING,
        'STATUS_ARRIVED': Ride.STATUS_ARRIVED,
        'STATUS_OTP_VERIFIED': Ride.STATUS_OTP_VERIFIED,
        'STATUS_STARTED': Ride.STATUS_STARTED,
        'STATUS_REACHED_DESTINATION': Ride.STATUS_REACHED_DESTINATION,
        'STATUS_PAYMENT_REQUIRED': Ride.STATUS_PAYMENT_REQUIRED,
        'STATUS_PAYMENT_CONFIRMED': Ride.STATUS_PAYMENT_CONFIRMED,
        'STATUS_COMPLETED': Ride.STATUS_COMPLETED,
        'STATUS_CANCELLED': Ride.STATUS_CANCELLED,
    }

    PUBLIC_STATUS_MAP = {
        Ride.STATUS_REQUESTED: 'requested',
        Ride.STATUS_SEARCHING_DRIVER: 'searching',
        Ride.STATUS_DRIVER_ASSIGNED: 'accepted',
        Ride.STATUS_DRIVER_ARRIVING: 'driver_arriving',
        Ride.STATUS_ARRIVED: 'driver_arrived',
        Ride.STATUS_OTP_VERIFIED: 'otp_verified',
        Ride.STATUS_STARTED: 'started',
        Ride.STATUS_REACHED_DESTINATION: 'reached_destination',
        Ride.STATUS_PAYMENT_REQUIRED: 'payment_required',
        Ride.STATUS_PAYMENT_CONFIRMED: 'payment_confirmed',
        Ride.STATUS_COMPLETED: 'completed',
        Ride.STATUS_CANCELLED: 'cancelled',
    }

    @classmethod
    def _to_status_key(cls, status_value: str) -> str:
        raw = str(status_value or '').strip()
        lowered = raw.lower()
        if lowered in cls.STATUS_MAP:
            return cls.STATUS_MAP[lowered]
        if raw.startswith('STATUS_'):
            return raw
        return f"STATUS_{raw.upper()}"

    @classmethod
    def _to_status_value(cls, status_key: str) -> str:
        return cls.KEY_TO_VALUE.get(status_key, status_key.replace('STATUS_', ''))

    @classmethod
    def to_public_status(cls, status_value: str) -> str:
        return cls.PUBLIC_STATUS_MAP.get(status_value, (status_value or '').lower())

    @classmethod
    def can_transition(cls, ride: Ride, new_status: str) -> Tuple[bool, str]:
        current_status = ride.status
        current_key = cls._to_status_key(current_status)
        new_key = cls._to_status_key(new_status)

        # Allow cancellation from any non-terminal state.
        if new_key == 'STATUS_CANCELLED':
            if current_key in ['STATUS_COMPLETED', 'STATUS_CANCELLED']:
                return False, "Cannot cancel a completed/cancelled ride"
            return True, "Cancellation allowed"

        valid_next = Ride.VALID_TRANSITIONS.get(current_key, [])
        if new_key not in valid_next:
            return False, f"Invalid transition: {current_status} -> {new_status}"

        return True, "Transition valid"

    @classmethod
    @transaction.atomic
    def transition(
        cls,
        ride_id: int,
        new_status: str,
        actor_type: str = 'system',  # 'system', 'driver', 'passenger'
        actor_id: Optional[int] = None,
        metadata: Optional[dict] = None
    ) -> Tuple[bool, str, Optional[Ride]]:
        logger.info(
            f"[STATE_MACHINE] Transition requested: ride={ride_id}, new_status={new_status}, actor={actor_type}({actor_id})"
        )

        try:
            ride = Ride.objects.select_for_update().get(id=ride_id)
        except Ride.DoesNotExist:
            logger.error(f"[STATE_MACHINE] Ride {ride_id} not found")
            return False, "Ride not found", None

        old_status = ride.status
        new_status_key = cls._to_status_key(new_status)
        target_status = cls._to_status_value(new_status_key)

        logger.info(f"[STATE_MACHINE] Current status for ride {ride_id}: {old_status}")

        allowed, message = cls.can_transition(ride, target_status)
        if not allowed:
            logger.warning(f"[STATE_MACHINE] Transition REJECTED: {old_status} -> {target_status}: {message}")
            return False, message, ride

        logger.info(f"[STATE_MACHINE] Transition ALLOWED: {old_status} -> {target_status}")

        try:
            if target_status == Ride.STATUS_SEARCHING_DRIVER:
                cls._handle_searching_driver(ride, metadata)
            elif target_status == Ride.STATUS_DRIVER_ASSIGNED:
                cls._handle_driver_assigned(ride, metadata)
            elif target_status == Ride.STATUS_ARRIVED:
                cls._handle_arrival(ride, metadata)
            elif target_status == Ride.STATUS_OTP_VERIFIED:
                cls._handle_otp_verified(ride, metadata)
            elif target_status == Ride.STATUS_STARTED:
                cls._handle_start(ride, metadata)
            elif target_status == Ride.STATUS_REACHED_DESTINATION:
                cls._handle_reached_destination(ride, metadata)
            elif target_status == Ride.STATUS_COMPLETED:
                success, msg = cls._handle_completion(ride, metadata)
                if not success:
                    logger.error(f"[STATE_MACHINE] Completion failed for ride {ride_id}: {msg}")
                    return False, msg, ride
            elif target_status == Ride.STATUS_CANCELLED:
                cls._handle_cancellation(ride, actor_type, actor_id, metadata)

            ride.status = target_status
            ride.save(update_fields=['status'])
            RideCacheService.invalidate(ride.id, "status_change")
            logger.info("[STATE UPDATE] ride=%s %s->%s", ride_id, old_status, target_status)

            safe_cache_set(
                f'ride:{ride_id}:status',
                {'status': target_status, 'status_key': new_status_key, 'timestamp': timezone.now().isoformat()},
                timeout=300
            )

            # Canonical realtime update for clients.
            try:
                from asgiref.sync import async_to_sync
                from channels.layers import get_channel_layer
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f'ride_{ride_id}',
                    {
                        'type': 'ride_update',
                        'ride_id': ride_id,
                        'status': cls.to_public_status(target_status),
                        'status_raw': target_status,
                        'previous_status': cls.to_public_status(old_status) if old_status else None,
                        'previous_status_raw': old_status,
                    }
                )
            except Exception as ws_err:
                logger.warning(f"[STATE_MACHINE] ride_update broadcast failed for ride {ride_id}: {ws_err}")

            logger.info(f"Ride {ride_id} state: {old_status} -> {target_status} by {actor_type}({actor_id})")
            return True, f"Transition successful: {old_status} -> {target_status}", ride

        except Exception as e:
            logger.exception(f"Error during state transition for ride {ride_id}: {e}")
            return False, f"Transition failed: {str(e)}", ride

    @classmethod
    def _handle_searching_driver(cls, ride: Ride, metadata: Optional[dict]):
        ride.driver = None
        if metadata:
            if 'driver_to_pickup_polyline' in metadata:
                ride.driver_to_pickup_polyline = metadata['driver_to_pickup_polyline']
            if 'driver_to_pickup_distance_km' in metadata:
                ride.driver_to_pickup_distance_km = metadata['driver_to_pickup_distance_km']
            if 'driver_to_pickup_duration_minutes' in metadata:
                ride.driver_to_pickup_duration_minutes = metadata['driver_to_pickup_duration_minutes']
        ride.save(update_fields=['driver', 'driver_to_pickup_polyline', 'driver_to_pickup_distance_km', 'driver_to_pickup_duration_minutes'])
        logger.info(f"Ride {ride.id}: Reset to searching state")

    @classmethod
    def _handle_driver_assigned(cls, ride: Ride, metadata: Optional[dict]):
        if metadata and 'driver_id' in metadata:
            ride.driver_id = metadata['driver_id']
            ride.driver_assigned_at = timezone.now()
            ride.save(update_fields=['driver', 'driver_assigned_at'])
            logger.info(f"Ride {ride.id}: Driver {ride.driver_id} assigned")

    @classmethod
    def _handle_arrival(cls, ride: Ride, metadata: Optional[dict]):
        ride.arrival_time = timezone.now()
        ride.save(update_fields=['arrival_time'])
        safe_cache_set(
            f'ride:{ride.id}:waiting_start',
            ride.arrival_time.isoformat(),
            timeout=3600
        )
        logger.info(f"Ride {ride.id}: Driver arrived at pickup, waiting timer started")

    @classmethod
    def _handle_otp_verified(cls, ride: Ride, metadata: Optional[dict]):
        from rides.services.billing_service import BillingService
        if ride.arrival_time:
            waiting_seconds = int((timezone.now() - ride.arrival_time).total_seconds())
            ride.waiting_time_seconds = waiting_seconds
            ride.waiting_charge = BillingService.calculate_waiting_charge(waiting_seconds)
            ride.waiting_charge_locked = True
            ride.start_time = timezone.now()
            ride.save(update_fields=[
                'waiting_time_seconds', 'waiting_charge',
                'waiting_charge_locked', 'start_time'
            ])

    @classmethod
    def _handle_start(cls, ride: Ride, metadata: Optional[dict]):
        if not ride.start_time:
            ride.start_time = timezone.now()
            ride.save(update_fields=['start_time'])

    @classmethod
    def _handle_reached_destination(cls, ride: Ride, metadata: Optional[dict]):
        ride.end_time = timezone.now()
        # Move to explicit payment-required stage as soon as destination is reached.
        ride.payment_status = Ride.PAYMENT_PENDING
        ride.save(update_fields=['end_time', 'payment_status'])

    @classmethod
    def _handle_completion(cls, ride: Ride, metadata: Optional[dict]) -> Tuple[bool, str]:
        from rides.services.billing_service import BillingService

        if ride.payment_status != Ride.PAYMENT_SUCCESS:
            return False, "Cannot complete ride before payment is confirmed"

        ride.end_time = timezone.now()
        fare_breakdown = BillingService.calculate_final_fare(ride)

        ride.base_fare = fare_breakdown['base_fare']
        ride.distance_fare = fare_breakdown['distance_fare']
        ride.time_fare = fare_breakdown['time_fare']
        ride.waiting_charge = fare_breakdown['waiting_charge']
        ride.platform_commission = fare_breakdown['platform_commission']
        ride.driver_share = fare_breakdown['driver_share']
        ride.final_fare = fare_breakdown['total']
        ride.completed_at = timezone.now()
        ride.save(update_fields=[
            'end_time', 'base_fare', 'distance_fare', 'time_fare',
            'waiting_charge', 'platform_commission', 'driver_share',
            'final_fare', 'completed_at'
        ])

        if ride.driver:
            ride.driver.total_rides += 1
            ride.driver.save(update_fields=['total_rides'])
        return True, "Completion successful"

    @classmethod
    def _handle_cancellation(
        cls,
        ride: Ride,
        actor_type: str,
        actor_id: Optional[int],
        metadata: Optional[dict]
    ):
        cancelled_by = metadata.get('cancelled_by', actor_type) if metadata else actor_type
        reason = metadata.get('reason', '') if metadata else ''
        if ride.driver:
            from drivers.state_machine import DriverStateMachine
            DriverStateMachine.mark_available(ride.driver.id)
        logger.info(f"Ride {ride.id}: Cancelled by {cancelled_by}. Reason: {reason}.")

    @classmethod
    def get_current_waiting_time(cls, ride_id: int) -> dict:
        try:
            ride = Ride.objects.get(id=ride_id)
            if ride.status not in [Ride.STATUS_ARRIVED, Ride.STATUS_OTP_VERIFIED]:
                return {'error': 'Ride not in waiting state'}
            if not ride.arrival_time:
                return {'error': 'Arrival time not recorded'}
            if ride.waiting_charge_locked:
                return {
                    'waiting_time_seconds': ride.waiting_time_seconds,
                    'waiting_charge': float(ride.waiting_charge),
                    'locked': True,
                    'free_seconds': 120,
                    'chargeable_seconds': max(0, ride.waiting_time_seconds - 120)
                }

            current_seconds = int((timezone.now() - ride.arrival_time).total_seconds())
            from rides.services.billing_service import BillingService
            current_charge = BillingService.calculate_waiting_charge(current_seconds)
            return {
                'waiting_time_seconds': current_seconds,
                'waiting_charge': float(current_charge),
                'locked': False,
                'free_seconds': min(120, current_seconds),
                'chargeable_seconds': max(0, current_seconds - 120),
                'charge_per_minute': 3,
                'message': 'First 2 minutes free, Rs3/min after' if current_seconds > 120 else 'Free waiting'
            }
        except Ride.DoesNotExist:
            return {'error': 'Ride not found'}

    @classmethod
    def get_ride_summary(cls, ride_id: int) -> dict:
        try:
            ride = Ride.objects.select_related('passenger', 'driver').get(id=ride_id)
            return {
                'ride_id': ride.id,
                'status': ride.status,
                'status_label': cls.STATE_LABELS.get(ride.status, ride.status),
                'passenger_id': ride.passenger_id,
                'driver_id': ride.driver_id,
                'driver_name': ride.driver.name if ride.driver else None,
                'pickup_address': ride.pickup_address,
                'drop_address': ride.drop_address,
                'vehicle_type': ride.vehicle_type,
                'estimated_fare': float(ride.estimated_fare) if ride.estimated_fare else None,
                'final_fare': float(ride.final_fare) if ride.final_fare else None,
                'payment_method': ride.payment_method,
                'payment_status': ride.payment_status,
                'can_cancel': ride.status not in [
                    Ride.STATUS_COMPLETED, Ride.STATUS_CANCELLED, Ride.STATUS_STARTED,
                    Ride.STATUS_REACHED_DESTINATION, Ride.STATUS_PAYMENT_REQUIRED,
                    Ride.STATUS_PAYMENT_CONFIRMED
                ],
                'can_complete': ride.status == Ride.STATUS_PAYMENT_CONFIRMED,
                'waiting_info': cls.get_current_waiting_time(ride_id) if ride.status in [
                    Ride.STATUS_ARRIVED, Ride.STATUS_OTP_VERIFIED
                ] else None
            }
        except Ride.DoesNotExist:
            return None
