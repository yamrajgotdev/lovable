"""
Database configuration for production.
Includes connection pooling, retry logic, and query timeout protection.
"""
import os
import logging
from functools import wraps
from django.db import connections, OperationalError, InterfaceError
from django.db.utils import DatabaseError
from time import sleep

logger = logging.getLogger('rides4u.db')

# Database retry configuration
DB_MAX_RETRIES = int(os.environ.get('DB_MAX_RETRIES', 3))
DB_RETRY_DELAY = float(os.environ.get('DB_RETRY_DELAY', 1.0))
DB_QUERY_TIMEOUT = int(os.environ.get('DB_QUERY_TIMEOUT', 30))


class DatabaseRetry:
    """Decorator for retrying database operations on transient failures."""
    
    def __init__(self, max_retries=DB_MAX_RETRIES, delay=DB_RETRY_DELAY, 
                 exceptions=(OperationalError, InterfaceError)):
        self.max_retries = max_retries
        self.delay = delay
        self.exceptions = exceptions
    
    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(self.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except self.exceptions as e:
                    last_exception = e
                    if attempt < self.max_retries:
                        logger.warning(
                            f"Database operation failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. Retrying..."
                        )
                        sleep(self.delay * (2 ** attempt))  # Exponential backoff
                        # Reset connection before retry
                        for conn in connections.all():
                            conn.close_if_unusable_or_obsolete()
                    else:
                        logger.error(f"Database operation failed after {self.max_retries + 1} attempts: {e}")
                        raise
            
            raise last_exception
        return wrapper


def get_database_config():
    """Generate production database configuration with pooling."""
    
    # Parse DATABASE_URL if provided
    database_url = os.environ.get('DATABASE_URL')
    
    if database_url:
        import dj_database_url
        config = dj_database_url.parse(database_url, conn_max_age=60)
    else:
        # Manual configuration
        config = {
            'ENGINE': os.environ.get('DB_ENGINE', 'django.db.backends.postgresql'),
            'NAME': os.environ.get('DB_NAME', 'rides4u'),
            'USER': os.environ.get('DB_USER', 'rides4u'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
        }
    
    # Add connection pooling and timeout settings
    config.update({
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', 60)),
        'CONN_HEALTH_CHECKS': True,
        'OPTIONS': {
            'connect_timeout': int(os.environ.get('DB_CONNECT_TIMEOUT', 10)),
            'options': f'-c statement_timeout={DB_QUERY_TIMEOUT * 1000}',  # PostgreSQL uses milliseconds
        }
    })
    
    # Add SSL configuration for production
    if os.environ.get('DB_SSL_MODE', 'prefer') != 'disable':
        ssl_mode = os.environ.get('DB_SSL_MODE', 'prefer')
        config['OPTIONS']['sslmode'] = ssl_mode
        
        if ssl_mode in ('verify-ca', 'verify-full'):
            ssl_root_cert = os.environ.get('DB_SSL_ROOT_CERT')
            ssl_cert = os.environ.get('DB_SSL_CERT')
            ssl_key = os.environ.get('DB_SSL_KEY')
            
            if ssl_root_cert:
                config['OPTIONS']['sslrootcert'] = ssl_root_cert
            if ssl_cert:
                config['OPTIONS']['sslcert'] = ssl_cert
            if ssl_key:
                config['OPTIONS']['sslkey'] = ssl_key
    
    return {'default': config}


def check_database_health():
    """Check if database connection is healthy."""
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return True, "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False, str(e)


# SQL for creating additional indexes
RIDE_INDEXES_SQL = """
-- Additional indexes for ride lookup optimization
CREATE INDEX IF NOT EXISTS idx_rides_status_created_at 
    ON rides (status, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_rides_driver_status 
    ON rides (driver_id, status) WHERE driver_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rides_passenger_status 
    ON rides (passenger_id, status);

CREATE INDEX IF NOT EXISTS idx_rides_searching_driver 
    ON rides (status, vehicle_type, requested_at) 
    WHERE status = 'searching_driver';

CREATE INDEX IF NOT EXISTS idx_rides_payment_required 
    ON rides (status, requested_at) 
    WHERE status = 'payment_required';

CREATE INDEX IF NOT EXISTS idx_rides_location 
    ON rides (pickup_lat, pickup_lng, status) 
    WHERE status = 'searching_driver';

-- Partial index for active rides
CREATE INDEX IF NOT EXISTS idx_rides_active 
    ON rides (passenger_id, driver_id, status) 
    WHERE status IN ('searching_driver', 'driver_assigned', 'driver_arriving', 'ride_started');

-- Driver location index
CREATE INDEX IF NOT EXISTS idx_drivers_online_location 
    ON drivers (is_online, current_lat, current_lng, last_location_update) 
    WHERE is_online = true;

-- Payment indexes
CREATE INDEX IF NOT EXISTS idx_payments_pending 
    ON payments (status, created_at) 
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_payments_reconciliation 
    ON payments (status, method, created_at, razorpay_order_id) 
    WHERE status = 'pending' AND method = 'razorpay_online';
"""


def create_optimization_indexes():
    """Create database indexes for query optimization."""
    from django.db import connection
    
    try:
        with connection.cursor() as cursor:
            cursor.execute(RIDE_INDEXES_SQL)
        logger.info("Database optimization indexes created successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to create optimization indexes: {e}")
        return False


class QueryTimeoutMiddleware:
    """Middleware to set query timeout for requests."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        from django.db import connection
        
        # Set statement timeout for this request
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = {DB_QUERY_TIMEOUT * 1000}")
        
        response = self.get_response(request)
        return response
