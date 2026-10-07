"""Cache Google's public verification certificates with bounded network work."""

import re
import threading
import time
from dataclasses import dataclass

from google.auth import exceptions
from google.auth.transport import requests as google_requests


CERTIFICATE_URLS = frozenset({"https://www.googleapis.com/oauth2/v1/certs",
                             "https://www.googleapis.com/oauth2/v3/certs"})


@dataclass(frozen=True)
class CertificateResponse:
    status: int
    data: bytes
    headers: dict


class CachedGoogleCertificates:
    def __init__(self, transport=None, clock=time.monotonic):
        self.transport = transport or google_requests.Request()
        self.clock = clock
        self.lock = threading.Lock()
        self.cache = {}
        self.failed_until = 0

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        # The verifier receives no route or certificate URL from a token/caller.
        # Its only network destinations are these Google-owned HTTPS feeds.
        if url not in CERTIFICATE_URLS or method != "GET" or body is not None:
            raise exceptions.TransportError("Unsupported certificate request")
        with self.lock:
            current = self.clock()
            cached = self.cache.get(url)
            if cached is not None and cached[0] > current:
                return cached[1]
            if self.failed_until > current:
                raise exceptions.TransportError("Certificate service is temporarily unavailable")
            try:
                response = self.transport(url, method="GET", timeout=5)
                if response.status != 200 or len(response.data) > 65536:
                    raise exceptions.TransportError("Certificate response is unavailable")
                cache_header = response.headers.get("cache-control", response.headers.get("Cache-Control", ""))
                maximum = re.search(r"(?:^|[,\s])max-age=(\d+)", cache_header)
                ttl = min(3600, int(maximum.group(1))) if maximum else 300
                if re.search(r"(?:^|[,\s])(?:no-store|no-cache)(?:$|[,\s])", cache_header):
                    ttl = 0
                value = CertificateResponse(200, bytes(response.data), {"Cache-Control": cache_header})
                self.cache[url] = (self.clock() + ttl, value)
                self.failed_until = 0
                return value
            except Exception:
                self.failed_until = self.clock() + 2
                # Never accept an expired certificate cache after a failed fetch.
                raise exceptions.TransportError("Certificate service is temporarily unavailable") from None


certificate_transport = CachedGoogleCertificates()
