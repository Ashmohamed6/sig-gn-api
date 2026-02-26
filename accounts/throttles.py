from rest_framework.throttling import SimpleRateThrottle


class AuthIdentifierRateThrottle(SimpleRateThrottle):
    """
    Throttle anti brute-force par identifiant saisi (username/email),
    en complément du throttle par IP.
    """

    scope = "auth_identifier"

    def get_cache_key(self, request, view):
        username = request.data.get("username") or request.data.get("email")
        if not username:
            return None

        ident = str(username).strip().lower()
        if not ident:
            return None

        return self.cache_format % {"scope": self.scope, "ident": ident}
