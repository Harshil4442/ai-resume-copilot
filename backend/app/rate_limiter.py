import os
import sys

from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared rate admission scales across API instances; ledger state remains in SQL.
# Tests keep isolated in-memory storage and never reach a production Redis.
storage_uri = "memory://" if "pytest" in sys.modules else (
    os.getenv("RATE_LIMIT_STORAGE_URL") or os.getenv("REDIS_URL") or "memory://"
)
limiter = Limiter(key_func=get_remote_address, storage_uri=storage_uri)
