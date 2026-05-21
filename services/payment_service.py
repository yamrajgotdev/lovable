"""
Payment Service
Handles CASH and ONLINE (Razorpay) payment flows.
"""
import logging
from typing import Dict, Optional
from decimal import Decimal
from django.utils import timezone
from django.conf import settings

from rides.models import Ride
from rides.services.state_machine import RideStateMachine
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger('rides.payments')


class PaymentService:
    """
    Manages payment flows for rides.
    
    CASH Flow:
    1. Passenger sees "Pay driver at end"
    2. Driver sees "Collect ₹X from customer"
    3. Driver taps "I have collected"
    4. Mark ride payment as SUCCESS
    5. Complete ride
    
    ONLINE Flow (Razorpay):
    1. Generate Razorpay order
    2. Show QR/payment link to passenger
    3. Track payment status
    4. On success, mark payment SUCCESS
    5. Notify both passenger and driver
    """

    @classmethod
    def initialize_payment(cls, ride_id: int, payment_method: str) -> Dict:
        """
        Initialize payment for a ride.
        Called when driver starts ride or when setting up online payment.
        """
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return {'success': False, 'message': 'Ride not found'}
        
        if payment_method not in [Ride.PAYMENT_CASH, Ride.PAYMENT_ONLINE]:
            return {'success': False, 'message': 'Invalid payment method'}
        
        ride.payment_method = payment_method
        ride.payment_status = Ride.PAYMENT_PENDING
        ride.save(update_fields=['payment_method', 'payment_status'])
        
        if payment_method == Ride.PAYMENT_CASH:
            return {
                'success': True,
                'method': 'CASH',
                'message': 'Cash payment selected',
                'instructions': {
                    'passenger': 'Pay driver at the end of the ride',
                    'driver': f'Collect ₹{ride.final_fare or ride.estimated_fare} from customer'
                }
            }
        
        else:  # ONLINE
            # Generate Razorpay order
            order_result = cls._create_razorpay_order(ride)
            return {
                'success': order_result['success'],
                'method': 'ONLINE',
                'order_id': order_result.get('order_id'),
                'amount': order_result.get('amount'),
                'currency': order_result.get('currency', 'INR'),
                'message': order_result.get('message', 'Online payment initialized')
            }

    @classmethod
    def confirm_cash_collection(cls, ride_id: int, driver_id: int) -> Dict:
        """
        Driver confirms cash collection.
        This is the critical step for cash rides.
        """
        try:
            ride = Ride.objects.select_related('driver').get(id=ride_id)
        except Ride.DoesNotExist:
            return {'success': False, 'message': 'Ride not found'}
        
        # Verify driver
        if not ride.driver or ride.driver.id != driver_id:
            return {'success': False, 'message': 'Driver mismatch'}
        
        # Verify payment method
        if ride.payment_method != Ride.PAYMENT_CASH:
            return {'success': False, 'message': 'Payment method is not cash'}
        
        # Payment collection is valid after destination/payment stage.
        if ride.status not in [
            Ride.STATUS_REACHED_DESTINATION,
            Ride.STATUS_PAYMENT_REQUIRED,
            Ride.STATUS_PAYMENT_CONFIRMED,
        ]:
            return {'success': False, 'message': f'Ride not ready for payment collection: {ride.status}'}
        
        # Mark payment as received
        ride.payment_status = Ride.PAYMENT_SUCCESS
        ride.payment_received_at = timezone.now()
        ride.save(update_fields=['payment_status', 'payment_received_at'])
        
        # Move through explicit payment states (do not auto-complete here).
        if ride.status == Ride.STATUS_REACHED_DESTINATION:
            RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_PAYMENT_REQUIRED,
                actor_type='system',
                metadata={'payment_method': 'cash'}
            )
        RideStateMachine.transition(
            ride_id=ride.id,
            new_status=Ride.STATUS_PAYMENT_CONFIRMED,
            actor_type='system',
            metadata={'payment_confirmed': True, 'collected_by': 'driver'}
        )
        RideStateMachine.transition(
            ride_id=ride.id,
            new_status=Ride.STATUS_COMPLETED,
            actor_type='system',
            metadata={'payment_completed': True, 'collected_by': 'driver'}
        )
        
        # Notify passenger
        cls._notify_passenger_payment(ride, {
            'type': 'payment_success',
            'method': 'CASH',
            'amount': float(ride.final_fare) if ride.final_fare else None,
            'message': 'Payment confirmed by driver'
        })

        # Broadcast updated stats to driver via WebSocket
        from rides.services.notification_service import NotificationService
        if ride.driver:
            NotificationService.broadcast_driver_stats_update(ride.driver)

        return {
            'success': True,
            'message': 'Cash collection confirmed.',
            'amount_collected': float(ride.final_fare) if ride.final_fare else None
        }

    @classmethod
    def handle_online_payment_success(cls, ride_id: int, 
                                    razorpay_payment_id: str,
                                    razorpay_order_id: str) -> Dict:
        """
        Handle successful Razorpay payment webhook/callback.
        """
        try:
            ride = Ride.objects.select_related('driver', 'passenger').get(id=ride_id)
        except Ride.DoesNotExist:
            return {'success': False, 'message': 'Ride not found'}
        
        # Verify order match
        if ride.razorpay_order_id != razorpay_order_id:
            return {'success': False, 'message': 'Order ID mismatch'}
        
        # Update payment status
        ride.payment_status = Ride.PAYMENT_SUCCESS
        ride.razorpay_payment_id = razorpay_payment_id
        ride.payment_received_at = timezone.now()
        ride.save(update_fields=[
            'payment_status', 'razorpay_payment_id', 'payment_received_at'
        ])

        # Move through explicit payment states in canonical order.
        if ride.status == Ride.STATUS_REACHED_DESTINATION:
            RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_PAYMENT_REQUIRED,
                actor_type='system',
                metadata={'payment_method': 'online'}
            )
            ride.refresh_from_db(fields=['status'])
        if ride.status in [Ride.STATUS_PAYMENT_REQUIRED, Ride.STATUS_REACHED_DESTINATION]:
            RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_PAYMENT_CONFIRMED,
                actor_type='system',
                metadata={'payment_confirmed': True, 'gateway': 'razorpay'}
            )
            RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_COMPLETED,
                actor_type='system',
                metadata={'payment_completed': True, 'gateway': 'razorpay'}
            )
        
        # Notify passenger
        cls._notify_passenger_payment(ride, {
            'type': 'payment_success',
            'method': 'ONLINE',
            'amount': float(ride.final_fare) if ride.final_fare else None,
            'message': 'Payment successful!'
        })
        
        # Notify driver - payment received via WebSocket
        if ride.driver:
            cls._notify_driver_payment(ride.driver.id, {
                'type': 'payment_received',
                'ride_id': ride.id,
                'amount': float(ride.driver_share) if ride.driver_share else None,
                'message': f'Payment received! ₹{ride.driver_share} added to your wallet.',
                'added_to_wallet': True
            })
            
            # Send formatted notification and broadcast stats update
            from rides.services.notification_service import NotificationService
            try:
                NotificationService.notify_driver_online_payment_received(
                    ride.driver, ride, ride.driver_share
                )
                # Broadcast updated stats to driver via WebSocket
                NotificationService.broadcast_driver_stats_update(ride.driver)
            except Exception as e:
                logger.error(f"Error sending payment notification: {e}")
        
        logger.info(f"Ride {ride_id}: Online payment success. Payment ID: {razorpay_payment_id}")
        
        return {
            'success': True,
            'message': 'Payment processed successfully',
            'driver_notified': True,
            'wallet_updated': True
        }

    @classmethod
    def get_payment_status(cls, ride_id: int) -> Dict:
        """Get current payment status for a ride."""
        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return {'error': 'Ride not found'}
        
        result = {
            'ride_id': ride.id,
            'payment_method': ride.payment_method,
            'payment_status': ride.payment_status,
            'amount': float(ride.final_fare) if ride.final_fare else None,
            'can_complete': False
        }
        
        if ride.payment_method == Ride.PAYMENT_CASH:
            result['driver_action_required'] = ride.status in [
                Ride.STATUS_REACHED_DESTINATION,
                Ride.STATUS_PAYMENT_REQUIRED,
            ]
            result['driver_instruction'] = f'Collect ₹{ride.final_fare} and tap "Collected"'
            result['passenger_instruction'] = 'Pay driver at the end'
            result['can_complete'] = (
                ride.payment_status == Ride.PAYMENT_SUCCESS and
                ride.status in [Ride.STATUS_PAYMENT_CONFIRMED, Ride.STATUS_COMPLETED]
            )
            
        elif ride.payment_method == Ride.PAYMENT_ONLINE:
            result['passenger_action_required'] = ride.payment_status == Ride.PAYMENT_PENDING
            if ride.razorpay_order_id:
                result['order_id'] = ride.razorpay_order_id
                result['payment_link'] = f'/payment/{ride.razorpay_order_id}'
            result['can_complete'] = (
                ride.payment_status == Ride.PAYMENT_SUCCESS and
                ride.status in [Ride.STATUS_PAYMENT_CONFIRMED, Ride.STATUS_COMPLETED]
            )
        
        return result

    @classmethod
    def _create_razorpay_order(cls, ride: Ride) -> Dict:
        """
        Create Razorpay order for online payment.
        """
        try:
            import razorpay
            
            client = razorpay.Client(
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
            )
            
            amount_paise = int((ride.final_fare or ride.estimated_fare or 0) * 100)
            
            order_data = {
                'amount': amount_paise,
                'currency': 'INR',
                'receipt': f'ride_{ride.id}',
                'notes': {
                    'ride_id': str(ride.id),
                    'passenger_id': str(ride.passenger_id),
                    'driver_id': str(ride.driver_id) if ride.driver else None
                }
            }
            
            order = client.order.create(data=order_data)
            
            # Save order ID to ride
            ride.razorpay_order_id = order['id']
            ride.save(update_fields=['razorpay_order_id'])
            
            logger.info(f"Ride {ride.id}: Razorpay order created: {order['id']}")
            
            return {
                'success': True,
                'order_id': order['id'],
                'amount': order['amount'],  # in paise
                'currency': order['currency'],
                'key_id': settings.RAZORPAY_KEY_ID
            }
            
        except ImportError:
            logger.error("Razorpay SDK not installed")
            return {'success': False, 'message': 'Razorpay not configured'}
        except Exception as e:
            logger.exception(f"Error creating Razorpay order: {e}")
            return {'success': False, 'message': str(e)}

    @classmethod
    def verify_razorpay_signature(cls, razorpay_order_id: str, 
                                 razorpay_payment_id: str,
                                 razorpay_signature: str) -> bool:
        """Verify Razorpay webhook signature."""
        try:
            import razorpay
            
            client = razorpay.Client(
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
            )
            
            params_dict = {
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            }
            
            return client.utility.verify_payment_signature(params_dict)
            
        except Exception as e:
            logger.exception(f"Error verifying signature: {e}")
            return False

    @classmethod
    def _notify_passenger_payment(cls, ride: Ride, notification: dict):
        """Send payment notification to passenger."""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'ride_{ride.id}',
                {
                    'type': 'payment_update',
                    'notification': notification
                }
            )
        except Exception as e:
            logger.error(f"Error notifying passenger: {e}")

    @classmethod
    def _notify_driver_payment(cls, driver_id: int, notification: dict):
        """Send payment notification to driver."""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'driver_notifications_{driver_id}',
                {
                    'type': 'payment_received',
                    'notification': notification
                }
            )
        except Exception as e:
            logger.error(f"Error notifying driver: {e}")
