"""Shield H5 (v20.2): the live-research fetcher must re-validate the
SSRF policy on EVERY redirect hop. Before the fix, httpx followed
redirects internally, so a validated public page could 302 the
fetcher at loopback, link-local (cloud metadata) or private LAN
addresses the policy never saw. These tests drive fetch() through a
MockTransport — no network is touched."""
from __future__ import annotations

import httpx
import pytest

from app.netpolicy import UrlPolicyError
from app.research import LiveWebResearcher

_PUBLIC = "http://93.184.216.34"  # literal public IP (example.com's)
_PAGE = "<html><head><title>T</title></head><body>" + \
        ("evidence text worth cataloguing. " * 12) + "</body></html>"


def _researcher(handler) -> LiveWebResearcher:
    return LiveWebResearcher(transport=httpx.MockTransport(handler))


def _client(r: LiveWebResearcher) -> httpx.Client:
    return httpx.Client(transport=r.transport, follow_redirects=True,
                        trust_env=False)


def test_direct_public_fetch_ok():
    def handler(request):
        return httpx.Response(200, text=_PAGE,
                              headers={"content-type": "text/html"})
    r = _researcher(handler)
    with _client(r) as client:
        page = r.fetch(client, f"{_PUBLIC}/ok")
    assert page is not None and "evidence" in page["text"]


def test_redirect_to_loopback_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})
    r = _researcher(handler)
    with _client(r) as client:
        with pytest.raises(UrlPolicyError):
            r.fetch(client, f"{_PUBLIC}/start")


def test_redirect_to_link_local_metadata_blocked():
    def handler(request):
        return httpx.Response(302, headers={
            "location": "http://169.254.169.254/latest/meta-data/"})
    r = _researcher(handler)
    with _client(r) as client:
        with pytest.raises(UrlPolicyError):
            r.fetch(client, f"{_PUBLIC}/start")


def test_redirect_to_private_lan_blocked():
    def handler(request):
        return httpx.Response(301, headers={"location": "http://192.168.1.10/internal"})
    r = _researcher(handler)
    with _client(r) as client:
        with pytest.raises(UrlPolicyError):
            r.fetch(client, f"{_PUBLIC}/start")


def test_redirect_chain_second_hop_blocked():
    def handler(request):
        if request.url.path == "/one":
            return httpx.Response(302, headers={"location": f"{_PUBLIC}/two"})
        return httpx.Response(302, headers={"location": "http://10.0.0.5/secret"})
    r = _researcher(handler)
    with _client(r) as client:
        with pytest.raises(UrlPolicyError):
            r.fetch(client, f"{_PUBLIC}/one")


def test_legit_redirect_chain_followed_and_relative_location_ok():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "/new"})
        return httpx.Response(200, text=_PAGE,
                              headers={"content-type": "text/html"})
    r = _researcher(handler)
    with _client(r) as client:
        page = r.fetch(client, f"{_PUBLIC}/old")
    assert page is not None and page["title"] == "T"


def test_redirect_to_forbidden_scheme_blocked():
    def handler(request):
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})
    r = _researcher(handler)
    with _client(r) as client:
        with pytest.raises(UrlPolicyError):
            r.fetch(client, f"{_PUBLIC}/start")
