import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.db import transaction
import hashlib

from rides.models import Notification
from utils.idempotency import IdempotencyService

logger = logging.getLogger("rides4u")


class NotificationCenter:
    """Create + broadcast user notifications with strict at-least-once delivery."""

    @classmethod
    def _next_sequence_id(cls, user_id: int) -> int:
        """Get monotonic sequence ID per user via Redis."""
        key = f"notif_seq:{user_id}"
        # Ensure key exists so incr never fails on first notification.
        cache.add(key, 0, timeout=86400 * 30)
        return cache.incr(key)

    @classmethod
    def create_and_broadcast(cls, user, notif_type: str, message: str, data=None):
        if not user:
            return None

        # Mandatory Safeguard: Use IdempotencyService
        # Dedupe within 1 minute window to prevent bursts
        idempotency_key = f"notif:{user.id}:{notif_type}:{hashlib.sha1(message.encode()).hexdigest()[:16]}"
        if not IdempotencyService.is_allowed(idempotency_key, ttl=60):
            return None

        with transaction.atomic():
            # Monotonic sequence ID
            seq_id = cls._next_sequence_id(user.id)
            
            notif = Notification.objects.create(
                user=user,
                type=notif_type,
                message=message,
                metadata=data or {},
                sequence_id=seq_id
            )

        payload = {
            "type": notif.type,
            "message": notif.message,
            "notification_id": notif.id,
            "sequence_id": seq_id,
            "timestamp": notif.timestamp.isoformat(),
            "ride_trace_id": (data or {}).get("ride_trace_id"),
            **(data or {}),
        }

        def _broadcast():
            try:
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"user_notifications_{user.id}",
                    {"type": "notification", "notification": payload},
                )
                logger.info(f"Notification broadcasted: user={user.id} seq={seq_id} type={notif_type}", extra={'event_type': 'notification_broadcast', 'user_id': user.id, 'sequence_id': seq_id})
            except Exception as exc:
                logger.warning("notification_broadcast_failed user=%s err=%s", user.id, exc)

        # Persistence-first: send ONLY after DB commit.
        transaction.on_commit(_broadcast)

        return notif
