import json
import logging
from datetime import datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger("rides.consumers")

class RideConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.ride_id = self.scope['url_route']['kwargs']['ride_id']
        self.room_group_name = f'ride_{self.ride_id}'

        # Verify user has access to this ride
        if not await self.check_ride_access():
            await self.close()
            return

        # Join ride group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        # If user is passenger, also join driver's location group if driver is assigned
        self.driver_id = await self.get_assigned_driver_id()
        if self.driver_id:
            await self.channel_layer.group_add(
                f"driver_{self.driver_id}",
                self.channel_name
            )

        await self.accept()
        
        # Send initial state synchronization
        await self.sync_state()

    async def sync_state(self):
        """Send current ride state to client upon connection/reconnection."""
        import logging
        logger = logging.getLogger('rides.consumers')
        
        user = self.scope["user"]
        ride_data = await self.get_ride_state()
        if ride_data:
            payload = {
                'type': 'ride_sync',
                'status': ride_data.get('status_public') or ride_data['status'],
                'ride_id': self.ride_id,
            }

            # Add role-specific info
            if user.is_authenticated:
                try:
                    role_data = await self.get_user_role_info(user, ride_data)
                    payload.update(role_data)
                except Exception as e:
                    logger.error(f"Error in sync_state: {e}")

            payload['driver'] = ride_data['driver']
            logger.info(f"[WS] ride_sync sent: ride={self.ride_id}, status={ride_data['status']}")
            await self.send(text_data=json.dumps(payload))

    @database_sync_to_async
    def get_user_role_info(self, user, ride_data):
        """Get user role and OTP info (sync wrapper for async context)."""
        is_driver = hasattr(user, 'driver_profile') and ride_data.get('driver') and ride_data['driver']['id'] == user.driver_profile.id
        if is_driver:
            return {
                'role': 'driver',
                'otp_preview': ride_data['otp'][:4] if ride_data.get('otp') else None
            }
        else:
            return {
                'role': 'passenger',
                'otp': ride_data.get('otp')
            }

    def _is_valid_coordinate(self, lat, lng):
        """Validate coordinates to prevent map glitches (reject null, 0,0, or out-of-bounds)."""
        if lat is None or lng is None:
            return False
        try:
            lat_f = float(lat)
            lng_f = float(lng)
            if lat_f == 0 and lng_f == 0:  # Reject Africa (0,0)
                return False
            if not (-90 <= lat_f <= 90):
                return False
            if not (-180 <= lng_f <= 180):
                return False
            return True
        except (TypeError, ValueError):
            return False

    @database_sync_to_async
    def get_ride_state(self):
        from rides.models import Ride
        from rides.services.state_machine import RideStateMachine
        try:
            ride = Ride.objects.get(id=self.ride_id)
            # BUG FIX: Only include driver with valid coordinates to prevent map glitches
            driver_data = None
            if ride.driver:
                lat = ride.driver.current_lat
                lng = ride.driver.current_lng
                if self._is_valid_coordinate(lat, lng):
                    driver_data = {
                        'id': ride.driver.id,
                        'name': ride.driver.name,
                        'lat': float(lat),
                        'lng': float(lng),
                        'vehicle_number': ride.driver.vehicle_number,
                        'vehicle_type': ride.driver.vehicle_type
                    }
            return {
                'status': ride.status,
                'status_public': RideStateMachine.to_public_status(ride.status),
                'otp': ride.otp,
                'driver': driver_data
            }
        except Ride.DoesNotExist:
            return None
        except Exception as e:
            import logging
            logger = logging.getLogger('rides.consumers')
            logger.error(f"Error in get_ride_state: {e}")
            return None

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        if hasattr(self, 'driver_id') and self.driver_id:
            await self.channel_layer.group_discard(
                f"driver_{self.driver_id}",
                self.channel_name
            )

    @database_sync_to_async
    def check_ride_access(self):
        from rides.models import Ride
        user = self.scope["user"]
        if not user.is_authenticated:
            return False
        
        try:
            ride = Ride.objects.get(id=self.ride_id)
            return ride.passenger == user or (ride.driver and ride.driver.user == user)
        except Ride.DoesNotExist:
            return False

    @database_sync_to_async
    def get_assigned_driver_id(self):
        from rides.models import Ride
        try:
            ride = Ride.objects.get(id=self.ride_id)
            if ride.driver:
                return ride.driver.id
            return None
        except Exception as e:
            import logging
            logger = logging.getLogger('rides.consumers')
            logger.error(f"Error in get_assigned_driver_id: {e}")
            return None

    # Receive message from WebSocket
    async def receive(self, text_data):
        # We might not need to receive much from client here,
        # status updates are usually via REST for safety/transactions.
        pass

    # Receive message from group
    async def ride_update(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps(event))

    async def location_update(self, event):
        # Send driver location update to passenger
        # BUG FIX: Validate coordinates before broadcasting to prevent map glitches
        lat = event.get('latitude')
        lng = event.get('longitude')
        if not self._is_valid_coordinate(lat, lng):
            logger.warning(f"[WS] Ignoring invalid driver location for ride {self.ride_id}: lat={lat}, lng={lng}")
            return
        await self.send(text_data=json.dumps({
            'type': 'driver_location',
            'latitude': float(lat),
            'longitude': float(lng),
            'heading': event.get('heading')
        }))

    async def payment_update(self, event):
        """Send payment status update."""
        await self.send(text_data=json.dumps({
            'type': 'payment_update',
            'notification': event.get('notification', {})
        }))

    async def payment_received(self, event):
        """Forward driver payment received events on ride channel."""
        await self.send(text_data=json.dumps({
            'type': 'payment_received',
            'ride_id': event.get('ride_id'),
            'amount': event.get('amount'),
            'message': event.get('message'),
            'added_to_wallet': event.get('added_to_wallet', False),
        }))

    async def waiting_update(self, event):
        """Send waiting time updates during driver arrival."""
        await self.send(text_data=json.dumps({
            'type': 'waiting_update',
            'elapsed_seconds': event.get('elapsed_seconds'),
            'chargeable_seconds': event.get('chargeable_seconds'),
            'current_charge': event.get('current_charge'),
            'message': event.get('message')
        }))

    async def driver_arrived(self, event):
        """Notify passenger that driver has arrived."""
        await self.send(text_data=json.dumps({
            'type': 'driver_arrived',
            'message': 'Driver has arrived at your location',
            'waiting_info': event.get('waiting_info', {})
        }))

    async def ride_completed(self, event):
        """Notify about ride completion."""
        await self.send(text_data=json.dumps({
            'type': 'ride_completed',
            'message': 'Ride completed!',
            'fare_breakdown': event.get('fare_breakdown', {}),
            'rating_required': True
        }))

    async def notification(self, event):
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'notification': event.get('notification', {})
        }))


class DriverNotificationConsumer(AsyncWebsocketConsumer):
    """
    Consumer for driver-specific notifications.
    Handles ride requests, payment notifications, and status updates.
    """
    
    async def connect(self):
        user = self.scope["user"]
        
        # Use sync_to_async for all database access including auth check
        @database_sync_to_async
        def check_auth_and_get_driver(user):
            if not user.is_authenticated:
                return None
            if hasattr(user, 'driver_profile'):
                return user.driver_profile.id
            return None
        
        driver_id = await check_auth_and_get_driver(user)
        if not driver_id:
            await self.close()
            return
        
        self.driver_id = driver_id
        self.group_name = f'driver_notifications_{self.driver_id}'
        
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    def _event_payload(self, event):
        """Normalize event payloads sent via different dispatcher paths."""
        payload = event.get('notification') or event.get('payload')
        if isinstance(payload, dict):
            return payload
        return event
    
    async def ride_request(self, event):
        """Send ride request to driver."""
        source = self._event_payload(event)
        pickup = source.get('pickup') if isinstance(source.get('pickup'), dict) else {}
        drop = source.get('drop') if isinstance(source.get('drop'), dict) else {}
        trip_distance_km = source.get('pickup_to_drop_km')
        if trip_distance_km is None:
            trip_distance_km = source.get('trip_distance_km')
        if trip_distance_km is None:
            trip_distance_km = source.get('distance_km')
        await self.send(text_data=json.dumps({
            'type': 'ride_request',
            'request_id': source.get('request_id') or event.get('request_id'),
            'ride_id': source.get('ride_id') or event.get('ride_id'),
            'pickup_lat': pickup.get('lat', source.get('pickup_lat')),
            'pickup_lng': pickup.get('lng', source.get('pickup_lng')),
            'pickup_address': pickup.get('address', source.get('pickup_address')),
            'drop_lat': drop.get('lat', source.get('drop_lat')),
            'drop_lng': drop.get('lng', source.get('drop_lng')),
            'drop_address': drop.get('address', source.get('drop_address')),
            'distance_km': trip_distance_km,
            'pickup_to_drop_km': trip_distance_km,
            'driver_to_pickup_km': source.get('driver_to_pickup_km'),
            'estimated_fare': source.get('estimated_fare'),
            'vehicle_type': source.get('vehicle_type'),
            'timeout_seconds': source.get('timeout_seconds', 8)
        }))
    
    async def broadcast_ride(self, event):
        """Send broadcast ride request."""
        source = self._event_payload(event)
        pickup = source.get('pickup') if isinstance(source.get('pickup'), dict) else {}
        drop = source.get('drop') if isinstance(source.get('drop'), dict) else {}
        trip_distance_km = source.get('pickup_to_drop_km')
        if trip_distance_km is None:
            trip_distance_km = source.get('trip_distance_km')
        if trip_distance_km is None:
            trip_distance_km = source.get('distance_km')
        await self.send(text_data=json.dumps({
            'type': 'broadcast_ride_request',
            'ride_id': source.get('ride_id') or event.get('ride_id'),
            'pickup_lat': pickup.get('lat', source.get('pickup_lat')),
            'pickup_lng': pickup.get('lng', source.get('pickup_lng')),
            'pickup_address': pickup.get('address', source.get('pickup_address')),
            'drop_lat': drop.get('lat', source.get('drop_lat')),
            'drop_lng': drop.get('lng', source.get('drop_lng')),
            'drop_address': drop.get('address', source.get('drop_address')),
            'distance_km': trip_distance_km,
            'pickup_to_drop_km': trip_distance_km,
            'driver_to_pickup_km': source.get('driver_to_pickup_km'),
            'estimated_fare': source.get('estimated_fare'),
            'vehicle_type': source.get('vehicle_type')
        }))
    
    async def notification(self, event):
        """Send general notification to driver."""
        payload = event.get('notification') if isinstance(event.get('notification'), dict) else {}
        if not payload:
            payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}

        event_type = event.get('message_type') or payload.get('type') or 'notification'
        # Compatibility mapping for frontend handlers.
        if event_type == 'broadcast_ride':
            event_type = 'broadcast_ride_request'

        await self.send(text_data=json.dumps({
            'type': event_type,
            'title': payload.get('title'),
            'message': payload.get('message') or payload.get('body'),
            'data': payload
        }))
    
    async def payment_received(self, event):
        """Notify driver of payment received."""
        await self.send(text_data=json.dumps({
            'type': 'payment_received',
            'ride_id': event.get('ride_id'),
            'amount': event.get('amount'),
            'message': event.get('message'),
            'added_to_wallet': event.get('added_to_wallet', False)
        }))


class UserNotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            logger.warning("[WS CONNECT] notifications unauthenticated")
            await self.close()
            return
            
        # Mandatory Safeguard: Rate limit connection attempts
        from rideapp.enhanced_throttling import WSRateLimiter
        client_ip = self.scope.get('client', ('', 0))[0]
        if not WSRateLimiter.is_allowed(str(user.id), client_ip, 'connect', limit=5, window=60):
            logger.warning("[WS CONNECT] notifications throttled user=%s ip=%s", user.id, client_ip)
            await self.close(code=4001) # Policy violation
            return

        self.group_name = f"user_notifications_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        
        # Cursor-based replay
        query_params = self.scope.get('query_string', b'').decode()
        last_seq = None
        if 'last_seq=' in query_params:
            try:
                last_seq = int(query_params.split('last_seq=')[1].split('&')[0])
            except (ValueError, IndexError):
                pass
        logger.info("[WS CONNECT] notifications user=%s last_seq=%s", user.id, last_seq)
        
        if last_seq is not None:
            logger.info("[WS RECONNECT] notifications replay user=%s from_seq=%s", user.id, last_seq)
            await self.replay_notifications(last_seq)
        else:
            await self.send_unread_snapshot()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notification(self, event):
        await self.send(text_data=json.dumps({
            "type": "notification",
            "notification": event.get("notification", {}),
        }))

    @database_sync_to_async
    def _get_missed_notifications(self, last_seq):
        from rides.models import Notification
        # Limit to 50 events for scalability
        items = Notification.objects.filter(
            user=self.scope["user"], 
            sequence_id__gt=last_seq
        ).order_by("sequence_id")[:51] # Fetch 51 to check if more exist
        
        notifs = list(items[:50])
        has_more = len(items) > 50
        
        return notifs, has_more

    async def replay_notifications(self, last_seq):
        notifs, has_more = await self._get_missed_notifications(last_seq)
        
        if not notifs and not has_more:
            return

        # If too many missed events, force a full sync
        if has_more:
            await self.send(text_data=json.dumps({
                "type": "SYNC_REQUIRED",
                "reason": "too_many_missed_events",
                "last_seq_on_server": notifs[-1].sequence_id if notifs else None
            }))
            return

        for n in notifs:
            await self.send(text_data=json.dumps({
                "type": "notification",
                "notification": {
                    "notification_id": n.id,
                    "sequence_id": n.sequence_id,
                    "type": n.type,
                    "message": n.message,
                    "timestamp": n.timestamp.isoformat(),
                    **(n.metadata or {})
                },
                "is_replay": True
            }))

    @database_sync_to_async
    def _get_unread_snapshot(self):
        from rides.models import Notification
        items = Notification.objects.filter(user=self.scope["user"], is_read=False).order_by("-timestamp")[:20]
        return [
            {
                "notification_id": n.id,
                "type": n.type,
                "message": n.message,
                "timestamp": n.timestamp.isoformat(),
            }
            for n in items
        ]

    async def send_unread_snapshot(self):
        unread = await self._get_unread_snapshot()
        await self.send(text_data=json.dumps({
            "type": "notification_snapshot",
            "notifications": unread,
        }))


class WaitingTimeConsumer(AsyncWebsocketConsumer):
    """
    Consumer for real-time waiting time updates.
    Used when driver has arrived and is waiting for passenger.
    """
    
    async def connect(self):
        self.ride_id = self.scope['url_route']['kwargs']['ride_id']
        user = self.scope["user"]
        
        # Verify access
        if not await self.check_ride_access():
            await self.close()
            return
        
        self.group_name = f'waiting_{self.ride_id}'
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()
        
        # Send initial waiting state
        await self.send_waiting_state()
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
    
    @database_sync_to_async
    def check_ride_access(self):
        from rides.models import Ride
        user = self.scope["user"]
        if not user.is_authenticated:
            return False
        try:
            ride = Ride.objects.get(id=self.ride_id)
            return ride.passenger == user or (ride.driver and ride.driver.user == user)
        except Ride.DoesNotExist:
            return False
    
    @database_sync_to_async
    def get_waiting_state(self):
        from rides.services.billing_service import BillingService
        return BillingService.get_waiting_stopwatch_data(self.ride_id)
    
    async def send_waiting_state(self):
        """Send current waiting state to client."""
        state = await self.get_waiting_state()
        await self.send(text_data=json.dumps({
            'type': 'waiting_state',
            'data': state
        }))
    
    async def waiting_tick(self, event):
        """Receive periodic waiting time updates."""
        await self.send(text_data=json.dumps({
            'type': 'waiting_tick',
            'elapsed_seconds': event.get('elapsed_seconds'),
            'time_display': event.get('time_display'),
            'current_charge': event.get('current_charge'),
            'charge_applies': event.get('charge_applies')
        }))
    
    async def waiting_stopped(self, event):
        """Notify that waiting has stopped (OTP entered)."""
        await self.send(text_data=json.dumps({
            'type': 'waiting_stopped',
            'final_waiting_time': event.get('final_waiting_time'),
            'waiting_charge': event.get('waiting_charge'),
            'message': 'Waiting stopped - ride starting'
        }))


# ============================================================
# CHAT SYSTEM - Real-time chat consumer
# ADDED: WebSocket consumer for rider-passenger chat
# ============================================================

import time
from django.core.cache import cache

class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time rider-passenger chat.
    Handles send_message, receive_message, and mark_read events.
    Includes rate limiting to prevent spam.
    """

    # Rate limiting: max 30 messages per minute per user
    RATE_LIMIT_MAX = 30
    RATE_LIMIT_WINDOW = 60  # seconds

    # Valid ride statuses for sending messages (from booking until ride ends)
    # Database status values are UPPERCASE
    VALID_CHAT_STATUSES = ['REQUESTED', 'SEARCHING', 'SEARCHING_DRIVER', 'BOOKED', 'DRIVER_ASSIGNED', 'DRIVER_ARRIVING', 'ARRIVED', 'OTP_VERIFIED', 'STARTED', 'REACHED_DESTINATION', 'PAYMENT_REQUIRED', 'PAYMENT_CONFIRMED', 'ACCEPTED']
    READ_ONLY_STATUSES = ['COMPLETED', 'CANCELLED']

    async def connect(self):
        import logging
        logger = logging.getLogger('rides.consumers')
        
        self.ride_id = self.scope['url_route']['kwargs']['ride_id']
        self.room_group_name = f'chat_{self.ride_id}'
        self.user = self.scope.get("user")

        logger.info(f"[WS] Connect start: ride={self.ride_id}, user={self.user}")

        # Auth check
        if not self.user or not self.user.is_authenticated:
            logger.warning(f"[WS] Auth failed: ride={self.ride_id}, user={self.user}")
            await self.close()
            return

        # Access check
        if not await self.check_ride_access():
            logger.warning(f"[WS] Access denied: ride={self.ride_id}, user={self.user.id}")
            await self.close()
            return

        # Determine role (passenger or rider/driver)
        self.role = await self.get_user_role()
        if not self.role:
            logger.warning(f"[WS] Role determination failed: ride={self.ride_id}")
            await self.close()
            return

        logger.info(f"[WS] Connecting accepted: ride={self.ride_id}, role={self.role}")
        
        # Join chat group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()
        logger.info(f"[WS] Connection established: ride={self.ride_id}")

        # Send initial chat history
        await self.send_chat_history()

    async def disconnect(self, close_code):
        # Leave chat group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if action == 'send_message':
                await self.handle_send_message(data)
            elif action == 'mark_read':
                await self.handle_mark_read(data)
            elif action == 'typing':
                await self.handle_typing(data)

        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def handle_send_message(self, data):
        """Handle sending a new message with rate limiting and status checks."""
        # Check rate limit
        if not await self.check_rate_limit():
            await self.send(text_data=json.dumps({
                'type': 'error',
                'code': 'RATE_LIMITED',
                'message': 'Too many messages. Please wait a moment.'
            }))
            return

        # Check if ride allows sending messages (Use UPPERCASE comparison)
        ride_status = await self.get_ride_status()
        if ride_status in self.READ_ONLY_STATUSES:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'code': 'RIDE_ENDED',
                'message': 'Chat is read-only for this ride.'
            }))
            return

        if ride_status not in self.VALID_CHAT_STATUSES:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'code': 'CHAT_NOT_AVAILABLE',
                'message': 'Chat is not available for this ride status.'
            }))
            return

        message_text = data.get('message', '').strip()
        message_type = data.get('message_type', 'TEXT')

        # Validate message
        if not message_text:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Message cannot be empty'
            }))
            return

        if len(message_text) > 500:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Message too long (max 500 characters)'
            }))
            return

        # Validate quick message type
        if message_type == 'QUICK':
            valid_quick_messages = [
                "Where are you?",
                "I am at pickup point",
                "Please come fast",
                "I will be 2 minutes late",
                "I have arrived",
                "I am nearby",
                "Stuck in traffic",
                "Please come to pickup point",
            ]
            if message_text not in valid_quick_messages:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Invalid quick message'
                }))
                return

        # Save message to database
        message = await self.save_message(message_text, message_type)

        if message:
            # Persist + broadcast notification for the other party.
            await self.create_chat_notification_for_other_party(message)
            # Broadcast to all in the chat room
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'message': message,
                }
            )
        else:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Failed to send message or unauthorized'
            }))

    async def handle_mark_read(self, data):
        """Handle marking messages as read and broadcast to others."""
        message_ids = data.get('message_ids', [])
        if message_ids:
            updated_count = await self.mark_messages_read(message_ids)
            if updated_count > 0:
                # Broadcast to others so they can show "Read" status
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'messages_read',
                        'message_ids': message_ids,
                        'reader_role': self.role
                    }
                )

    async def handle_typing(self, data):
        """Handle typing indicator."""
        is_typing = data.get('typing', False)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing_indicator',
                'role': self.role,
                'typing': is_typing,
            }
        )

    async def chat_message(self, event):
        """Send chat message to WebSocket. Safely copies event data."""
        # Use copy to avoid modifying event for other consumers
        message = event['message'].copy()
        # Add is_mine flag for the recipient
        message['is_mine'] = message.get('sender_role') == self.role
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'message': message
        }))

    async def messages_read(self, event):
        """Notify client that messages have been read by the other party."""
        # Only notify if the reader was NOT the current user
        if event['reader_role'] != self.role:
            await self.send(text_data=json.dumps({
                'type': 'messages_marked_read',
                'message_ids': event['message_ids']
            }))

    async def typing_indicator(self, event):
        """Send typing indicator to WebSocket."""
        # Don't send to the user who is typing
        if event['role'] != self.role:
            await self.send(text_data=json.dumps({
                'type': 'typing',
                'role': event['role'],
                'typing': event['typing']
            }))

    async def user_joined(self, event):
        """Notify when user joins (for UI updates)."""
        if event['role'] != self.role:
            await self.send(text_data=json.dumps({
                'type': 'user_joined',
                'role': event['role'],
                'ride_status': event.get('ride_status')
            }))

    async def send_chat_history(self):
        """Send chat history to connected user."""
        messages = await self.get_chat_history()
        ride_status = await self.get_ride_status()

        await self.send(text_data=json.dumps({
            'type': 'chat_history',
            'messages': messages,
            'ride_status': ride_status,
            'can_send': ride_status in self.VALID_CHAT_STATUSES,
            'read_only': ride_status in self.READ_ONLY_STATUSES,
        }))

    @database_sync_to_async
    def check_ride_access(self):
        """Verify user is part of this ride."""
        from rides.models import Ride
        if not self.user.is_authenticated:
            return False
        try:
            ride = Ride.objects.get(id=self.ride_id)
            return ride.passenger == self.user or (ride.driver and ride.driver.user == self.user)
        except Ride.DoesNotExist:
            return False

    @database_sync_to_async
    def get_user_role(self):
        """Determine if user is passenger or rider."""
        from rides.models import Ride
        try:
            ride = Ride.objects.get(id=self.ride_id)
            if ride.passenger == self.user:
                return 'PASSENGER'
            elif ride.driver and ride.driver.user == self.user:
                return 'RIDER'
            return None
        except Ride.DoesNotExist:
            return None

    @database_sync_to_async
    def get_ride_status(self):
        """Get current ride status (UPPERCASE for comparison)."""
        from rides.models import Ride
        try:
            ride = Ride.objects.get(id=self.ride_id)
            return ride.status.upper() if ride.status else None
        except Ride.DoesNotExist:
            return None

    @database_sync_to_async
    def save_message(self, message_text, message_type):
        """Save message to database with authorization check."""
        from rides.models import ChatMessage, Ride
        try:
            ride = Ride.objects.get(id=self.ride_id)

            # Security: ensure sender is actually part of the ride
            if self.role == 'PASSENGER' and ride.passenger != self.user:
                return None
            if self.role == 'RIDER' and (not ride.driver or ride.driver.user != self.user):
                return None

            # Get sender name (first name only for privacy)
            sender_name = self.user.name.strip().split()[0] if self.user.name and self.user.name.strip() else 'User'

            message = ChatMessage.objects.create(
                ride=ride,
                sender_user=self.user,
                sender_role=self.role,
                sender_name=sender_name,
                message_text=message_text,
                message_type=message_type,
            )
            return message.to_dict(for_recipient_role=self.role)
        except Exception as e:
            import logging
            logger = logging.getLogger('rides.consumers')
            logger.error(f"Error saving chat message: {e}")
            return None

    @database_sync_to_async
    def get_chat_history(self, limit=50):
        """Get chat history for this ride."""
        from rides.models import ChatMessage
        messages = ChatMessage.objects.filter(ride_id=self.ride_id)[:limit]
        return [msg.to_dict(for_recipient_role=self.role) for msg in reversed(messages)]

    @database_sync_to_async
    def mark_messages_read(self, message_ids):
        """Mark messages as read and return count."""
        from rides.models import ChatMessage
        # Only mark messages from the OTHER party as read
        return ChatMessage.objects.filter(
            id__in=message_ids,
            ride_id=self.ride_id
        ).exclude(
            sender_role=self.role  # Exclude messages from current user
        ).update(is_read=True)

    @database_sync_to_async
    def create_chat_notification_for_other_party(self, message_dict):
        """Create a persisted user notification for the opposite chat participant."""
        from rides.models import Ride, Notification
        from rides.services.notification_center import NotificationCenter

        try:
            ride = Ride.objects.select_related('passenger', 'driver', 'driver__user').get(id=self.ride_id)
        except Ride.DoesNotExist:
            return

        receiver = None
        sender_label = message_dict.get('sender_name') or 'User'
        if self.role == 'PASSENGER':
            if ride.driver and ride.driver.user:
                receiver = ride.driver.user
        else:
            receiver = ride.passenger

        if not receiver:
            return

        NotificationCenter.create_and_broadcast(
            receiver,
            Notification.TYPE_CHAT_MESSAGE,
            f"New message from {sender_label}",
            data={
                "ride_id": str(self.ride_id),
                "chat_message_id": message_dict.get("id"),
                "chat_preview": (message_dict.get("message_text") or "")[:80],
                "sender_role": self.role,
            },
        )

    async def check_rate_limit(self):
        """Check if user is within rate limit using cache."""
        cache_key = f'chat_rate_limit:{self.user.id}:{self.ride_id}'
        current = cache.get(cache_key, 0)

        if current >= self.RATE_LIMIT_MAX:
            return False

        # Use cache.incr for atomic increment (works with django-redis)
        try:
            # Try atomic increment first
            new_value = cache.incr(cache_key)
            if new_value == 1:
                # First increment, set expiry
                cache.expire(cache_key, self.RATE_LIMIT_WINDOW)
        except ValueError:
            # Key doesn't exist, initialize it
            cache.set(cache_key, 1, self.RATE_LIMIT_WINDOW)

        return True


class DriverLocationConsumer(AsyncWebsocketConsumer):
    """
    Consumer for real-time driver location updates.
    Driver sends location every 2 seconds while online.
    """
    
    async def connect(self):
        """Authenticate driver and join their location group."""
        self.user = self.scope["user"]
        
        # Verify user is authenticated and is a driver
        if not self.user.is_authenticated or self.user.role != 'driver':
            await self.close()
            return
        
        self.driver_id = self.user.id
        self.room_group_name = f"driver_{self.driver_id}"
        
        # Join driver location group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        logger.info(f"[DriverLocation] Driver {self.driver_id} connected")
        
        # Send confirmation
        await self.send(text_data=json.dumps({
            'type': 'connected',
            'driver_id': self.driver_id
        }))
    
    async def disconnect(self, close_code):
        """Mark driver offline when WebSocket disconnects."""
        from drivers.models import Driver
        
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
        
        # Mark driver offline if they disconnect
        try:
            driver = await database_sync_to_async(Driver.objects.get)(user_id=self.driver_id)
            if driver.is_online:
                driver.is_online = False
                driver.current_lat = None
                driver.current_lng = None
                await database_sync_to_async(driver.save)()
                logger.info(f"[DriverLocation] Driver {self.driver_id} marked offline on disconnect")
                
                # Send notification to driver about being offline
                await self.send_driver_notification(
                    self.driver_id,
                    "You went offline due to connection loss. Tap 'Go Online' to start receiving rides again."
                )
        except Exception as e:
            logger.error(f"[DriverLocation] Error marking driver offline: {e}")
    
    async def receive(self, text_data):
        """Handle location updates from driver."""
        from drivers.models import Driver
        
        try:
            data = json.loads(text_data)
            message_type = data.get('type', 'location_update')
            
            if message_type == 'location_update':
                lat = data.get('latitude')
                lng = data.get('longitude')
                heading = data.get('heading')
                
                # Validate coordinates
                if not self._is_valid_coordinate(lat, lng):
                    logger.warning(f"[DriverLocation] Invalid coordinates from driver {self.driver_id}: {lat}, {lng}")
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Invalid coordinates'
                    }))
                    return
                
                # Update driver location in database
                try:
                    driver = await database_sync_to_async(Driver.objects.get)(user_id=self.driver_id)
                    driver.current_lat = float(lat)
                    driver.current_lng = float(lng)
                    if heading is not None:
                        driver.heading = float(heading)
                    
                    # Ensure driver is marked online
                    if not driver.is_online:
                        driver.is_online = True
                    
                    await database_sync_to_async(driver.save)()
                    
                    # Broadcast location to any passengers watching this driver
                    await self.broadcast_location_to_passengers(lat, lng, heading)
                    
                    # Send confirmation
                    await self.send(text_data=json.dumps({
                        'type': 'location_confirmed',
                        'timestamp': datetime.now().isoformat()
                    }))
                    
                except Driver.DoesNotExist:
                    logger.error(f"[DriverLocation] Driver {self.driver_id} not found")
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': 'Driver not found'
                    }))
                    
            elif message_type == 'ping':
                # Keep-alive ping
                await self.send(text_data=json.dumps({'type': 'pong'}))
                
            elif message_type == 'manual_offline':
                # Driver manually went offline via button
                try:
                    driver = await database_sync_to_async(Driver.objects.get)(user_id=self.driver_id)
                    driver.is_online = False
                    driver.current_lat = None
                    driver.current_lng = None
                    await database_sync_to_async(driver.save)()
                    logger.info(f"[DriverLocation] Driver {self.driver_id} went offline manually")
                    await self.send(text_data=json.dumps({
                        'type': 'offline_confirmed'
                    }))
                except Exception as e:
                    logger.error(f"[DriverLocation] Error handling manual offline: {e}")
                    
        except json.JSONDecodeError:
            logger.error("[DriverLocation] Invalid JSON received")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON'
            }))
    
    async def broadcast_location_to_passengers(self, lat, lng, heading):
        """Broadcast driver location to passengers in active rides."""
        from rides.models import Ride
        
        try:
            # Find active rides for this driver
            active_rides = await database_sync_to_async(list)(
                Ride.objects.filter(
                    driver_id=self.driver_id,
                    status__in=[
                        Ride.STATUS_DRIVER_ASSIGNED,
                        Ride.STATUS_ARRIVED,
                        Ride.STATUS_OTP_VERIFIED,
                        Ride.STATUS_STARTED
                    ]
                )
            )
            
            for ride in active_rides:
                await self.channel_layer.group_send(
                    f"ride_{ride.id}",
                    {
                        'type': 'location_update',
                        'driver_id': self.driver_id,
                        'latitude': lat,
                        'longitude': lng,
                        'heading': heading
                    }
                )
        except Exception as e:
            logger.error(f"[DriverLocation] Error broadcasting location: {e}")
    
    async def send_driver_notification(self, driver_id, message):
        """Send push notification to driver about being offline."""
        # This would integrate with your notification service
        # For now, we log it
        logger.info(f"[DriverLocation] Notification to driver {driver_id}: {message}")
    
    def _is_valid_coordinate(self, lat, lng):
        """Validate coordinates to prevent invalid updates."""
        if lat is None or lng is None:
            return False
        try:
            lat_f = float(lat)
            lng_f = float(lng)
            if lat_f == 0 and lng_f == 0:
                return False
            if not (-90 <= lat_f <= 90):
                return False
            if not (-180 <= lng_f <= 180):
                return False
            return True
        except (TypeError, ValueError):
            return False


class DriverStatsConsumer(AsyncWebsocketConsumer):
    """
    Consumer for real-time driver stats updates.
    Replaces polling every 30 seconds with WebSocket push updates.
    """

    async def connect(self):
        user = self.scope["user"]

        # Verify user is an authenticated driver
        @database_sync_to_async
        def check_driver_auth(user):
            if not user.is_authenticated:
                return None
            if hasattr(user, 'driver_profile'):
                return user.driver_profile.id
            return None

        driver_id = await check_driver_auth(user)
        if not driver_id:
            await self.close()
            return

        self.driver_id = driver_id
        self.group_name = f'driver_stats_{driver_id}'

        # Join stats group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

        # Send initial stats immediately
        await self.send_current_stats()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    @database_sync_to_async
    def get_driver_stats(self):
        """Get current driver stats from database."""
        from drivers.models import Driver
        from rides.models import Ride
        from payments.models import DriverWallet
        from django.db.models import Avg, Count
        from django.utils import timezone

        try:
            driver = Driver.objects.get(id=self.driver_id)

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

            return {
                'earningsToday': float(today_earnings),
                'totalRides': total_rides,
                'walletBalance': float(wallet_balance),
                'rating': round(float(rating), 2) if rating else 0,
            }
        except Exception as e:
            import logging
            logger = logging.getLogger('rides.consumers')
            logger.error(f"Error getting driver stats: {e}")
            return {
                'earningsToday': 0,
                'totalRides': 0,
                'walletBalance': 0,
                'rating': 0,
            }

    async def send_current_stats(self):
        """Send current stats to the client."""
        stats = await self.get_driver_stats()
        await self.send(text_data=json.dumps({
            'type': 'stats_update',
            'stats': stats
        }))

    async def stats_update(self, event):
        """Receive stats update from channel layer."""
        await self.send(text_data=json.dumps({
            'type': 'stats_update',
            'stats': event.get('stats', {})
        }))
