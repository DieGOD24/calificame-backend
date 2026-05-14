import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Disable rate limiting in test environment
_enabled = os.environ.get("TESTING", "").lower() not in ("1", "true")

limiter = Limiter(key_func=get_remote_address, enabled=_enabled)
