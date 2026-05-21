from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from rides.models import Notification


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            Notification.objects.filter(user=request.user)
            .values("id", "type", "message", "is_read", "timestamp")
            .order_by("-timestamp")[:30]
        )
        unread = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({
            "notifications": [
                {
                    "id": n["id"],
                    "type": n["type"],
                    "message": n["message"],
                    "is_read": n["is_read"],
                    "timestamp": n["timestamp"].isoformat(),
                }
                for n in qs
            ],
            "unread_count": unread,
        })


class NotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        notif_id = request.data.get("notification_id")
        if notif_id:
            Notification.objects.filter(user=request.user, id=notif_id).update(is_read=True)
        else:
            Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"success": True})
