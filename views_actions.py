"""
Ride Action Views
API endpoints for driver actions: arrival, OTP verification, completion, payment.
"""
import logging
from django.utils import timezone
from django.db import connection, transaction
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from rides.models import Ride, Rating, Feedback
from rides.services.state_machine import RideStateMachine
from rides.services.billing_service import BillingService
from rides.services.matching_engine import DriverMatchingEngine
from rides.services.notification_service import NotificationService
from rides.services.notification_center import NotificationCenter
from rides.models import Notification
from drivers.models import Driver
from rides.services.payment_service import PaymentService

logger = logging.getLogger('rides.actions')


class DriverArriveView(APIView):
    """
    POST /api/rides/{ride_id}/arrive/
    Driver taps "ARRIVED" at pickup location.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        user = request.user
        
        # Verify user is a driver
        if not hasattr(user, 'driver_profile'):
            return Response(
                {'success': False, 'message': 'Only drivers can call this endpoint'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        driver = user.driver_profile
        
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify this driver is assigned to this ride
        if ride.driver_id != driver.id:
            return Response(
                {'success': False, 'message': 'You are not assigned to this ride'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Perform state transition
        success, message, updated_ride = RideStateMachine.transition(
            ride_id=ride_id,
            new_status=Ride.STATUS_ARRIVED,
            actor_type='driver',
            actor_id=driver.id
        )
        
        if not success:
            return Response(
                {'success': False, 'message': message},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get waiting info for the response
        waiting_info = BillingService.get_waiting_stopwatch_data(ride_id)
        
        # Send canonical persisted notification.
        try:
            NotificationCenter.create_and_broadcast(
                updated_ride.passenger,
                Notification.TYPE_DRIVER_ARRIVED,
                "Your driver has arrived.",
                data={"ride_id": ride_id},
            )
        except Exception as e:
            logger.error(f"Error sending arrival notification: {e}")
        
        # Serialize the complete ride for frontend
        from rides.serializers import RideStatusSerializer
        serializer = RideStatusSerializer(updated_ride)
        
        return Response({
            'success': True,
            'message': 'Arrival confirmed. Waiting timer started.',
            'ride': serializer.data,
            'waiting_info': {
                'message': 'First 2 minutes free, ₹3/min after',
                'free_seconds': 120,
                'charge_per_minute': 3
            }
        })


class VerifyOtpView(APIView):
    """
    POST /api/rides/{ride_id}/verify-otp/
    Driver enters OTP from passenger.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        user = request.user
        
        if not hasattr(user, 'driver_profile'):
            return Response(
                {'success': False, 'message': 'Only drivers can call this endpoint'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        driver = user.driver_profile
        otp_code = request.data.get('otp', '').strip()
        
        if not otp_code:
            return Response(
                {'success': False, 'message': 'OTP is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify driver is assigned
        if ride.driver_id != driver.id:
            return Response(
                {'success': False, 'message': 'You are not assigned to this ride'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Verify OTP
        ride_status = (ride.status or "").upper()
        if ride_status in {Ride.STATUS_COMPLETED, Ride.STATUS_CANCELLED}:
            return Response(
                {'success': False, 'message': 'OTP cannot be verified for completed/cancelled rides'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if ride_status != Ride.STATUS_ARRIVED:
            return Response(
                {'success': False, 'message': f'OTP can only be verified when driver has arrived. Current status: {ride.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        otp_expiry_minutes = int(getattr(settings, 'RIDE_OTP_EXPIRY_MINUTES', 60))
        if ride.requested_at and (timezone.now() - ride.requested_at).total_seconds() > otp_expiry_minutes * 60:
            return Response(
                {'success': False, 'message': 'OTP has expired. Please cancel and rebook the ride.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if ride.otp != otp_code:
            return Response(
                {'success': False, 'message': 'Invalid OTP'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Perform state transition
        success, message, updated_ride = RideStateMachine.transition(
            ride_id=ride_id,
            new_status=Ride.STATUS_OTP_VERIFIED,
            actor_type='driver',
            actor_id=driver.id
        )
        
        if not success:
            return Response(
                {'success': False, 'message': message},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get waiting charge info
        waiting_breakdown = BillingService.get_waiting_time_breakdown(
            updated_ride.waiting_time_seconds
        )
        
        # Notify WebSocket
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()
            
            # Notify passenger
            async_to_sync(channel_layer.group_send)(
                f'ride_{ride_id}',
                {
                    'type': 'waiting_stopped',
                    'final_waiting_time': updated_ride.waiting_time_seconds,
                    'waiting_charge': float(updated_ride.waiting_charge)
                }
            )
            
            # Stop waiting time updates
            async_to_sync(channel_layer.group_send)(
                f'waiting_{ride_id}',
                {
                    'type': 'waiting_stopped',
                    'final_waiting_time': updated_ride.waiting_time_seconds,
                    'waiting_charge': float(updated_ride.waiting_charge)
                }
            )
        except Exception as e:
            logger.error(f"Error sending WebSocket notifications: {e}")
        
        # Send notifications
        try:
            NotificationService.notify_driver_otp_verified(driver, updated_ride)
        except Exception as e:
            logger.error(f"Error sending OTP notification: {e}")
        
        # Serialize the complete ride for frontend
        from rides.serializers import RideStatusSerializer
        serializer = RideStatusSerializer(updated_ride)
        
        return Response({
            'success': True,
            'message': 'OTP verified. Ride starting.',
            'ride': serializer.data,
            'waiting_time': waiting_breakdown,
            'next_step': 'Tap "Start Ride" to begin'
        })


class StartRideView(APIView):
    """
    POST /api/rides/{ride_id}/start/
    Driver confirms passenger picked up and ride starts.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        user = request.user
        
        if not hasattr(user, 'driver_profile'):
            return Response(
                {'success': False, 'message': 'Only drivers can call this endpoint'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        driver = user.driver_profile
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if ride.driver_id != driver.id:
            return Response(
                {'success': False, 'message': 'You are not assigned to this ride'},
                status=status.HTTP_403_FORBIDDEN
            )
            
        # Verify OTP if currently in ARRIVED state
        if ride.status == Ride.STATUS_ARRIVED:
            code = request.data.get('code', '').strip()
            if not code or ride.otp != code:
                return Response(
                    {'success': False, 'message': 'Invalid ride code. Please ask passenger for the correct 4-digit code.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Transition to OTP_VERIFIED first
            success, message, updated_ride = RideStateMachine.transition(
                ride_id=ride_id,
                new_status=Ride.STATUS_OTP_VERIFIED,
                actor_type='driver',
                actor_id=driver.id
            )
            if not success:
                return Response({'success': False, 'message': message}, status=status.HTTP_400_BAD_REQUEST)
        
        # Perform state transition to STARTED
        success, message, updated_ride = RideStateMachine.transition(
            ride_id=ride_id,
            new_status=Ride.STATUS_STARTED,
            actor_type='driver',
            actor_id=driver.id,
            metadata={}
        )
        
        if not success:
            return Response(
                {'success': False, 'message': message},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Send canonical persisted notification.
        try:
            NotificationCenter.create_and_broadcast(
                updated_ride.passenger,
                Notification.TYPE_RIDE_STARTED,
                "Your ride has started.",
                data={"ride_id": ride_id},
            )
        except Exception as e:
            logger.error(f"Error sending ride started notification: {e}")
        
        # Serialize the complete ride for frontend
        from rides.serializers import RideStatusSerializer
        serializer = RideStatusSerializer(updated_ride)
        
        return Response({
            'success': True,
            'message': 'Ride started',
            'ride': serializer.data,
            'payment': {
                'success': True,
                'method': (updated_ride.payment_method or '').upper() or None
            }
        })


class ReachedDestinationView(APIView):
    """
    POST /api/driver/rides/{ride_id}/reached-destination/
    Driver marks that they have reached the destination.
    Transitions ride to REACHED_DESTINATION status to trigger payment flow.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        user = request.user

        with transaction.atomic():
            try:
                ride = Ride.objects.select_for_update().get(id=ride_id)
            except Ride.DoesNotExist:
                return Response(
                    {'success': False, 'message': 'Ride not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Only the assigned driver can mark destination reached
            if not hasattr(user, 'driver_profile') or ride.driver_id != user.driver_profile.id:
                return Response(
                    {'success': False, 'message': 'Only the assigned driver can mark destination reached'},
                    status=status.HTTP_403_FORBIDDEN
                )

            driver = user.driver_profile

            # Idempotent retries: if payment stage already started/completed, return current state.
            if ride.status in [Ride.STATUS_PAYMENT_REQUIRED, Ride.STATUS_PAYMENT_CONFIRMED, Ride.STATUS_COMPLETED]:
                from rides.serializers import RideStatusSerializer
                serializer = RideStatusSerializer(ride)
                return Response({
                    'success': True,
                    'message': 'Destination status already processed.',
                    'ride': serializer.data,
                    'next_step': 'Collect payment from passenger'
                })

            # Move to reached_destination only when still in started stage.
            if ride.status == Ride.STATUS_STARTED:
                success, message, updated_ride = RideStateMachine.transition(
                    ride_id=ride_id,
                    new_status=Ride.STATUS_REACHED_DESTINATION,
                    actor_type='driver',
                    actor_id=driver.id
                )

                if not success:
                    return Response(
                        {'success': False, 'message': message},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            elif ride.status != Ride.STATUS_REACHED_DESTINATION:
                return Response(
                    {'success': False, 'message': f'Cannot mark destination from status: {ride.status}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            success, message, updated_ride = RideStateMachine.transition(
                ride_id=ride_id,
                new_status=Ride.STATUS_PAYMENT_REQUIRED,
                actor_type='system',
                actor_id=driver.id,
                metadata={'reason': 'destination_reached'}
            )

            if not success:
                return Response(
                    {'success': False, 'message': message},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Send canonical persisted notification.
        try:
            NotificationCenter.create_and_broadcast(
                updated_ride.passenger,
                "ride_updated",
                "Driver reached destination. Please complete payment.",
                data={"ride_id": ride_id},
            )
        except Exception as e:
            logger.error(f"Error sending destination reached notification: {e}")

        # Serialize the complete ride for frontend
        from rides.serializers import RideStatusSerializer
        serializer = RideStatusSerializer(updated_ride)
        
        return Response({
            'success': True,
            'message': 'Destination reached. Waiting for payment confirmation.',
            'ride': serializer.data,
            'next_step': 'Collect payment from passenger'
        })


class CompleteRideView(APIView):
    """
    POST /api/rides/{ride_id}/complete/
    Mark ride as completed.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        user = request.user

        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Only driver or system can complete
        if hasattr(user, 'driver_profile') and ride.driver_id == user.driver_profile.id:
            actor_type = 'driver'
            actor_id = user.driver_profile.id
        else:
            return Response(
                {'success': False, 'message': 'Only the assigned driver can complete this ride'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check payment status
        payment_status = PaymentService.get_payment_status(ride_id)
        if not payment_status.get('can_complete'):
            return Response({
                'success': False,
                'message': 'Cannot complete - payment not confirmed',
                'payment_status': payment_status
            }, status=status.HTTP_400_BAD_REQUEST)

        # Perform state transition
        success, message, updated_ride = RideStateMachine.transition(
            ride_id=ride_id,
            new_status=Ride.STATUS_COMPLETED,
            actor_type=actor_type,
            actor_id=actor_id
        )

        if not success:
            return Response(
                {'success': False, 'message': message},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get fare breakdown
        fare_breakdown = BillingService.calculate_final_fare(updated_ride)

        # Send canonical persisted notifications to passenger and driver.
        try:
            NotificationCenter.create_and_broadcast(
                updated_ride.passenger,
                Notification.TYPE_RIDE_COMPLETED,
                "Ride completed successfully.",
                data={"ride_id": ride_id},
            )
            if updated_ride.driver and updated_ride.driver.user:
                NotificationCenter.create_and_broadcast(
                    updated_ride.driver.user,
                    Notification.TYPE_RIDE_COMPLETED,
                    "Ride completed successfully.",
                    data={"ride_id": ride_id},
                )
        except Exception as e:
            logger.error(f"Error sending completion notifications: {e}")

        # Serialize the complete ride for frontend
        from rides.serializers import RideStatusSerializer
        serializer = RideStatusSerializer(updated_ride)
        
        return Response({
            'success': True,
            'message': 'Ride completed',
            'ride': serializer.data,
            'fare_breakdown': fare_breakdown,
            'rating_required': True
        })


class ConfirmCashCollectionView(APIView):
    """
    POST /api/rides/{ride_id}/confirm-cash/
    Driver confirms they received cash payment.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):
        user = request.user
        
        if not hasattr(user, 'driver_profile'):
            return Response(
                {'success': False, 'message': 'Only drivers can call this endpoint'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        driver = user.driver_profile
        
        result = PaymentService.confirm_cash_collection(ride_id, driver.id)
        
        if not result['success']:
            return Response(
                result,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Send cash collected notification to driver
        try:
            amount = result.get('amount_collected', 0)
            ride = Ride.objects.filter(id=ride_id).first()
            if ride:
                NotificationService.notify_driver_cash_collected(driver, ride, amount)
        except Exception as e:
            logger.error(f"Error sending cash collected notification: {e}")
        
        return Response(result)


class DriverRespondDispatchView(APIView):
    """
    POST /api/rides/dispatch-response/
    Driver responds to dispatch request (accept/reject).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        
        if not hasattr(user, 'driver_profile'):
            return Response(
                {'success': False, 'message': 'Only drivers can respond'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        driver = user.driver_profile
        request_id = request.data.get('request_id')
        accepted = request.data.get('accepted', False)
        
        if not request_id:
            return Response(
                {'success': False, 'message': 'Request ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        result = DriverMatchingEngine.handle_driver_response(
            request_id=request_id,
            driver_id=driver.id,
            accepted=accepted
        )
        
        if not result['success']:
            return Response(
                result,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        return Response(result)


class SubmitRatingView(APIView):
    """
    POST /api/rides/{ride_id}/rate/
    Submit post-ride rating.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, ride_id):

        user = request.user
        rating = request.data.get('rating')
        feedback = request.data.get('feedback', '')

        try:
            rating_value = int(rating)
        except (TypeError, ValueError):
            rating_value = None

        if rating_value is None or not (1 <= rating_value <= 5):
            return Response(
                {'success': False, 'message': 'Rating must be between 1 and 5'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            ride = Ride.objects.select_related('driver').get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify user is passenger or driver for this ride
        is_passenger = ride.passenger == user
        is_driver = hasattr(user, 'driver_profile') and ride.driver_id == user.driver_profile.id
        
        if not (is_passenger or is_driver):
            return Response(
                {'success': False, 'message': 'You are not authorized to rate this ride'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if ride.status != Ride.STATUS_COMPLETED:
            return Response(
                {'success': False, 'message': 'Rating is allowed only after ride completion'},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            rating_obj = Rating.objects.select_for_update().filter(ride=ride).first()
            if rating_obj is None:
                # Backward compatibility: some production DBs still enforce legacy NOT NULL
                # columns on ratings (score/from_user_id/to_user_id/is_passenger_rating_driver).
                # Create the row with those fields populated, then continue with canonical fields.
                counterpart_user_id = (
                    ride.driver.user_id if (is_passenger and ride.driver and ride.driver.user_id)
                    else ride.passenger_id
                )
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO ratings
                            (ride_id, passenger_id, driver_id,
                             score, feedback, is_passenger_rating_driver,
                             from_user_id, to_user_id,
                             created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        """,
                        [
                            ride.id,
                            ride.passenger_id,
                            ride.driver_id,
                            rating_value,
                            feedback or "",
                            bool(is_passenger),
                            user.id,
                            counterpart_user_id,
                        ],
                    )
                rating_obj = Rating.objects.select_for_update().get(ride=ride)
        
            if is_passenger:
                if rating_obj.passenger_rating is not None:
                    return Response(
                        {'success': False, 'message': 'Passenger rating already submitted'},
                        status=status.HTTP_409_CONFLICT
                    )
                rating_obj.passenger_rating = rating_value
                rating_obj.passenger_feedback = feedback
                rating_obj.passenger_rated_at = timezone.now()

                # Keep legacy Feedback table in sync so ratings/feedback are visible in admin reports.
                if ride.driver:
                    Feedback.objects.update_or_create(
                        ride=ride,
                        passenger=ride.passenger,
                        driver=ride.driver,
                        defaults={
                            'rating': rating_value,
                            'comment': feedback or "",
                        },
                    )
            
                # Update driver rating
                if ride.driver:
                    self._update_driver_rating(ride.driver)
                
                # Notify driver of rating received
                try:
                    NotificationService.notify_driver_rating_received(
                        ride.driver, rating_value, feedback
                    )
                except Exception as e:
                    logger.error(f"Error sending rating notification: {e}")
                
            elif is_driver:
                if rating_obj.driver_rating is not None:
                    return Response(
                        {'success': False, 'message': 'Driver rating already submitted'},
                        status=status.HTTP_409_CONFLICT
                    )
                rating_obj.driver_rating = rating_value
                rating_obj.driver_feedback = feedback
                rating_obj.driver_rated_at = timezone.now()

            rating_obj.save()
        
        return Response({
            'success': True,
            'message': 'Rating submitted successfully',
            'ride_id': ride_id,
            'rating_complete': rating_obj.is_complete()
        })
    
    def _update_driver_rating(self, driver: Driver):
        """Recalculate driver's average rating."""
        from django.db.models import Avg
        
        avg_rating = Rating.objects.filter(
            driver=driver,
            passenger_rating__isnull=False
        ).aggregate(avg=Avg('passenger_rating'))['avg']
        
        if avg_rating:
            driver.rating = round(avg_rating, 2)
            driver.save(update_fields=['rating'])


class RideStatusView(APIView):
    """
    GET /api/rides/{ride_id}/status/
    Get current ride status with waiting info.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, ride_id):
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Ride not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Verify access
        user = request.user
        is_passenger = ride.passenger == user
        is_driver = hasattr(user, 'driver_profile') and ride.driver_id == user.driver_profile.id
        
        if not (is_passenger or is_driver):
            return Response(
                {'success': False, 'message': 'Access denied'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        summary = RideStateMachine.get_ride_summary(ride_id)
        
        # Add payment status
        summary['payment'] = PaymentService.get_payment_status(ride_id)
        
        return Response({
            'success': True,
            'ride': summary
        })
