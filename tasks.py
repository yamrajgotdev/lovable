"""
Celery Tasks for Ride Dispatch and Background Operations
"""
import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

from rides.models import Ride
from rides.dispatch_service import DispatchService, SequentialDispatchTask
from drivers.state_machine import DriverStateMachine, DriverState, HeartbeatMonitor
from drivers.rate_limiter import DriverLocationRateLimiter

logger = logging.getLogger('rides.tasks')


@shared_task
def auto_offline_stale_drivers():
    """
    Find drivers who haven't sent heartbeat for > 35 seconds and mark them TEMP_OFFLINE.
    After 30s grace, they go OFFLINE.
    """
    try:
        from drivers.services.tracking_service import DriverTrackingService
        
        # Use the tracking service to check stale locations
        changes = DriverTrackingService.check_stale_locations()
        
        temp_offline_count = sum(1 for c in changes if c['new_status'] == 'TEMP_OFFLINE')
        offline_count = sum(1 for c in changes if c['new_status'] == 'OFFLINE')
        
        if temp_offline_count > 0 or offline_count > 0:
            logger.info(f"Auto-offline: {temp_offline_count} TEMP_OFFLINE, {offline_count} OFFLINE")
            
        return {'status': 'success', 'temp_offline': temp_offline_count, 'offline': offline_count}
    except Exception as e:
        logger.error(f"Auto-offline task failed: {e}")
        return {'status': 'error', 'message': str(e)}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def fetch_driver_to_pickup_route(self, ride_id, driver_lat, driver_lng, pickup_lat, pickup_lng):
    """
    Fetch route from driver location to pickup and save polyline.
    """
    try:
        from utils.ola_maps import ola_maps
        from rides.models import Ride
        
        route = ola_maps.get_route(driver_lat, driver_lng, pickup_lat, pickup_lng)
        if route:
            Ride.objects.filter(id=ride_id).update(
                driver_to_pickup_polyline=route.get("geometry", "")
            )
            return {'status': 'success', 'ride_id': ride_id}
        return {'status': 'no_route', 'ride_id': ride_id}
    except Exception as exc:
        logger.error(f"Error fetching route for ride {ride_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task
def notify_ride_accepted(ride_id, driver_id, passenger_id):
    """Notify passenger when ride is accepted."""
    from rides.models import Ride
    ride = Ride.objects.get(id=ride_id)
    send_ride_notification.delay(
        passenger_id,
        "Ride Accepted",
        f"Driver {ride.driver.name} is on the way!"
    )

@shared_task
def notify_ride_arrived(ride_id, passenger_id):
    """Notify passenger when driver arrives."""
    send_ride_notification.delay(
        passenger_id,
        "Driver Arrived",
        "Your driver has arrived at the pickup location."
    )

@shared_task
def notify_ride_started(ride_id, passenger_id):
    """Notify passenger when ride starts."""
    send_ride_notification.delay(
        passenger_id,
        "Ride Started",
        "Your ride is in progress."
    )

@shared_task
def notify_ride_completed(ride_id, passenger_id, amount):
    """Notify passenger when ride is completed."""
    send_ride_notification.delay(
        passenger_id,
        "Ride Completed",
        f"Your ride is complete. Total fare: ₹{amount}"
    )

@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def send_ride_notification(self, user_id, title, body, data=None):
    """
    Send push notification to a user via FCM.
    """
    try:
        from utils.notifications import NotificationService
        NotificationService.send_to_user(user_id, title, body, data)
    except Exception as exc:
        logger.error(f"Failed to send notification to user {user_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def notify_driver_batch(self, ride_id: int, driver_ids: list, batch_index: int = 0):
    """
    Notify a batch of drivers in parallel.
    Wait for response, then proceed to next batch if no acceptance.
    """
    try:
        # Check if ride already accepted
        if DispatchService.is_ride_accepted(ride_id):
            logger.info(f"Ride {ride_id} already accepted, skipping batch {batch_index}")
            return {'status': 'skipped', 'reason': 'already_accepted'}

        ride = Ride.objects.get(id=ride_id)
        
        # Store current batch index in Redis for coordination
        from rideapp.redis_utils import GracefulCache
        GracefulCache.set(f"ride:{ride_id}:current_batch", batch_index, timeout=600)

        notified_count = 0
        for driver_id in driver_ids:
            if DispatchService.notify_driver(ride, driver_id):
                notified_count += 1
                # Schedule response check for each driver in batch
                check_driver_response.apply_async(
                    args=[ride_id, driver_id],
                    countdown=10  # 10 second acceptance window for faster matching
                )

        if notified_count == 0:
            # All drivers in batch were unavailable, move to next batch immediately
            proceed_to_next_batch(ride_id, batch_index)
            return {'status': 'failed', 'reason': 'all_unavailable'}

        return {'status': 'batch_notified', 'count': notified_count, 'batch': batch_index}

    except Ride.DoesNotExist:
        return {'status': 'error', 'reason': 'ride_not_found'}
    except Exception as exc:
        logger.exception(f"Notify driver batch failed: {exc}")
        raise self.retry(exc=exc)

def proceed_to_next_batch(ride_id, current_batch_index):
    """Utility to trigger the next batch of drivers."""
    from rideapp.redis_utils import GracefulCache
    
    # Use a lock to ensure we only trigger the next batch ONCE
    lock_key = f"ride:{ride_id}:batch_trigger_lock:{current_batch_index + 1}"
    from django_redis import get_redis_connection
    redis = get_redis_connection("default")
    if not redis.set(lock_key, "triggered", ex=60, nx=True):
        return # Already triggered by another driver's timeout

    queue_key = DispatchService._get_dispatch_queue_key(ride_id)
    queue_data = GracefulCache.get(queue_key)
    
    if not queue_data:
        return
        
    driver_ids = queue_data.get('driver_ids', [])
    batch_size = 2
    next_index = (current_batch_index + 1) * batch_size
    
    if next_index < len(driver_ids):
        next_batch = driver_ids[next_index : next_index + batch_size]
        notify_driver_batch.delay(ride_id, next_batch, batch_index=current_batch_index + 1)
    else:
        # End of queue
        handle_no_driver_acceptance.delay(ride_id)

@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def notify_next_driver(self, ride_id: int, driver_id: int):
    """
    Notify next driver in dispatch queue.
    Wait for response, then proceed to next driver if no acceptance.
    """
    try:
        # Check if ride already accepted
        if DispatchService.is_ride_accepted(ride_id):
            logger.info(f"Ride {ride_id} already accepted, skipping notification")
            return {'status': 'skipped', 'reason': 'already_accepted'}

        # Notify driver
        ride = Ride.objects.get(id=ride_id)
        if not DispatchService.notify_driver(ride, driver_id):
            # Driver unavailable, proceed to next immediately
            return {'status': 'failed', 'reason': 'driver_unavailable', 'driver_id': driver_id}

        # Wait for acceptance (simulate by checking after delay)
        # In production, this would be triggered by driver WebSocket response
        check_driver_response.apply_async(
            args=[ride_id, driver_id],
            countdown=15  # 15 second acceptance window
        )

        return {'status': 'notified', 'driver_id': driver_id}

    except Ride.DoesNotExist:
        logger.error(f"Ride {ride_id} not found")
        return {'status': 'error', 'reason': 'ride_not_found'}
    except Exception as exc:
        logger.exception(f"Notify driver task failed: {exc}")
        raise self.retry(exc=exc)


@shared_task
def check_driver_response(ride_id: int, driver_id: int):
    """
    Check if driver responded to dispatch notification.
    If not accepted, record rejection and continue dispatch.
    """
    try:
        # Check current state
        state = DriverStateMachine.get_state(driver_id)
        accepted_driver = DispatchService.is_ride_accepted(ride_id)

        if accepted_driver:
            # Ride was accepted (by someone)
            if accepted_driver == driver_id:
                return {'status': 'accepted', 'driver_id': driver_id}
            else:
                return {'status': 'accepted_by_other', 'driver_id': driver_id}

        if state == DriverState.DISPATCHED:
            # Driver didn't respond, mark as rejected and continue
            DispatchService.record_driver_rejection(ride_id, driver_id)
            DriverStateMachine.set_state(driver_id, DriverState.AVAILABLE)

            # Check if this was the last driver in the current batch
            # We use a Redis counter or just check if any other driver in the batch is still pending.
            # For simplicity, we trigger the next batch check.
            # A more robust way is to use a Batch Coordination key in Redis.
            
            # Trigger next batch if queue is not finished and no one accepted
            if not DispatchService.is_ride_accepted(ride_id):
                # We need to know which batch we are in. 
                # For now, we'll use a simplified check: if all drivers tried so far didn't accept, try next.
                # However, since check_driver_response is per-driver, we should only trigger next batch
                # once ALL drivers in the current batch have timed out or rejected.
                
                # Implementation detail: notify_driver_batch uses batch_index.
                # We can store the 'last_processed_batch' in Redis.
                from rideapp.redis_utils import GracefulCache
                batch_key = f"ride:{ride_id}:current_batch"
                current_batch = GracefulCache.get(batch_key) or 0
                
                # Only move to next batch if we haven't already
                # This is a bit complex without more state. 
                # Let's simplify: every timeout checks if it's time for next batch.
                proceed_to_next_batch(ride_id, current_batch)
                
            return {'status': 'timeout', 'driver_id': driver_id}

        return {'status': 'unknown', 'driver_id': driver_id, 'state': state.value}

    except Exception as exc:
        logger.exception(f"Check driver response failed: {exc}")
        return {'status': 'error', 'reason': str(exc)}


@shared_task
def handle_no_driver_acceptance(ride_id: int):
    """
    Handle case where no drivers accepted the ride.
    """
    try:
        ride = Ride.objects.get(id=ride_id, status=Ride.STATUS_SEARCHING_DRIVER)

        # Update ride status via state machine
        from rides.services.state_machine import RideStateMachine
        RideStateMachine.transition(
            ride_id=ride_id,
            new_status=Ride.STATUS_CANCELLED,
            actor_type='system',
            reason='no_drivers_available'
        )

        # Notify passenger (via WebSocket)
        # TODO: Implement WebSocket notification

        # Cleanup dispatch queue
        DispatchService.cancel_dispatch(ride_id)

        logger.warning(f"Ride {ride_id} - no drivers available")
        return {'status': 'no_drivers', 'ride_id': ride_id}

    except Ride.DoesNotExist:
        logger.info(f"Ride {ride_id} already assigned or cancelled")
        return {'status': 'already_resolved'}


@shared_task
def check_driver_heartbeats():
    """
    Periodic task to check all driver heartbeats.
    Mark stale drivers as offline.
    """
    try:
        # Get all drivers who should be active
        from drivers.models import Driver
        active_drivers = Driver.objects.filter(
            is_approved=True
        ).exclude(
            status=Driver.STATUS_OFFLINE
        ).values_list('id', flat=True)

        # Check heartbeats
        results = HeartbeatMonitor.check_all_drivers(list(active_drivers))

        # Handle stale drivers in rides
        for driver_id in results['in_ride_stale']:
            ride_id = DriverStateMachine.get_current_ride(driver_id)
            if ride_id:
                HeartbeatMonitor.handle_stale_in_ride_driver(driver_id, ride_id)

        logger.info(
            f"Heartbeat check complete: "
            f"marked_offline={len(results['offline_marked'])}, "
            f"stale_in_ride={len(results['in_ride_stale'])}"
        )

        return results

    except Exception as exc:
        logger.exception(f"Heartbeat check failed: {exc}")
        return {'error': str(exc)}


@shared_task
def cleanup_stale_dispatches():
    """
    Cleanup stale dispatch queues (older than 30 minutes).
    """
    try:
        from rides.models import Ride
        stale_rides = Ride.objects.filter(
            status='searching_driver',
            requested_at__lt=timezone.now() - timedelta(minutes=30)
        )

        count = 0
        for ride in stale_rides:
            DispatchService.cancel_dispatch(ride.id)
            from rides.services.state_machine import RideStateMachine
            RideStateMachine.transition(
                ride_id=ride.id,
                new_status=Ride.STATUS_CANCELLED,
                actor_type='system',
                reason='dispatch_timeout'
            )
            count += 1

        logger.info(f"Cleaned up {count} stale dispatches")
        return {'cleaned': count}

    except Exception as exc:
        logger.exception(f"Cleanup stale dispatches failed: {exc}")
        return {'error': str(exc)}


@shared_task
def reset_driver_rate_limits():
    """
    Periodic task to clean up expired rate limit entries.
    Rate limits expire naturally, but this ensures cleanup.
    """
    # Rate limits auto-expire via Redis TTL
    # This task can be used for any additional cleanup needed
    logger.debug("Rate limit cleanup completed")
    return {'status': 'cleaned'}


@shared_task
def monitor_dispatch_queues():
    """
    Monitor active dispatch queues and retry if stuck.
    """
    try:
        from rides.models import Ride

        # Find rides stuck in searching for > 5 minutes
        stuck_rides = Ride.objects.filter(
            status='searching_driver',
            requested_at__lt=timezone.now() - timedelta(minutes=5),
            requested_at__gt=timezone.now() - timedelta(minutes=30)  # But not too old
        )

        results = []
        for ride in stuck_rides:
            # Try to continue dispatch
            next_driver = DispatchService.get_next_driver_for_dispatch(ride.id)
            if next_driver:
                notify_next_driver.delay(ride.id, next_driver)
                results.append({'ride_id': ride.id, 'action': 'continued_dispatch'})
            else:
                # No more drivers in queue, need new search
                results.append({'ride_id': ride.id, 'action': 'needs_new_search'})

        logger.info(f"Monitored {len(stuck_rides)} stuck dispatch queues")
        return {'monitored': len(stuck_rides), 'actions': results}

    except Exception as exc:
        logger.exception(f"Dispatch queue monitoring failed: {exc}")
        return {'error': str(exc)}


@shared_task
def cleanup_idempotency_keys(batch_size: int = 1000):
    """
    Periodic cleanup of expired idempotency keys from PostgreSQL.
    Redis keys auto-expire via TTL, but DB entries need periodic cleanup.
    Removes keys older than 7 days in batches to prevent blocking queries.

    Args:
        batch_size: Number of rows to delete per batch (default: 1000)
    """
    try:
        from core.models import IdempotencyKey
        from datetime import timedelta

        cutoff = timezone.now() - timedelta(days=7)
        total_deleted = 0
        batches = 0

        # Batch deletion to prevent long-running blocking queries
        while True:
            # Get IDs to delete (limit to batch_size)
            ids_to_delete = list(
                IdempotencyKey.objects
                .filter(created_at__lt=cutoff)
                .values_list('id', flat=True)[:batch_size]
            )

            if not ids_to_delete:
                break

            # Delete this batch
            deleted, _ = IdempotencyKey.objects.filter(id__in=ids_to_delete).delete()
            total_deleted += deleted
            batches += 1

            logger.debug(f"[IDEMPOTENCY CLEANUP] Batch {batches}: deleted {deleted} keys")

            # If we got fewer than batch_size, we're done
            if len(ids_to_delete) < batch_size:
                break

        logger.info(f"[IDEMPOTENCY CLEANUP] Completed: {total_deleted} keys deleted in {batches} batches")
        return {'deleted': total_deleted, 'batches': batches}
    except Exception as e:
        logger.error(f"Idempotency cleanup failed: {e}")
        return {'error': str(e)}
