"""
Celery configuration for background tasks.
"""
import os
from celery import Celery
from celery.signals import task_failure, task_success, task_retry
from django.conf import settings
import logging

logger = logging.getLogger('rides4u.celery')

# Set Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rideapp.settings')

# Create Celery app
app = Celery('rideapp')

# Load configuration from Django settings
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from installed apps
app.autodiscover_tasks()

# Celery configuration
app.conf.update(
    # Broker (Redis)
    broker_url=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/1'),
    result_backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/2'),
    
    # Serialization
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    
    # Task execution
    task_track_started=True,
    task_time_limit=300,  # 5 minutes hard limit
    task_soft_time_limit=240,  # 4 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    
    # Result backend
    result_expires=3600,  # Results expire after 1 hour
    result_extended=True,
    
    # Retry settings
    task_default_retry_delay=60,  # 1 minute
    task_max_retries=3,
    
    # Queue configuration
    task_default_queue='default',
    task_queues={
        'default': {'exchange': 'default', 'routing_key': 'default'},
        'payments': {'exchange': 'payments', 'routing_key': 'payments'},
        'wallet': {'exchange': 'wallet', 'routing_key': 'wallet'},
        'notifications': {'exchange': 'notifications', 'routing_key': 'notifications'},
        'cleanup': {'exchange': 'cleanup', 'routing_key': 'cleanup'},
        'backups': {'exchange': 'backups', 'routing_key': 'backups'},
    },
    task_routes={
        'payments.*': {'queue': 'payments'},
        'wallet.*': {'queue': 'wallet'},
        'notifications.*': {'queue': 'notifications'},
        'cleanup.*': {'queue': 'cleanup'},
        'backups.*': {'queue': 'backups'},
    },
    
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
)


# Task signal handlers
@task_failure.connect
def handle_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **extras):
    """Log task failures."""
    logger.error(f"Task {sender.name}[{task_id}] failed: {exception}")


@task_success.connect
def handle_task_success(sender, result, **kwargs):
    """Log task success."""
    logger.info(f"Task {sender.name} completed successfully")


@task_retry.connect
def handle_task_retry(sender, request, reason, einfo, **kwargs):
    """Log task retries."""
    logger.warning(f"Task {sender.name}[{request.id}] retrying: {reason}")
