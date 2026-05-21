from rest_framework.permissions import AllowAny
from rest_framework.throttling import SimpleRateThrottle


def _is_public_view(view) -> bool:
    permission_classes = getattr(view, "permission_classes", []) or []
    for perm in permission_classes:
        if perm is AllowAny:
            return True
        if isinstance(perm, type) and issubclass(perm, AllowAny):
            return True
    return False


class _BasePublicThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            return None
        if not _is_public_view(view):
            return None
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class PublicAPIBurstRateThrottle(_BasePublicThrottle):
    scope = "public_api_burst"


class PublicAPIRateThrottle(_BasePublicThrottle):
    scope = "public_api"
