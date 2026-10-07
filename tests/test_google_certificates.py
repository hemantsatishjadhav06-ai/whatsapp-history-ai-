from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from google.auth.exceptions import TransportError

from assistant.google_certificates import CachedGoogleCertificates


URL = "https://www.googleapis.com/oauth2/v1/certs"


def test_only_public_google_certificates_are_cached_and_network_deadline_is_fixed():
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status=200, data=b'{"synthetic-key":"public-cert"}',
                               headers={"Cache-Control": "public, max-age=600"})
    transport = CachedGoogleCertificates(fetch, clock=lambda: 10)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: transport(URL), range(30)))
    assert len(calls) == 1
    assert calls[0] == (URL, {"method": "GET", "timeout": 5})
    assert all(value.data == results[0].data and value.status == 200 for value in results)
    for url, options in [("http://127.0.0.1/private", {}), (URL, {"method": "POST"}),
                         (URL, {"body": b"private-credential"})]:
        with pytest.raises(TransportError):
            transport(url, **options)
    assert len(calls) == 1


def test_expired_certificates_are_not_used_during_an_outage_and_retry_is_bounded():
    clock = [0]
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        if len(calls) > 1:
            raise TransportError("synthetic network failure")
        return SimpleNamespace(status=200, data=b'{}', headers={"cache-control": "max-age=1"})
    transport = CachedGoogleCertificates(fetch, clock=lambda: clock[0])
    transport(URL)
    clock[0] = 2
    with pytest.raises(TransportError):
        transport(URL)
    with pytest.raises(TransportError):
        transport(URL)
    assert len(calls) == 2
    clock[0] = 5
    with pytest.raises(TransportError):
        transport(URL)
    assert len(calls) == 3


@pytest.mark.parametrize("header", ["no-store", "private, no-cache", "max-age=0"])
def test_certificate_cache_respects_disabled_cache_controls(header):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status=200, data=b'{}', headers={"cache-control": header})
    transport = CachedGoogleCertificates(fetch, clock=lambda: 0)
    transport(URL)
    transport(URL)
    assert len(calls) == 2


def test_certificate_response_size_is_bounded_and_invalid_status_never_enters_cache():
    for response in [SimpleNamespace(status=200, data=b"x" * 65537, headers={}),
                     SimpleNamespace(status=503, data=b"unavailable", headers={})]:
        transport = CachedGoogleCertificates(lambda *args, **kwargs: response, clock=lambda: 0)
        with pytest.raises(TransportError):
            transport(URL)
        assert not transport.cache
