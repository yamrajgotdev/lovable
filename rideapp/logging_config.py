import os
import uuid
import time
import logging
import json
from datetime import datetime
from threading import local

_thread_locals = local()

class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = str(uuid.uuid4())
        # ride_trace_id can be passed in headers for correlation across services
        ride_trace_id = request.headers.get('X-Ride-Trace-ID', str(uuid.uuid4()))
        
        request.request_id = request_id
        request.ride_trace_id = ride_trace_id
        
        _thread_locals.request_id = request_id
        _thread_locals.ride_trace_id = ride_trace_id
        _thread_locals.user_id = getattr(request.user, 'id', None) if hasattr(request, 'user') else None
        
        response = self.get_response(request)
        response['X-Request-ID'] = request_id
        response['X-Ride-Trace-ID'] = ride_trace_id
        return response

class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.logger = logging.getLogger('rides4u.requests')

    def __call__(self, request):
        start_time = time.time()
        response = self.get_response(request)
        duration = (time.time() - start_time) * 1000
        
        # Log with extra context for JSON formatter
        self.logger.info(
            f"{request.method} {request.path} {response.status_code} ({duration:.2f}ms)",
            extra={
                'event_type': 'api_request',
                'method': request.method,
                'path': request.path,
                'status_code': response.status_code,
                'duration_ms': duration
            }
        )
        return response

class SlowQueryMiddleware:
    def __init__(self, get_response, threshold_ms: float = 1000.0):
        self.get_response = get_response
        self.threshold_ms = threshold_ms
        self.logger = logging.getLogger('rides4u.slow')

    def __call__(self, request):
        start = time.time()
        response = self.get_response(request)
        duration_ms = (time.time() - start) * 1000
        if duration_ms > self.threshold_ms:
            self.logger.warning(
                f"SLOW_REQUEST {request.method} {request.path} {duration_ms:.1f}ms",
                extra={
                    'event_type': 'slow_request',
                    'duration_ms': duration_ms
                }
            )
        return response

class JSONFormatter(logging.Formatter):
    @staticmethod
    def _safe_headers(request):
        try:
            headers = dict(getattr(request, "headers", {}) or {})
        except Exception:
            return {}
        redacted = {}
        for key, value in headers.items():
            key_lower = str(key).lower()
            if key_lower in {"authorization", "cookie", "set-cookie", "x-api-key"}:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = str(value)
        return redacted

    @classmethod
    def _safe_request(cls, request):
        user = getattr(request, "user", None)
        user_id = getattr(user, "id", None) if user is not None else None
        return {
            "path": getattr(request, "path", None),
            "method": getattr(request, "method", None),
            "user_id": user_id,
            "headers": cls._safe_headers(request),
        }

    @classmethod
    def _json_safe(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): cls._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(v) for v in value]
        if hasattr(value, "path") and hasattr(value, "method"):
            return cls._safe_request(value)
        return str(value)

    def format(self, record):
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'request_id': getattr(_thread_locals, 'request_id', None),
            'ride_trace_id': getattr(_thread_locals, 'ride_trace_id', None),
            'user_id': getattr(_thread_locals, 'user_id', None),
            'event_type': getattr(record, 'event_type', 'log'),
        }
        
        # Add any extra fields passed via 'extra' param
        if hasattr(record, 'ride_id'):
            log_data['ride_id'] = record.ride_id
        
        # Include fields from 'extra' that aren't already handled
        reserved_attrs = (
            'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
            'funcName', 'levelname', 'levelno', 'lineno', 'module',
            'msecs', 'msg', 'name', 'pathname', 'process', 'processName',
            'relativeCreated', 'stack_info', 'thread', 'threadName'
        )
        for key, value in record.__dict__.items():
            if key not in reserved_attrs and key not in log_data:
                if key == "request":
                    log_data[key] = self._safe_request(value)
                else:
                    log_data[key] = self._json_safe(value)

        return json.dumps(log_data, default=str)

def get_logging_config():
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'json': {
                '()': JSONFormatter,
            },
            'simple': {
                'format': '{levelname} {asctime} {module} {message}',
                'style': '{',
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'json' if os.environ.get('FORCE_JSON_LOGGING', 'true').lower() == 'true' else 'simple',
            },
        },
        'root': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        'loggers': {
            'django': {
                'handlers': ['console'],
                'level': 'INFO',
                'propagate': False,
            },
            'rides4u': {
                'handlers': ['console'],
                'level': 'DEBUG',
                'propagate': False,
            },
        },
    }
