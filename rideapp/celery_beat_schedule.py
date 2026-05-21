"""
Celery Beat schedule with only registered tasks.
"""
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "reconcile-pending-payments": {
        "task": "run_payment_reconciliation",
        "schedule": 300.0,
        "options": {"queue": "payments"},
    },
    "cleanup-idempotency-keys": {
        "task": "cleanup_idempotency_keys",
        "schedule": crontab(hour=1, minute=0),
        "options": {"queue": "cleanup"},
    },
    "check-driver-heartbeats": {
        "task": "rides.tasks.check_driver_heartbeats",
        "schedule": 30.0,
        "options": {"queue": "default"},
    },
    "auto-offline-stale-drivers": {
        "task": "rides.tasks.auto_offline_stale_drivers",
        "schedule": 30.0,
        "options": {"queue": "cleanup"},
    },
    "cleanup-stale-dispatches": {
        "task": "rides.tasks.cleanup_stale_dispatches",
        "schedule": 600.0,
        "options": {"queue": "cleanup"},
    },
    "monitor-dispatch-queues": {
        "task": "rides.tasks.monitor_dispatch_queues",
        "schedule": 300.0,
        "options": {"queue": "default"},
    },
}
