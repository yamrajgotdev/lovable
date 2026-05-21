"""
================================================================================
CHAT SYSTEM - REST API Views
ADDED: API endpoints for chat history and fallback polling
================================================================================

Endpoints:
- GET  /rides/<ride_id>/chat/messages/     - Get chat history
- POST /rides/<ride_id>/chat/send/         - Send message (REST fallback)
- POST /rides/<ride_id>/chat/mark-read/    - Mark messages as read
"""

import logging
from django.db import transaction
from django.core.cache import cache
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from authsystem.views import get_authenticated_user
from .models import ChatMessage, Ride

logger = logging.getLogger('rides4u')


class ChatMessagesView(APIView):
    """
    GET /rides/<ride_id>/chat/messages/
    Returns chat history for the ride. Only accessible to ride participants.
    """
    def get(self, request, ride_id):
        user = get_authenticated_user(request)
        if not user:
            return Response(
                {"success": False, "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {"success": False, "message": "Ride not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Verify user is part of this ride
        is_passenger = ride.passenger == user
        is_driver = ride.driver and ride.driver.user == user

        if not is_passenger and not is_driver:
            return Response(
                {"success": False, "message": "You don't have access to this ride's chat"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Determine role
        user_role = 'PASSENGER' if is_passenger else 'RIDER'

        # Get messages (newest first, limit 100)
        messages = ChatMessage.objects.filter(ride=ride).order_by('-created_at')[:100]

        # Mark unread messages from the OTHER party as read
        unread_ids = [m.id for m in messages if not m.is_read and m.sender_role != user_role]
        if unread_ids:
            ChatMessage.objects.filter(
                id__in=unread_ids,
                ride=ride
            ).update(is_read=True)

            # Broadcast to WebSocket group
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f'chat_{ride_id}',
                    {
                        'type': 'messages_read',
                        'message_ids': unread_ids,
                        'reader_role': user_role
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to broadcast mark_read from GET: {e}")

        # Serialize messages (oldest first for display)
        serialized = [msg.to_dict(for_recipient_role=user_role) for msg in reversed(messages)]

        # Determine if chat is enabled (from booking until ride ends)
        # Database status values (uppercase as stored in DB)
        # Note: Must match VALID_CHAT_STATUSES in ChatConsumer (rides/consumers.py)
        valid_chat_statuses = ['REQUESTED', 'SEARCHING', 'SEARCHING_DRIVER', 'BOOKED', 'DRIVER_ASSIGNED', 'DRIVER_ARRIVING', 'ARRIVED', 'OTP_VERIFIED', 'STARTED', 'REACHED_DESTINATION', 'PAYMENT_REQUIRED', 'PAYMENT_CONFIRMED', 'ACCEPTED']
        read_only_statuses = ['COMPLETED', 'CANCELLED']

        # Ensure comparison is case-insensitive
        current_status = ride.status.upper() if ride.status else ""

        return Response({
            "success": True,
            "messages": serialized,
            "ride_status": ride.status.lower() if ride.status else None,
            "can_send": current_status in valid_chat_statuses,
            "read_only": current_status in read_only_statuses,
            "unread_count": 0,  # We just marked them as read
        })


class SendMessageView(APIView):
    """
    POST /rides/<ride_id>/chat/send/
    Send a message via REST API (fallback when WebSocket unavailable).
    Includes rate limiting.
    """

    # Rate limiting: max 20 messages per minute per user per ride
    RATE_LIMIT_MAX = 20
    RATE_LIMIT_WINDOW = 60

    # Valid ride statuses for sending messages (from booking until ride ends)
    # Database status values (uppercase as stored in DB)
    # Note: Must match VALID_CHAT_STATUSES in ChatConsumer (rides/consumers.py)
    VALID_CHAT_STATUSES = ['REQUESTED', 'SEARCHING', 'SEARCHING_DRIVER', 'BOOKED', 'DRIVER_ASSIGNED', 'DRIVER_ARRIVING', 'ARRIVED', 'OTP_VERIFIED', 'STARTED', 'REACHED_DESTINATION', 'PAYMENT_REQUIRED', 'PAYMENT_CONFIRMED', 'ACCEPTED']
    READ_ONLY_STATUSES = ['COMPLETED', 'CANCELLED']

    def post(self, request, ride_id):
        user = get_authenticated_user(request)
        if not user:
            return Response(
                {"success": False, "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {"success": False, "message": "Ride not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Verify user is part of this ride
        is_passenger = ride.passenger == user
        is_driver = ride.driver and ride.driver.user == user

        if not is_passenger and not is_driver:
            return Response(
                {"success": False, "message": "You don't have access to this ride's chat"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check rate limit
        cache_key = f'chat_api_rate_limit:{user.id}:{ride_id}'
        current_count = cache.get(cache_key, 0)
        if current_count >= self.RATE_LIMIT_MAX:
            return Response(
                {
                    "success": False,
                    "code": "RATE_LIMITED",
                    "message": "Too many messages. Please wait a moment."
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # Check ride status
        current_status = ride.status.upper() if ride.status else ""
        if current_status in self.READ_ONLY_STATUSES:
            return Response(
                {
                    "success": False,
                    "code": "RIDE_ENDED",
                    "message": "Chat is read-only for this ride."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        if current_status not in self.VALID_CHAT_STATUSES:
            return Response(
                {
                    "success": False,
                    "code": "CHAT_NOT_AVAILABLE",
                    "message": "Chat is not available for this ride status."
                },
                status=status.HTTP_403_FORBIDDEN
            )

        # Get message data
        message_text = request.data.get('message', '').strip()
        message_type = request.data.get('message_type', 'TEXT')

        # Validate message
        if not message_text:
            return Response(
                {"success": False, "message": "Message cannot be empty"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(message_text) > 500:
            return Response(
                {"success": False, "message": "Message too long (max 500 characters)"},
                status=status.HTTP_400_BAD_REQUEST
            )

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
                return Response(
                    {"success": False, "message": "Invalid quick message"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Determine role and sender name
        user_role = 'PASSENGER' if is_passenger else 'RIDER'
        sender_name = user.name.strip().split()[0] if user.name and user.name.strip() else 'User'

        try:
            # Create message
            message = ChatMessage.objects.create(
                ride=ride,
                sender_user=user,
                sender_role=user_role,
                sender_name=sender_name,
                message_text=message_text,
                message_type=message_type,
            )

            # Update rate limit counter
            cache.set(cache_key, current_count + 1, self.RATE_LIMIT_WINDOW)

            # Broadcast to WebSocket group
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f'chat_{ride_id}',
                    {
                        'type': 'chat_message',
                        'message': message.to_dict(for_recipient_role=user_role),
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to broadcast chat_message: {e}")

            # Persist + push a user notification to the other participant.
            try:
                from rides.services.notification_center import NotificationCenter
                from rides.models import Notification

                receiver = None
                if user_role == 'PASSENGER':
                    if ride.driver and ride.driver.user:
                        receiver = ride.driver.user
                else:
                    receiver = ride.passenger

                if receiver:
                    NotificationCenter.create_and_broadcast(
                        receiver,
                        Notification.TYPE_CHAT_MESSAGE,
                        f"New message from {sender_name}",
                        data={
                            "ride_id": str(ride_id),
                            "chat_message_id": message.id,
                            "chat_preview": message_text[:80],
                            "sender_role": user_role,
                        },
                    )
            except Exception as e:
                logger.warning(f"Failed to create chat notification: {e}")

            return Response({
                "success": True,
                "message": message.to_dict(for_recipient_role=user_role),
            })

        except Exception as e:
            logger.error(f"Failed to save chat message: {e}")
            return Response(
                {"success": False, "message": "Failed to send message"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MarkReadView(APIView):
    """
    POST /rides/<ride_id>/chat/mark-read/
    Mark specific messages as read.
    """
    def post(self, request, ride_id):
        user = get_authenticated_user(request)
        if not user:
            return Response(
                {"success": False, "message": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            ride = Ride.objects.get(id=ride_id)
        except Ride.DoesNotExist:
            return Response(
                {"success": False, "message": "Ride not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Verify user is part of this ride
        is_passenger = ride.passenger == user
        is_driver = ride.driver and ride.driver.user == user

        if not is_passenger and not is_driver:
            return Response(
                {"success": False, "message": "You don't have access to this ride's chat"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Determine role
        user_role = 'PASSENGER' if is_passenger else 'RIDER'

        # Get message IDs to mark as read
        message_ids = request.data.get('message_ids', [])
        if not message_ids:
            return Response(
                {"success": True, "marked_count": 0},
                status=status.HTTP_200_OK
            )

        # Mark messages from the OTHER party as read
        updated_count = ChatMessage.objects.filter(
            id__in=message_ids,
            ride=ride
        ).exclude(
            sender_role=user_role
        ).update(is_read=True)

        if updated_count > 0:
            # Broadcast to WebSocket group so the other party sees "Read" status
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f'chat_{ride_id}',
                    {
                        'type': 'messages_read',
                        'message_ids': message_ids,
                        'reader_role': user_role
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to broadcast mark_read: {e}")

        return Response({
            "success": True,
            "marked_count": updated_count,
        })
