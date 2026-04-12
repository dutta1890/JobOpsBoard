"""
security.py — Security utilities for Career-Ops.

Provides login/session auth, rate limiting, input sanitisation, and
secure response headers. All behaviour is opt-in via decorators so the
app degrades gracefully when no DASHBOARD_PASSWORD is configured.
"""

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import redirect, request, session, url_for

# ── Configuration ──────────────────────────────────────────────────────────────

SECRET_KEY: str = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
DASHBOARD_PASSWORD: str = os.environ.get("DASHBOARD_PASSWORD", "")

RATE_LIMIT_WINDOW = 60   # seconds
RATE_LIMIT_MAX = 100     # requests per window
SESSION_TIMEOUT = timedelta(hours=24)

# In-memory rate-limit store: ip → (count, window_start)
_rate_limit_store: dict[str, tuple[int, datetime]] = {}


# ── Password helpers ───────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Return a salted PBKDF2-SHA256 hash of *password*."""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{pwd_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time comparison of *password* against a stored hash."""
    try:
        salt, pwd_hash = stored_hash.split(":", 1)
        new_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return hmac.compare_digest(new_hash.hex(), pwd_hash)
    except Exception:
        return False


# ── Auth decorator ─────────────────────────────────────────────────────────────

def login_required(f):
    """Redirect unauthenticated requests to /login.

    If no DASHBOARD_PASSWORD is set the decorator is a no-op so the app
    remains accessible during development without any extra setup.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return f(*args, **kwargs)

        if "authenticated" not in session:
            return redirect(url_for("login"))

        if "login_time" in session:
            login_time = datetime.fromisoformat(session["login_time"])
            if datetime.now() - login_time > SESSION_TIMEOUT:
                session.clear()
                return redirect(url_for("login"))

        return f(*args, **kwargs)
    return decorated


# ── Rate limiting ──────────────────────────────────────────────────────────────

def check_rate_limit(ip: str) -> bool:
    """Return True if the IP is within the allowed rate, False if exceeded."""
    now = datetime.now()

    if ip in _rate_limit_store:
        count, window_start = _rate_limit_store[ip]
        if (now - window_start).total_seconds() > RATE_LIMIT_WINDOW:
            _rate_limit_store[ip] = (1, now)
            return True
        if count >= RATE_LIMIT_MAX:
            return False
        _rate_limit_store[ip] = (count + 1, window_start)
    else:
        _rate_limit_store[ip] = (1, now)

    return True


def rate_limit(f):
    """Decorator: return 429 if the caller exceeds the rate limit."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        if not check_rate_limit(ip):
            return {"error": "Rate limit exceeded"}, 429
        return f(*args, **kwargs)
    return decorated


# ── Input sanitisation ─────────────────────────────────────────────────────────

def sanitize_input(text: str) -> str:
    """Strip common XSS vectors and escape HTML entities."""
    if not text:
        return ""
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\bon\w+\s*=", "", text, flags=re.IGNORECASE)
    text = re.sub(r"javascript:", "", text, flags=re.IGNORECASE)
    text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
    )
    return text


def sanitize_url(url: str) -> str:
    """Return *url* only if it starts with http/https and has no JS injection."""
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        return ""
    if "javascript:" in url.lower():
        return ""
    return url


# ── Security headers ───────────────────────────────────────────────────────────

def add_security_headers(response):
    """Attach common security headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # CSP: allow CDN Tailwind + inline scripts used by the dashboard
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' cdn.tailwindcss.com; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response
