"""Per-download pinned-SDK body guards; never mutate a shared client/session."""
from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from .store import GuardUnavailable


class BoundedSink(io.BytesIO):
    def __init__(self, expected_length: int) -> None:
        super().__init__()
        self.expected_length = expected_length

    def write(self, data: Any) -> int:
        if not isinstance(data, bytes) or self.tell() + len(data) > self.expected_length:
            raise GuardUnavailable("Object body exceeds the exact expected byte length")
        return super().write(data)


class BoundedRaw:
    """Caps application body reads; socket/TLS/kernel read-ahead is not covered."""

    def __init__(self, raw: Any, expected_length: int) -> None:
        self.raw, self.expected_length = raw, expected_length
        self.read_bytes = 0

    def read(self, amount: int | None = None, *, decode_content: bool = False,
             cache_content: bool = False) -> bytes:
        if (type(amount) is not int or amount <= 0 or decode_content is not False
                or cache_content is not False):
            raise GuardUnavailable("Unbounded or decoded SDK body read is forbidden")
        remaining = self.expected_length + 1 - self.read_bytes
        if remaining <= 0:
            return b""
        requested = min(amount, remaining)
        data = self.raw.read(requested, decode_content=False, cache_content=False)
        if type(data) is not bytes or len(data) > requested:
            raise GuardUnavailable("HTTP body transport violated its bounded read contract")
        self.read_bytes += len(data)
        if self.read_bytes > self.expected_length:
            raise GuardUnavailable("Object body exceeds the exact expected byte length")
        return data

    def stream(self, amount: int, decode_content: bool = False) -> Iterator[bytes]:
        # Never delegate to raw.stream(), which can consume a full SDK-size chunk.
        while self.read_bytes <= self.expected_length:
            data = self.read(amount, decode_content=decode_content)
            if not data:
                return
            yield data

    def close(self) -> None:
        self.raw.close()

    def release_conn(self) -> None:
        self.raw.release_conn()


class MediaTransport:
    def __init__(self, delegate: Any, bucket: str, path: str, generation: str,
                 expected_length: int) -> None:
        self.delegate, self.generation = delegate, generation
        self.path = f"/download/storage/v1/b/{quote(bucket, safe='')}/o/{quote(path, safe='')}"
        self.expected_length = expected_length
        self.responses: list[Any] = []
        self.sent = False

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        parsed, headers = urlsplit(url), {key.lower(): value for key, value in
                                         kwargs.get("headers", {}).items()}
        query = parse_qs(parsed.query)
        if (self.sent or method != "GET" or parsed.scheme != "https"
                or parsed.netloc != "storage.googleapis.com" or parsed.path != self.path
                or query.get("alt") != ["media"] or query.get("generation") != [self.generation]
                or query.get("ifGenerationMatch") != [self.generation]
                or headers.get("range") != f"bytes=0-{self.expected_length}"):
            raise GuardUnavailable("SDK media request is outside its exact bounded generation")
        self.sent = True
        # Copy kwargs only; the SDK's shared session/client remains untouched.
        response = self.delegate.request(method, url, **{**kwargs, "stream": True,
                                                       "allow_redirects": False})
        self.responses.append(response)
        if (getattr(response, "_content", None) is not False
                or getattr(response, "_content_consumed", None) is not False
                or response.raw is None or response.status_code not in {200, 206}
                or response.headers.get("Content-Encoding") not in {None, "identity"}
                or response.headers.get("X-Goog-Stored-Content-Encoding") not in {None, "identity"}):
            response.close()  # No error/redirect body is materialized by the SDK.
            raise GuardUnavailable("Media response must be fresh, raw and successful")
        response.raw = BoundedRaw(response.raw, self.expected_length)
        return response

    def close_responses(self) -> None:
        for response in self.responses:
            # Cleanup must not replace an earlier body/transport failure. Neither
            # the shared session nor client's configuration is changed or closed.
            try:
                if response.raw is not None:
                    response.raw.close()
                response.close()
            except Exception:
                pass


class DownloadClient:
    """Narrow client view for this download; owns responses, not the delegate."""

    def __init__(self, client: Any, bucket: str, path: str, generation: str,
                 expected_length: int) -> None:
        self.client = client
        self._http = MediaTransport(client._http, bucket, path, generation, expected_length)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def close_responses(self) -> None:
        self._http.close_responses()
