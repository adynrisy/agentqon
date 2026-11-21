"""AgentQon — live web research (Phase 5).

The research provider turns a mission objective into REAL source records:
a keyless search (DuckDuckGo HTML — no API key, replaceable via the
provider seam), then bounded fetches of the top results with text
extraction. Every source carries provenance: URL, publisher (domain),
retrieval timestamp, content hash and method, so citations made against
it are verifiable and the deterministic evidence checks keep working.

Safety: fetched URLs pass the request-time SSRF policy; fetches are
bounded in count, size and time; ad/tracker results are filtered out;
individual page failures never fail the research run — a partial corpus
is returned, and a total failure raises so the caller can fall back to
the seed corpus with a visible event.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .netpolicy import UrlPolicyError, _ip_blocked, assert_resolved_host_safe

_UA = "Mozilla/5.0 (X11; Linux x86_64) AgentQonResearch/1.0 (+evidence-driven research)"
_AD_MARKERS = ("/aclick", "y.js?", "duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com")
_MAX_PAGE_BYTES = 512_000
_MAX_REDIRECTS = 5


def source_id_for_url(url: str) -> str:
    return "src_web_" + hashlib.sha1(url.encode()).hexdigest()[:12]


def _decode_ddg_href(href: str) -> Optional[str]:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        target = (qs.get("uddg") or [None])[0]
        return unquote(target) if target else None
    return href


def _is_ad(url: str) -> bool:
    return any(m in url for m in _AD_MARKERS) or "bing.com/aclick" in url


class _DdgParser(HTMLParser):
    """Extracts (title, url, snippet) triples from DDG HTML results."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._cur: Optional[dict] = None
        self._capture: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class", "")
        if tag == "a" and "result__a" in cls:
            url = _decode_ddg_href(attrs.get("href", ""))
            self._cur = {"url": url, "title": "", "snippet": ""}
            self._capture = "title"
        elif tag == "a" and "result__snippet" in cls and self._cur is not None:
            self._capture = "snippet"

    def handle_data(self, data):
        if self._cur is not None and self._capture:
            self._cur[self._capture] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._capture == "title":
            self._capture = None
        elif tag == "a" and self._capture == "snippet":
            self._capture = None
            if self._cur and self._cur["url"]:
                self.results.append(self._cur)
            self._cur = None


class _TextExtractor(HTMLParser):
    """Readable text + title + published-date hints from a page."""

    _SKIP = {"script", "style", "noscript", "svg", "header", "footer", "nav", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.published = ""
        self._parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            a = dict(attrs)
            if a.get("property") in ("article:published_time", "og:published_time") \
                    or a.get("name") in ("date", "publish-date", "article:published_time"):
                self.published = a.get("content", "")[:10]

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            text = data.strip()
            if text:
                self._parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


class LiveWebResearcher:
    """Search + fetch research provider (synchronous by design: the
    Tool Gateway's lifecycle is synchronous; bounds keep it brief)."""

    def __init__(self, max_fetches: int = 5, timeout: float = 10.0,
                 transport: Optional[httpx.BaseTransport] = None,
                 search_url: str = "https://html.duckduckgo.com/html/"):
        self.max_fetches = max_fetches
        self.timeout = timeout
        self.transport = transport
        self.search_url = search_url

    def _client(self) -> httpx.Client:
        from .netpolicy import egress_proxy, tls_verify
        kwargs = {}
        proxy = egress_proxy()
        if proxy and self.transport is None:
            kwargs["proxy"] = proxy
        return httpx.Client(timeout=self.timeout, transport=self.transport,
                            follow_redirects=True, trust_env=False,
                            verify=tls_verify(),
                            headers={"User-Agent": _UA}, **kwargs)

    def search(self, client: httpx.Client, query: str, limit: int = 8) -> list[dict]:
        resp = client.post(self.search_url, data={"q": query})
        resp.raise_for_status()
        parser = _DdgParser()
        parser.feed(resp.text)
        out = []
        for r in parser.results:
            url = r["url"] or ""
            if not url.startswith(("http://", "https://")) or _is_ad(url):
                continue
            out.append({"url": url, "title": r["title"].strip(),
                        "snippet": r["snippet"].strip()})
            if len(out) >= limit:
                break
        return out

    def fetch(self, client: httpx.Client, url: str) -> Optional[dict]:
        """Fetch one page, following redirects MANUALLY (Shield H5).

        The SSRF policy is re-validated on EVERY hop — scheme, host
        and resolved addresses — before the next request is issued.
        The previous implementation let httpx follow redirects on its
        own, so a page could 302 the fetcher from a validated public
        URL straight at 127.0.0.1, a link-local metadata address or a
        private LAN host without the policy ever seeing the target.
        A blocked hop raises UrlPolicyError (research() treats it as
        a failed fetch and moves on).
        """
        from urllib.parse import urljoin
        current = url
        for hop in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https"):
                raise UrlPolicyError(
                    "scheme", f"redirect hop has a forbidden scheme: "
                              f"{parsed.scheme or '(none)'}")
            assert_resolved_host_safe(current, allow_local=False)
            resp = client.get(current, follow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "")
                if not location:
                    return None
                if hop == _MAX_REDIRECTS:
                    return None
                current = urljoin(current, location)
                continue
            break
        if resp.status_code >= 400:
            return None
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return None
        body = resp.content[:_MAX_PAGE_BYTES].decode(
            resp.encoding or "utf-8", errors="replace")
        extractor = _TextExtractor()
        extractor.feed(body)
        text = extractor.text()
        if len(text) < 120:  # paywall shells / JS walls carry no evidence
            return None
        return {"title": extractor.title.strip(), "text": text,
                "published": extractor.published}

    def research(self, queries: list[str]) -> list[dict]:
        """Returns SourceRecord-shaped dicts (possibly empty)."""
        retrieved_at = datetime.now(timezone.utc).isoformat()
        seen: dict[str, dict] = {}
        fetches = 0
        with self._client() as client:
            for query in queries:
                for hit in self.search(client, query):
                    url = hit["url"]
                    if url in seen:
                        continue
                    # Never catalogue (let alone fetch) a result whose
                    # host is a blocked/private literal address.
                    try:
                        host_ip = ipaddress.ip_address(urlparse(url).hostname or "")
                        if _ip_blocked(host_ip) or host_ip.is_loopback or host_ip.is_private:
                            continue
                    except ValueError:
                        pass
                    page = None
                    if fetches < self.max_fetches:
                        fetches += 1
                        try:
                            page = self.fetch(client, url)
                        except (httpx.HTTPError, UrlPolicyError, ValueError):
                            page = None
                    excerpt = (page["text"][:900] if page else hit["snippet"][:900])
                    if not excerpt:
                        continue
                    seen[url] = {
                        "source_id": source_id_for_url(url),
                        "title": (page["title"] if page and page["title"]
                                  else hit["title"] or urlparse(url).netloc),
                        "kind": "secondary",
                        "published_at": (page["published"] if page and page["published"]
                                         else retrieved_at[:10]),
                        "retrieved_at": retrieved_at,
                        "excerpt": excerpt,
                        "url": url,
                        "publisher": urlparse(url).netloc,
                        "content_hash": hashlib.sha256(
                            (page["text"] if page else excerpt).encode()).hexdigest(),
                        "retrieval_method": "live-web:duckduckgo",
                    }
        return list(seen.values())
