"""Secure HTTP Headers Middleware for SevaHealth AI.

Enforces defense-in-depth HTTP security headers complying with OWASP Top 10,
HIPAA, and DISHA security guidelines.
"""

from typing import Callable, Set
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


class SecureHeadersMiddleware(BaseHTTPMiddleware):
    """Injects hardened security response headers into every HTTP response."""

    # Sensitive route prefixes that must disable client & proxy caching
    NOCACHE_PREFIXES: Set[str] = {
        "/api/v1/auth",
        "/api/v1/citizens",
        "/api/v1/clinical",
        "/api/v1/risk",
        "/api/v1/ai",
        "/api/v1/wearables",
        "/api/v1/privacy",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)

        # 1. Content Security Policy (CSP)
        # Prevents XSS, packet sniffing, clickjacking, while allowing necessary CDNs for Swagger UI, Tailwind & Fonts
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://cdnjs.cloudflare.com https://fonts.gstatic.com data:; "
            "connect-src 'self' https:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none';"
        )

        # 2. Frame Protection (Clickjacking defense)
        response.headers["X-Frame-Options"] = "DENY"

        # 3. MIME Sniffing Defense
        response.headers["X-Content-Type-Options"] = "nosniff"

        # 4. Cross-Site Scripting (XSS) Filter
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # 5. HTTP Strict Transport Security (HSTS)
        # Enforces TLS in transit for 1 year including subdomains
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

        # 6. Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # 7. Permissions Policy (Hardware API restrictions)
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"

        # 8. Cache-Control on sensitive healthcare and authentication routes
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self.NOCACHE_PREFIXES):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        return response
