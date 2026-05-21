"""
Notification Service Layer
Handles push notifications via FCM.
"""
from utils.notifications import NotificationService

class NotificationServiceLayer:
    @staticmethod
    def notify_ride_accepted(ride):
        NotificationService.send_to_user(
            ride.passenger.id,
            "Ride Accepted",
            f"Driver {ride.driver.name} is on the way!"
        )
