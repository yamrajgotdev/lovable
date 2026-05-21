from django.core.exceptions import ValidationError


class RideStatus:
    REQUESTED = "requested"
    SEARCHING = "searching"
    ASSIGNED = "accepted"
    ARRIVING = "driver_arriving"
    ARRIVED = "driver_arrived"
    OTP_VERIFIED = "otp_verified"
    STARTED = "started"
    REACHED_DESTINATION = "reached_destination"
    PAYMENT_REQUIRED = "payment_required"
    PAYMENT_CONFIRMED = "payment_confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS = {
    'requested': ['searching', 'cancelled'],
    'searching': ['accepted', 'cancelled'],
    'accepted': ['driver_arriving', 'driver_arrived', 'cancelled'],
    'driver_arriving': ['driver_arrived', 'cancelled'],
    'driver_arrived': ['otp_verified', 'cancelled'],
    'otp_verified': ['started', 'cancelled'],
    'started': ['reached_destination', 'cancelled'],
    'reached_destination': ['payment_required', 'payment_confirmed', 'cancelled'],
    'payment_required': ['payment_confirmed', 'cancelled'],
    'payment_confirmed': ['completed', 'cancelled'],
    'completed': [],
    'cancelled': [],
}


def update_ride_status(ride, new_status: str):
    """
    Compatibility adapter for legacy callers.
    All updates are delegated to the canonical RideStateMachine.
    """
    from rides.services.state_machine import RideStateMachine

    success, message, updated_ride = RideStateMachine.transition(
        ride_id=ride.id,
        new_status=new_status,
        actor_type='system',
    )
    if not success:
        raise ValidationError(message)
    return updated_ride


def assign_nearest_driver(ride, drivers_queryset):
    drivers = drivers_queryset.filter(
        is_online=True,
        is_approved=True
    )
    if not drivers.exists():
        return None

    driver = drivers.first()
    ride.driver = driver
    update_ride_status(ride, RideStatus.ASSIGNED)
    return driver


def start_ride(ride):
    return update_ride_status(ride, RideStatus.STARTED)


def complete_ride(ride):
    return update_ride_status(ride, RideStatus.COMPLETED)


def cancel_ride(ride):
    return update_ride_status(ride, RideStatus.CANCELLED)


def is_valid_transition(current_status, new_status):
    allowed = ALLOWED_TRANSITIONS.get((current_status or '').lower(), [])
    return (new_status or '').lower() in allowed
