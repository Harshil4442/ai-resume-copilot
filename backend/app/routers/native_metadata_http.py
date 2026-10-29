"""Finite public capacity errors; no native or Redis details in responses."""
from fastapi import HTTPException

from ..domains.recovery.native_metadata_admission import (
    CAPACITY_RETRY_SECONDS,
    MetadataAdmissionError,
    MetadataCapacityUnavailable,
)


def metadata_http_error(error: MetadataAdmissionError) -> HTTPException:
    capacity = isinstance(error, MetadataCapacityUnavailable)
    return HTTPException(429 if capacity else 503,
        {"code": "NATIVE_METADATA_CAPACITY" if capacity else "NATIVE_METADATA_UNAVAILABLE",
         "message": "Request capacity is temporarily allocated. Please try again later." if capacity
            else "Request verification is temporarily unavailable. Please try again later."},
        headers={"Retry-After": str(CAPACITY_RETRY_SECONDS if capacity else 30),
                 "Cache-Control": "private, no-store", "Pragma": "no-cache"})
