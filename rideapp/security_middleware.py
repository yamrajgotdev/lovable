"""
Security middleware for production hardening.
Adds CSP headers, secure proxy headers, and request protection.
"""
import os
import re
import json
from typing import Dict, List
from django.http import HttpResponseForbidden, HttpResponseBadRequest
from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """Add Content Security Policy headers."""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.csp_directives = self._build_csp()
    
    def _build_csp(self) -> Dict[str, List[str]]:
        """Build CSP directives."""
        directives = {
            'default-src': ["'self'"],
            'script-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'"],  # Required for some frontend frameworks
            'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
            'font-src': ["'self'", "https://fonts.gstatic.com"],
            'img-src': ["'self'", "data:", "https:", "blob:"],
            'connect-src': ["'self'", "https://api.olamaps.io", "wss:", "ws:"],
            'media-src': ["'self'"],
            'object-src': ["'none'"],
            'frame-ancestors': ["'none'"],
            'form-action': ["'self'"],
            'base-uri': ["'self'"],
            'upgrade-insecure-requests': [],
        }
        
        # Allow additional domains from settings
        if hasattr(settings, 'CSP_EXTRA_DOMAINS'):
            for domain in settings.CSP_EXTRA_DOMAINS:
                directives['connect-src'].append(domain)
        
        return directives
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Build CSP header
        csp_parts = []
        for directive, sources in self.csp_directives.items():
            if sources:
                csp_parts.append(f"{directive} {' '.join(sources)}")
            else:
                csp_parts.append(directive)
        
        response['Content-Security-Policy'] = '; '.join(csp_parts)
        return response


class SecureProxyMiddleware:
    """Handle secure headers from reverse proxy."""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.trusted_proxies = getattr(settings, 'TRUSTED_PROXIES', [])
    
    def __call__(self, request):
        # Get client IP considering proxy
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            # Get the leftmost IP (original client)
            ip = x_forwarded_for.split(',')[0].strip()
            request.META['REMOTE_ADDR'] = ip
        
        # Mark request as secure if forwarded proto is https
        x_forwarded_proto = request.META.get('HTTP_X_FORWARDED_PROTO', '')
        forwarded_proto = x_forwarded_proto.split(',')[0].strip().lower()
        if forwarded_proto == 'https':
            request.META['wsgi.url_scheme'] = 'https'
            request.is_secure = lambda: True
        
        response = self.get_response(request)
        
        # Add additional security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response['Permissions-Policy'] = 'geolocation=(self), microphone=(), camera=()'
        
        return response


class RequestSizeLimitMiddleware:
    """Limit request body size."""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.max_size = int(os.environ.get('MAX_REQUEST_SIZE_MB', 10)) * 1024 * 1024
    
    def __call__(self, request):
        content_length = request.META.get('CONTENT_LENGTH', 0)
        
        try:
            content_length = int(content_length)
        except (ValueError, TypeError):
            content_length = 0
        
        if content_length > self.max_size:
            return HttpResponseForbidden(
                json.dumps({'error': 'Request body too large'}),
                content_type='application/json'
            )
        
        return self.get_response(request)


class WebhookReplayProtectionMiddleware:
    """Prevent webhook replay attacks using nonce tracking."""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.webhook_paths = [
            '/api/v1/payments/webhook',
            '/api/payments/webhook',
            '/api/payments/razorpay/webhook',
            '/payments/razorpay/webhook',
        ]
    
    def __call__(self, request):
        normalized_path = (request.path or '').rstrip('/')
        if normalized_path in self.webhook_paths and request.method == 'POST':
            # Extract nonce/signature from request
            nonce = self._extract_nonce(request)
            
            if nonce:
                from rideapp.redis_utils import GracefulCache, CacheKeys
                
                cache_key = CacheKeys.webhook_nonce(nonce)
                
                # Check if nonce was already used
                if GracefulCache.get(cache_key):
                    # Do not block the request here. Let webhook view idempotency
                    # return a safe 200 already_processed response.
                    request.META['WEBHOOK_REPLAY_DETECTED'] = '1'
                    return self.get_response(request)
                
                # Store nonce with 24-hour expiration
                GracefulCache.set(cache_key, 'used', timeout=86400)
        
        return self.get_response(request)
    
    def _extract_nonce(self, request) -> str:
        """Extract unique identifier from webhook request."""
        # Try to get from various sources
        content_type = (request.content_type or '').lower()
        if 'application/json' in content_type:
            try:
                body = json.loads(request.body)
                # Look for Razorpay payment ID or order ID
                payload = body.get('payload', {})
                payment = payload.get('payment', {}).get('entity', {})
                return payment.get('id') or payment.get('order_id')
            except:
                pass
        
        # Fall back to signature
        return request.META.get('HTTP_X_RAZORPAY_SIGNATURE')


class BruteForceProtectionMiddleware:
    """Protect against brute force attacks on authentication endpoints."""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.protected_paths = ['/api/auth/', '/api/login', '/api/signup']
        self.max_attempts = int(os.environ.get('BRUTE_FORCE_MAX_ATTEMPTS', 5))
        self.block_duration = int(os.environ.get('BRUTE_FORCE_BLOCK_DURATION', 900))  # 15 minutes
    
    def __call__(self, request):
        # Check if path is protected
        is_protected = any(request.path.startswith(path) for path in self.protected_paths)

        if is_protected and request.method == 'POST':
            client_ip = self._get_client_ip(request)
            cache_key = f"brute_force:{client_ip}:{request.path}"

            from rideapp.redis_utils import GracefulCache

            # Try to increment - if key doesn't exist, it will be created with value 1
            try:
                attempts = GracefulCache.incr(cache_key)
                # Set expiry on first increment
                if attempts == 1:
                    GracefulCache.set(cache_key, 1, timeout=self.block_duration)
            except Exception:
                # Fallback: get and set manually
                attempts = GracefulCache.get(cache_key) or 0
                attempts += 1
                GracefulCache.set(cache_key, attempts, timeout=self.block_duration)

            if attempts >= self.max_attempts:
                return HttpResponseForbidden(
                    json.dumps({
                        'error': 'Too many attempts. Please try again later.',
                        'retry_after': self.block_duration
                    }),
                    content_type='application/json'
                )

        return self.get_response(request)
    
    def _get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class IPRestrictionMiddleware:
    """Restrict access to certain endpoints by IP."""
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.restricted_paths = getattr(settings, 'IP_RESTRICTED_PATHS', {})
        # Format: {'/admin/': ['10.0.0.0/8', '127.0.0.1']}
    
    def __call__(self, request):
        for path_prefix, allowed_ips in self.restricted_paths.items():
            if request.path.startswith(path_prefix):
                client_ip = self._get_client_ip(request)
                
                if not self._ip_in_allowed_list(client_ip, allowed_ips):
                    return HttpResponseForbidden(
                        json.dumps({'error': 'Access denied'}),
                        content_type='application/json'
                    )
        
        return self.get_response(request)
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
    
    def _ip_in_allowed_list(self, ip: str, allowed_list: List[str]) -> bool:
        """Check if IP is in allowed list (supports CIDR notation)."""
        import ipaddress
        
        try:
            client_addr = ipaddress.ip_address(ip)
            
            for allowed in allowed_list:
                if '/' in allowed:
                    # CIDR notation
                    network = ipaddress.ip_network(allowed, strict=False)
                    if client_addr in network:
                        return True
                else:
                    # Single IP
                    if client_addr == ipaddress.ip_address(allowed):
                        return True
            
            return False
        except ValueError:
            return False
