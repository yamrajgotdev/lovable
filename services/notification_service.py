"""
Notification Service for Ride-Hailing System
Handles all major event notifications for passengers and drivers.
"""
import logging
from typing import Dict, Optional
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from rides.models import Ride, Notification
from drivers.models import Driver
from rides.services.notification_center import NotificationCenter

logger = logging.getLogger('rides.notifications')


class NotificationMessages:
    """Centralized notification messages matching the app theme."""
    
    # Passenger Notifications
    RIDE_BOOKED = {
        'title': '🚗 Ride Booked',
        'body': 'Looking for nearby drivers...',
        'sound': 'default'
    }
    
    DRIVER_ASSIGNED = {
        'title': '🎉 Driver Found!',
        'body': '{driver_name} is on the way in a {vehicle_type}',
        'sound': 'default'
    }
    
    DRIVER_ARRIVING = {
        'title': '📍 Driver En Route',
        'body': '{driver_name} is {distance} away - ETA {eta}',
        'sound': 'default'
    }
    
    DRIVER_ARRIVED = {
        'title': '📍 Driver Arrived',
        'body': '{driver_name} has arrived at your pickup location',
        'sound': 'notification_arrived'
    }
    
    RIDE_STARTED = {
        'title': '🚀 Ride Started',
        'body': 'Heading to {drop_address}',
        'sound': 'default'
    }

    DESTINATION_REACHED = {
        'title': '📍 Destination Reached',
        'body': 'Driver has arrived at your destination. Please complete payment.',
        'sound': 'default'
    }

    RIDE_COMPLETED = {
        'title': '✅ Ride Completed',
        'body': 'You reached your destination! Fare: ₹{fare}',
        'sound': 'default'
    }
    
    RIDE_CANCELLED_BY_DRIVER = {
        'title': '❌ Ride Cancelled',
        'body': 'Driver cancelled. Booking another driver...',
        'sound': 'default'
    }
    
    DRIVER_CANCELLED_SEARCHING_NEW = {
        'title': '🔄 Finding New Driver',
        'body': 'Previous driver cancelled. Searching for another driver...',
        'sound': 'default'
    }
    
    RIDE_CANCELLED_BY_PASSENGER = {
        'title': 'Ride Cancelled',
        'body': 'You cancelled the ride',
        'sound': 'default'
    }
    
    PAYMENT_RECEIVED = {
        'title': '💰 Payment Confirmed',
        'body': 'Thank you for riding with us!',
        'sound': 'default'
    }
    
    RATING_REQUEST = {
        'title': '⭐ Rate Your Ride',
        'body': 'How was your ride with {driver_name}?',
        'sound': None
    }
    
    # Driver Notifications
    YOU_ARE_ONLINE = {
        'title': '🟢 You are Online',
        'body': 'You are now receiving ride requests in your area',
        'sound': 'default'
    }
    
    YOU_ARE_OFFLINE = {
        'title': '🔴 You are Offline',
        'body': 'Go online to start receiving requests',
        'sound': 'default'
    }
    
    RIDE_REQUEST_NEARBY = {
        'title': '🚕 New Ride Request!',
        'body': 'Pickup: {distance} away - ₹{estimated_fare}',
        'sound': 'ride_request',
        'priority': 'high'
    }
    
    RIDE_REQUEST_BROADCAST = {
        'title': '📢 Ride Available',
        'body': 'New ride in your area - Tap to view',
        'sound': 'ride_request'
    }
    
    RIDE_ACCEPTED_BY_YOU = {
        'title': '✅ Ride Accepted',
        'body': 'Head to pickup: {pickup_address}',
        'sound': 'default'
    }
    
    PASSENGER_CANCELLED = {
        'title': '❌ Ride Cancelled',
        'body': 'Passenger cancelled the ride',
        'sound': 'default'
    }
    
    YOU_CANCELLED_RIDE = {
        'title': 'Ride Cancelled',
        'body': 'You cancelled the ride',
        'sound': 'default'
    }
    
    ARRIVE_REMINDER = {
        'title': '📍 Almost There',
        'body': 'Tap "Arrived" when you reach pickup location',
        'sound': 'default'
    }
    
    OTP_VERIFIED = {
        'title': '✅ OTP Verified',
        'body': 'Ride started! Drive safe.',
        'sound': 'default'
    }
    
    CASH_COLLECTED = {
        'title': '💵 Cash Collected',
        'body': '₹{amount} collected. Have a great day!',
        'sound': 'default'
    }
    
    ONLINE_PAYMENT_RECEIVED = {
        'title': '💳 Payment Received',
        'body': '₹{driver_share} added to your wallet',
        'sound': 'payment_received'
    }
    
    RATING_RECEIVED = {
        'title': '⭐ New Rating!',
        'body': 'Passenger rated you {rating} stars',
        'sound': None
    }
    
    # GPS / Location Notifications
    GPS_OFF_WARNING = {
        'title': '📍 Location Sharing Off',
        'body': 'Going offline in 30 seconds. Keep app open to stay online.',
        'sound': 'warning'
    }
    
    WENT_OFFLINE_AUTO = {
        'title': '🔴 Went Offline',
        'body': 'You were marked offline due to no location updates',
        'sound': 'default'
    }
    
    GPS_RESUMED = {
        'title': '🟢 Back Online',
        'body': 'Location sharing resumed. You are now receiving requests.',
        'sound': 'default'
    }


class NotificationService:
    """
    Service for sending notifications to passengers and drivers.
    Supports WebSocket (real-time) and Push notifications.
    """
    
    @classmethod
    def _send_websocket(cls, group_name: str, message_type: str, payload: dict):
        """Send notification via WebSocket."""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'notification',
                    'message_type': message_type,
                    'payload': payload,
                    'timestamp': timezone.now().isoformat()
                }
            )
        except Exception as e:
            logger.error(f"WebSocket notification failed: {e}")
    
    @classmethod
    def _format_message(cls, template: dict, **kwargs) -> dict:
        """Format message template with variables."""
        return {
            'title': template['title'].format(**kwargs) if kwargs else template['title'],
            'body': template['body'].format(**kwargs) if kwargs else template['body'],
            'sound': template.get('sound'),
            'priority': template.get('priority', 'normal'),
            'timestamp': timezone.now().isoformat()
        }
    
    # ==================== PASSENGER NOTIFICATIONS ====================
    
    @classmethod
    def notify_passenger_ride_booked(cls, ride: Ride):
        """Ride successfully booked, searching for drivers."""
        msg = NotificationMessages.RIDE_BOOKED
        data = {
            'ride_id': ride.id,
            'status': 'searching_driver',
            'pickup': ride.pickup_address,
            'drop': ride.drop_address,
            'estimated_fare': float(ride.estimated_fare) if ride.estimated_fare else None
        }
        NotificationCenter.create_and_broadcast(
            ride.passenger,
            Notification.TYPE_RIDE_BOOKED,
            msg['body'],
            data=data
        )
        logger.info(f"Ride booked notification persisted for passenger {ride.passenger_id}")
    
    @classmethod
    def notify_passenger_driver_assigned(cls, ride: Ride):
        """Driver found and assigned to ride."""
        if not ride.driver:
            return
            
        payload = {
            **cls._format_message(
                NotificationMessages.DRIVER_ASSIGNED,
                driver_name=ride.driver.name,
                vehicle_type=ride.driver.get_vehicle_type_display()
            ),
            'ride_id': ride.id,
            'status': 'driver_assigned',
            'driver': {
                'id': ride.driver.id,
                'name': ride.driver.name,
                'phone': ride.driver.user.phone_number,
                'vehicle_type': ride.driver.vehicle_type,
                'vehicle_number': ride.driver.vehicle_number,
                'rating': float(ride.driver.rating) if ride.driver.rating else None,
                'photo': ride.driver.profile_photo.url if ride.driver.profile_photo else None
            },
            'otp': ride.otp
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'driver_assigned', payload)
        logger.info(f"Driver assigned notification sent to passenger {ride.passenger_id}")
    
    @classmethod
    def notify_passenger_driver_arriving(cls, ride: Ride, distance_km: float, eta_minutes: int):
        """Driver is en route to pickup."""
        if not ride.driver:
            return
            
        payload = {
            **cls._format_message(
                NotificationMessages.DRIVER_ARRIVING,
                driver_name=ride.driver.name,
                distance=f"{distance_km:.1f} km",
                eta=f"{eta_minutes} min"
            ),
            'ride_id': ride.id,
            'status': 'driver_arriving',
            'driver_location': {
                'lat': ride.driver.current_lat,
                'lng': ride.driver.current_lng
            },
            'distance_km': distance_km,
            'eta_minutes': eta_minutes
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'driver_arriving', payload)
    
    @classmethod
    def notify_passenger_driver_arrived(cls, ride: Ride):
        """Driver has arrived at pickup location."""
        if not ride.driver:
            return
            
        payload = {
            **cls._format_message(
                NotificationMessages.DRIVER_ARRIVED,
                driver_name=ride.driver.name
            ),
            'ride_id': ride.id,
            'status': 'arrived',
            'waiting_info': {
                'message': 'First 2 minutes free, ₹3/min after',
                'free_seconds': 120,
                'charge_per_minute': 3
            }
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'driver_arrived', payload)
        logger.info(f"Driver arrived notification sent to passenger {ride.passenger_id}")
    
    @classmethod
    def notify_passenger_ride_started(cls, ride: Ride):
        """Ride has started (OTP verified)."""
        payload = {
            **cls._format_message(
                NotificationMessages.RIDE_STARTED,
                drop_address=ride.drop_address[:50] + '...' if len(ride.drop_address) > 50 else ride.drop_address
            ),
            'ride_id': ride.id,
            'status': 'started',
            'share_live_location': True
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'ride_started', payload)

    @classmethod
    def notify_passenger_destination_reached(cls, ride: Ride):
        """Driver has reached destination - prompt passenger for payment."""
        from rides.services.billing_service import BillingService

        fare_breakdown = BillingService.calculate_final_fare(ride)

        payload = {
            **cls._format_message(NotificationMessages.DESTINATION_REACHED),
            'ride_id': ride.id,
            'status': 'reached_destination',
            'fare_breakdown': fare_breakdown,
            'payment_required': True,
            'payment_methods': ['cash', 'online'] if not ride.payment_method else [ride.payment_method.lower()]
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'destination_reached', payload)
        logger.info(f"Destination reached notification sent to passenger {ride.passenger_id}")

    @classmethod
    def notify_passenger_ride_completed(cls, ride: Ride):
        """Ride completed with fare breakdown."""
        from rides.services.billing_service import BillingService
        
        fare_breakdown = BillingService.calculate_final_fare(ride)
        
        payload = {
            **cls._format_message(
                NotificationMessages.RIDE_COMPLETED,
                fare=f"{fare_breakdown['total']:.2f}"
            ),
            'ride_id': ride.id,
            'status': 'completed',
            'fare_breakdown': fare_breakdown,
            'rating_required': True
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'ride_completed', payload)
        
        # Also send rating request notification after a delay
        # This would be handled by a Celery task in production
    
    @classmethod
    def notify_passenger_ride_cancelled(cls, ride: Ride, cancelled_by: str, reason: str = None):
        """Ride was cancelled."""
        if cancelled_by == 'driver':
            message = NotificationMessages.RIDE_CANCELLED_BY_DRIVER
        else:
            message = NotificationMessages.RIDE_CANCELLED_BY_PASSENGER
            
        payload = {
            **cls._format_message(message),
            'ride_id': ride.id,
            'status': 'cancelled',
            'cancelled_by': cancelled_by,
            'reason': reason,
            'refund_info': 'Any charges will be refunded within 5-7 business days' if cancelled_by == 'driver' else None
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'ride_cancelled', payload)
    
    @classmethod
    def notify_passenger_driver_cancelled_searching(cls, ride: Ride, previous_driver: Driver):
        """Driver cancelled but we're finding a new driver - ride continues."""
        payload = {
            **cls._format_message(NotificationMessages.DRIVER_CANCELLED_SEARCHING_NEW),
            'ride_id': ride.id,
            'status': 'searching_driver',
            'previous_driver_id': previous_driver.id if previous_driver else None,
            'pickup_address': ride.pickup_address,
            'drop_address': ride.drop_address,
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'driver_cancelled_searching', payload)
    
    @classmethod
    def notify_passenger_payment_confirmed(cls, ride: Ride):
        """Payment received/confirmed."""
        payload = {
            **cls._format_message(NotificationMessages.PAYMENT_RECEIVED),
            'ride_id': ride.id,
            'payment_method': ride.payment_method,
            'amount': float(ride.final_fare) if ride.final_fare else None,
            'invoice_url': f'/api/rides/{ride.id}/invoice'
        }
        cls._send_websocket(f'passenger_{ride.passenger_id}', 'payment_confirmed', payload)
    
    # ==================== DRIVER NOTIFICATIONS ====================
    
    @classmethod
    def notify_driver_online_status(cls, driver: Driver, is_online: bool):
        """Driver online/offline status change."""
        if is_online:
            message = NotificationMessages.YOU_ARE_ONLINE
            status = 'online'
        else:
            message = NotificationMessages.YOU_ARE_OFFLINE
            status = 'offline'
            
        payload = {
            **cls._format_message(message),
            'status': status,
            'timestamp': timezone.now().isoformat()
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'online_status', payload)
    
    @classmethod
    def notify_driver_ride_request(cls, driver: Driver, ride: Ride, distance_km: float, request_id: str):
        """New ride request for driver."""
        pickup_to_drop_km = float(ride.distance_km or 0)
        payload = {
            **cls._format_message(
                NotificationMessages.RIDE_REQUEST_NEARBY,
                distance=f"{distance_km:.1f} km",
                estimated_fare=f"{ride.estimated_fare:.0f}" if ride.estimated_fare else "--"
            ),
            'request_id': request_id,
            'ride_id': ride.id,
            'pickup': {
                'address': ride.pickup_address,
                'lat': ride.pickup_lat,
                'lng': ride.pickup_lng
            },
            'drop': {
                'address': ride.drop_address,
                'lat': ride.drop_lat,
                'lng': ride.drop_lng
            },
            # Preserve explicit distance semantics for frontend fare display.
            'driver_to_pickup_km': float(distance_km or 0),
            'pickup_to_drop_km': pickup_to_drop_km,
            'distance_km': pickup_to_drop_km,
            'estimated_fare': float(ride.estimated_fare) if ride.estimated_fare else None,
            'vehicle_type': ride.vehicle_type,
            'timeout_seconds': 8,
            'action_required': True
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'ride_request', payload)
        logger.info(f"Ride request notification sent to driver {driver.id}")
    
    @classmethod
    def notify_driver_broadcast_ride(cls, driver: Driver, ride: Ride, distance_km: float):
        """Broadcast ride request to nearby drivers."""
        payload = {
            **cls._format_message(NotificationMessages.RIDE_REQUEST_BROADCAST),
            'ride_id': ride.id,
            'pickup_address': ride.pickup_address[:60] + '...' if len(ride.pickup_address) > 60 else ride.pickup_address,
            'distance_km': distance_km,
            'estimated_fare': float(ride.estimated_fare) if ride.estimated_fare else None
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'broadcast_ride', payload)
    
    @classmethod
    def notify_driver_ride_accepted(cls, driver: Driver, ride: Ride):
        """Driver successfully accepted ride."""
        payload = {
            **cls._format_message(
                NotificationMessages.RIDE_ACCEPTED_BY_YOU,
                pickup_address=ride.pickup_address[:60] + '...' if len(ride.pickup_address) > 60 else ride.pickup_address
            ),
            'ride_id': ride.id,
            'passenger': {
                'name': ride.passenger.get_full_name() or 'Passenger',
                'phone': ride.passenger.phone_number
            },
            'pickup': {
                'lat': ride.pickup_lat,
                'lng': ride.pickup_lng,
                'address': ride.pickup_address
            },
            'otp': ride.otp[:4] + '**' if len(ride.otp) > 4 else ride.otp  # Partial OTP preview
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'ride_accepted', payload)

    @classmethod
    def notify_other_drivers_ride_taken(cls, ride_id: int, accepted_driver_id: int):
        """
        Notify other dispatched drivers that this ride is no longer available.
        Used to immediately close stale incoming popups.
        """
        try:
            from rides.dispatch_service import DispatchService
            from rideapp.redis_utils import GracefulCache

            queue_key = DispatchService._get_dispatch_queue_key(ride_id)
            queue_data = GracefulCache.get(queue_key) or {}
            driver_ids = queue_data.get('driver_ids', []) if isinstance(queue_data, dict) else []

            payload = {
                'ride_id': ride_id,
                'accepted_driver_id': accepted_driver_id,
                'status': 'taken',
                'title': 'Ride already taken',
                'body': 'This ride was accepted by another driver.',
            }

            for driver_id in driver_ids:
                if str(driver_id) == str(accepted_driver_id):
                    continue
                cls._send_websocket(f'driver_notifications_{driver_id}', 'ride_taken', payload)
        except Exception as e:
            logger.warning(f"Failed to broadcast ride_taken for ride {ride_id}: {e}")
    
    @classmethod
    def notify_driver_passenger_cancelled(cls, driver: Driver, ride: Ride):
        """Passenger cancelled the ride."""
        payload = {
            **cls._format_message(NotificationMessages.PASSENGER_CANCELLED),
            'ride_id': ride.id,
            'status': 'cancelled',
            'you_are_now': 'AVAILABLE',
            'look_for_new_requests': True
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'passenger_cancelled', payload)
    
    @classmethod
    def notify_driver_you_cancelled(cls, driver: Driver, ride: Ride):
        """Driver cancelled the ride."""
        payload = {
            **cls._format_message(NotificationMessages.YOU_CANCELLED_RIDE),
            'ride_id': ride.id,
            'status': 'cancelled',
            'you_are_now': 'AVAILABLE'
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'you_cancelled', payload)
    
    @classmethod
    def notify_driver_arrive_reminder(cls, driver: Driver, ride: Ride):
        """Reminder to tap "Arrived" when near pickup."""
        payload = {
            **cls._format_message(NotificationMessages.ARRIVE_REMINDER),
            'ride_id': ride.id,
            'action': 'tap_arrived'
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'arrive_reminder', payload)
    
    @classmethod
    def notify_driver_otp_verified(cls, driver: Driver, ride: Ride):
        """OTP verified, ride started."""
        payload = {
            **cls._format_message(NotificationMessages.OTP_VERIFIED),
            'ride_id': ride.id,
            'status': 'started',
            'destination': ride.drop_address[:60] + '...' if len(ride.drop_address) > 60 else ride.drop_address
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'otp_verified', payload)
    
    @classmethod
    def notify_driver_cash_collected(cls, driver: Driver, ride: Ride, amount: float):
        """Cash payment collected."""
        payload = {
            **cls._format_message(
                NotificationMessages.CASH_COLLECTED,
                amount=f"{amount:.2f}"
            ),
            'ride_id': ride.id,
            'amount': amount,
            'ride_complete': True
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'cash_collected', payload)
    
    @classmethod
    def notify_driver_online_payment_received(cls, driver: Driver, ride: Ride, driver_share: float):
        """Online payment received, added to wallet."""
        payload = {
            **cls._format_message(
                NotificationMessages.ONLINE_PAYMENT_RECEIVED,
                driver_share=f"{driver_share:.2f}"
            ),
            'ride_id': ride.id,
            'amount_added': driver_share,
            'wallet_balance': float(driver.wallet_balance) if hasattr(driver, 'wallet_balance') else None
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'payment_received', payload)
    
    @classmethod
    def notify_driver_rating_received(cls, driver: Driver, rating: int, feedback: str = None):
        """Passenger rated the driver."""
        payload = {
            **cls._format_message(
                NotificationMessages.RATING_RECEIVED,
                rating=rating
            ),
            'rating': rating,
            'feedback': feedback,
            'stars': '⭐' * rating
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'rating_received', payload)
    
    @classmethod
    def notify_driver_gps_warning(cls, driver: Driver, seconds_remaining: int):
        """GPS off warning, going offline soon."""
        payload = {
            **cls._format_message(NotificationMessages.GPS_OFF_WARNING),
            'seconds_remaining': seconds_remaining,
            'action_required': 'resume_gps'
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'gps_warning', payload)
    
    @classmethod
    def notify_driver_went_offline_auto(cls, driver: Driver):
        """Auto-marked offline due to no GPS."""
        payload = {
            **cls._format_message(NotificationMessages.WENT_OFFLINE_AUTO),
            'status': 'offline',
            'to_go_online': 'Open app and toggle online'
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'went_offline_auto', payload)
    
    @classmethod
    def notify_driver_gps_resumed(cls, driver: Driver):
        """GPS resumed, back online."""
        payload = {
            **cls._format_message(NotificationMessages.GPS_RESUMED),
            'status': 'online'
        }
        cls._send_websocket(f'driver_notifications_{driver.id}', 'gps_resumed', payload)

    @classmethod
    def broadcast_driver_stats_update(cls, driver: Driver):
        """
        Broadcast updated stats to driver via WebSocket.
        Called when a ride completes or payment is confirmed.
        """
        from django.db.models import Avg, Count
        from rides.models import Ride
        from payments.models import DriverWallet

        try:
            # Today's earnings
            today = timezone.now().date()
            today_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))

            completed_rides = Ride.objects.filter(
                driver=driver,
                status='COMPLETED'
            )
            today_earnings = sum(
                float(ride.final_fare or ride.estimated_fare or 0)
                for ride in completed_rides.filter(completed_at__gte=today_start)
            )

            # Total completed rides
            total_rides = completed_rides.count()

            # Wallet balance
            try:
                wallet = DriverWallet.objects.get(driver=driver)
                wallet_balance = wallet.balance
            except DriverWallet.DoesNotExist:
                wallet_balance = 0

            # Rating
            rating_data = Ride.objects.filter(
                driver=driver,
                status='COMPLETED',
                driver_rating__isnull=False
            ).aggregate(
                avg_rating=Avg('driver_rating'),
                rating_count=Count('driver_rating')
            )
            rating = rating_data['avg_rating'] or 0

            stats = {
                'earningsToday': float(today_earnings),
                'totalRides': total_rides,
                'walletBalance': float(wallet_balance),
                'rating': round(float(rating), 2) if rating else 0,
            }

            # Send to driver's stats WebSocket group
            cls._send_websocket(f'driver_stats_{driver.id}', 'stats_update', {
                'stats': stats
            })

            logger.info(f"Stats update broadcast to driver {driver.id}: earnings={today_earnings}, rides={total_rides}")

        except Exception as e:
            logger.error(f"Error broadcasting stats update: {e}")
