"""AgentQon — reach gateway pack (v21, Track 4).

Read-only external-reach tools for the tool gateway, mapped from
agent-reach (Panniantong/agent-reach, MIT — its channel catalogue and
the idea of one reach layer over many public sources; implementation
here is original). Every tool is executed ONLY through the gateway
(app/gateway.py): allowlist, classification, budget and audit apply
before any code in this module runs, and a channel must additionally
be operator-enabled (AGENTQON_REACH_CHANNELS) or the gateway denies
it as not_configured — denied by default.

Access rules honoured: public/keyless endpoints only, a descriptive
User-Agent, upstream rate-limit/block answers reported honestly
(never retried aggressively, never faked). Channels that need an
API key (Exa) exist as declared capabilities and answer
`not_configured` until the key is present.
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .memory import redact_text
from .netpolicy import UrlPolicyError, assert_resolved_host_safe
from .research import _TextExtractor

USER_AGENT = "AgentQon/21.0 (+https://www.npmjs.com/package/agentqon)"
MAX_BYTES = 2_000_000
MAX_TEXT = 8000
MAX_REDIRECTS = 4

# tool name -> channel name (the gateway's enablement unit)
TOOL_CHANNELS = {
    "reach.read": "web",
    "reach.rss": "rss",
    "reach.github": "github",
    "reach.youtube": "youtube",
    "reach.reddit": "reddit",
    "reach.exa": "exa",
}

CHANNEL_INFO = {
    "web": {"tool": "reach.read", "needs_key": False,
            "description": "Fetch a public web page and extract its readable text."},
    "rss": {"tool": "reach.rss", "needs_key": False,
            "description": "Read a public RSS/Atom feed's latest items."},
    "github": {"tool": "reach.github", "needs_key": False,
               "description": "Read a public GitHub repository's metadata and README."},
    "youtube": {"tool": "reach.youtube", "needs_key": False,
                "description": "Fetch a public YouTube video's caption transcript."},
    "reddit": {"tool": "reach.reddit", "needs_key": False,
               "description": "Read a public subreddit's current top posts (JSON)."},
    "exa": {"tool": "reach.exa", "needs_key": True,
            "description": "Exa web search (requires EXA_API_KEY)."},
}


def channel_for(tool: str) -> str | None:
    return TOOL_CHANNELS.get(tool)


def _fetch(url: str, headers: dict | None = None,
           timeout: float = 15.0) -> tuple[int, str, bytes]:
    """One SSRF-disciplined GET: scheme checked, resolved host
    re-validated on EVERY redirect hop (same policy as the research
    fetcher, Shield H5). Tests stub this boundary — app code never
    branches on being tested."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    current = url
    with httpx.Client(timeout=timeout, follow_redirects=False,
                      trust_env=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https"):
                raise UrlPolicyError(
                    "scheme", f"forbidden scheme: {parsed.scheme or '(none)'}")
            assert_resolved_host_safe(current, allow_local=False)
            resp = client.get(current, headers=hdrs)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "")
                if not location or hop == MAX_REDIRECTS:
                    return resp.status_code, resp.headers.get("content-type", ""), b""
                current = urljoin(current, location)
                continue
            return (resp.status_code,
                    resp.headers.get("content-type", ""),
                    resp.content[:MAX_BYTES])
    return 0, "", b""  # unreachable


def _err(status: str, reason: str, **extra) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def read_page(args: dict) -> dict[str, Any]:
    url = str(args.get("url", ""))
    if not url:
        return _err("invalid_args", "reach.read requires args.url")
    try:
        status, ctype, raw = _fetch(url)
    except UrlPolicyError as exc:
        return _err("blocked", f"URL policy: {exc}")
    except httpx.HTTPError as exc:
        return _err("fetch_failed", f"{type(exc).__name__}: {exc}")
    if status >= 400:
        return _err("fetch_failed", f"HTTP {status}", url=url)
    if "html" not in ctype and "text" not in ctype:
        return _err("unsupported", f"content-type {ctype or 'unknown'}", url=url)
    body = raw.decode("utf-8", errors="replace")
    extractor = _TextExtractor()
    extractor.feed(body)
    text = extractor.text()
    return {"status": "ok", "url": url, "title": extractor.title.strip(),
            "text": text[:MAX_TEXT], "chars": len(text),
            "extractor": "native-readable-v1"}


def read_rss(args: dict) -> dict[str, Any]:
    url = str(args.get("url", ""))
    if not url:
        return _err("invalid_args", "reach.rss requires args.url")
    try:
        status, _ctype, raw = _fetch(url)
    except UrlPolicyError as exc:
        return _err("blocked", f"URL policy: {exc}")
    except httpx.HTTPError as exc:
        return _err("fetch_failed", f"{type(exc).__name__}: {exc}")
    if status >= 400:
        return _err("fetch_failed", f"HTTP {status}", url=url)
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        return _err("parse_failed", f"feed is not valid XML: {exc}", url=url)
    items: list[dict] = []
    # RSS 2.0 items, then Atom entries (namespace-agnostic local names).
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {}
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "link" and child.get("href"):
                fields["link"] = child.get("href")
            elif ctag in ("title", "link", "pubDate", "published", "updated",
                          "summary", "description") and child.text:
                fields[ctag] = child.text.strip()
        if fields.get("title"):
            items.append({"title": fields["title"],
                          "link": fields.get("link", ""),
                          "published": fields.get("pubDate")
                          or fields.get("published")
                          or fields.get("updated", ""),
                          "summary": (fields.get("summary")
                                      or fields.get("description", ""))[:300]})
        if len(items) >= 20:
            break
    return {"status": "ok", "url": url, "items": items, "count": len(items)}


def read_github(args: dict) -> dict[str, Any]:
    repo = str(args.get("repo", "")).strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return _err("invalid_args",
                    "reach.github requires args.repo as 'owner/name'")
    api = f"https://api.github.com/repos/{repo}"
    try:
        status, _c, raw = _fetch(api, {"Accept": "application/vnd.github+json"})
        if status == 403:
            return _err("rate_limited",
                        "GitHub API rate limit for unauthenticated callers",
                        repo=repo)
        if status >= 400:
            return _err("fetch_failed", f"HTTP {status}", repo=repo)
        meta = json.loads(raw.decode("utf-8", errors="replace"))
        rstatus, _rc, rraw = _fetch(
            f"https://api.github.com/repos/{repo}/readme",
            {"Accept": "application/vnd.github.raw+json"})
        readme = (rraw.decode("utf-8", errors="replace")[:4000]
                  if rstatus == 200 else "")
    except UrlPolicyError as exc:
        return _err("blocked", f"URL policy: {exc}")
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        return _err("fetch_failed", f"{type(exc).__name__}: {exc}")
    return {"status": "ok", "repo": repo,
            "description": meta.get("description") or "",
            "stars": meta.get("stargazers_count"),
            "language": meta.get("language"),
            "default_branch": meta.get("default_branch"),
            "updated_at": meta.get("updated_at"),
            "readme_excerpt": readme}


def read_youtube(args: dict) -> dict[str, Any]:
    url = str(args.get("url", ""))
    match = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{6,})", url)
    if not match:
        return _err("invalid_args",
                    "reach.youtube requires args.url with a video id")
    video_id = match.group(1)
    try:
        status, _c, raw = _fetch(f"https://www.youtube.com/watch?v={video_id}")
    except UrlPolicyError as exc:
        return _err("blocked", f"URL policy: {exc}")
    except httpx.HTTPError as exc:
        return _err("fetch_failed", f"{type(exc).__name__}: {exc}")
    if status >= 400:
        return _err("fetch_failed", f"HTTP {status}", video_id=video_id)
    page = raw.decode("utf-8", errors="replace")
    cap = re.search(r'"captionTracks":(\[.*?\])', page)
    if not cap:
        return _err("unavailable",
                    "no caption tracks are exposed for this video",
                    video_id=video_id)
    try:
        tracks = json.loads(cap.group(1))
        track = next((t for t in tracks
                      if t.get("languageCode", "").startswith("en")),
                     tracks[0] if tracks else None)
        if not track or not track.get("baseUrl"):
            return _err("unavailable", "caption track has no fetch URL",
                        video_id=video_id)
        tstatus, _tc, traw = _fetch(track["baseUrl"])
        if tstatus >= 400:
            return _err("fetch_failed", f"caption fetch HTTP {tstatus}",
                        video_id=video_id)
        root = ET.fromstring(traw.decode("utf-8", errors="replace"))
        lines = [el.text or "" for el in root.iter("text")]
    except (json.JSONDecodeError, ET.ParseError) as exc:
        return _err("parse_failed", f"captions unparsable: {exc}",
                    video_id=video_id)
    except UrlPolicyError as exc:
        return _err("blocked", f"URL policy: {exc}")
    text = " ".join(" ".join(lines).split())
    return {"status": "ok", "video_id": video_id,
            "language": track.get("languageCode", ""),
            "transcript": text[:MAX_TEXT], "chars": len(text)}


def read_reddit(args: dict) -> dict[str, Any]:
    sub = str(args.get("subreddit", "")).strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,21}", sub or ""):
        return _err("invalid_args",
                    "reach.reddit requires args.subreddit (name only)")
    url = f"https://www.reddit.com/r/{sub}/top.json?t=day&limit=15&raw_json=1"
    try:
        status, _c, raw = _fetch(url, {"Accept": "application/json"})
    except UrlPolicyError as exc:
        return _err("blocked", f"URL policy: {exc}")
    except httpx.HTTPError as exc:
        return _err("fetch_failed", f"{type(exc).__name__}: {exc}")
    if status in (401, 403):
        return _err("blocked",
                    "Reddit refused the public JSON endpoint (its current "
                    "access policy); no data was returned", subreddit=sub)
    if status >= 400:
        return _err("fetch_failed", f"HTTP {status}", subreddit=sub)
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        posts = [{"title": c["data"].get("title", ""),
                  "author": c["data"].get("author", ""),
                  "score": c["data"].get("score"),
                  "url": "https://www.reddit.com"
                         + c["data"].get("permalink", "")}
                 for c in data.get("data", {}).get("children", [])]
    except (json.JSONDecodeError, KeyError, AttributeError) as exc:
        return _err("parse_failed", f"unexpected Reddit payload: {exc}",
                    subreddit=sub)
    return {"status": "ok", "subreddit": sub, "posts": posts,
            "count": len(posts)}


def exa_search(args: dict) -> dict[str, Any]:
    api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        return {"status": "not_configured", "channel": "exa",
                "needs": "EXA_API_KEY environment variable",
                "note": "Exa is a declared capability; it activates when "
                        "the operator provides its API key."}
    query = str(args.get("query", ""))
    if not query:
        return _err("invalid_args", "reach.exa requires args.query")
    try:
        with httpx.Client(timeout=20, trust_env=False) as client:
            resp = client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={"query": query, "numResults": 5,
                      "contents": {"text": {"maxCharacters": 1200}}})
        if resp.status_code >= 400:
            return _err("fetch_failed", f"Exa HTTP {resp.status_code}")
        data = resp.json()
    except httpx.HTTPError as exc:
        return _err("fetch_failed", f"{type(exc).__name__}: {exc}")
    results = [{"title": r.get("title", ""), "url": r.get("url", ""),
                "text": (r.get("text") or "")[:1200]}
               for r in data.get("results", [])]
    return {"status": "ok", "query": query, "results": results,
            "count": len(results)}


_DISPATCH = {
    "reach.read": read_page,
    "reach.rss": read_rss,
    "reach.github": read_github,
    "reach.youtube": read_youtube,
    "reach.reddit": read_reddit,
    "reach.exa": exa_search,
}


def execute(tool: str, args: dict) -> dict[str, Any]:
    fn = _DISPATCH.get(tool)
    if fn is None:
        return _err("unknown_tool", f"no reach implementation for {tool}")
    try:
        result = fn(args or {})
    except Exception as exc:  # a reach failure must never crash a mission
        return _err("error", f"{type(exc).__name__}: {exc}")
    return _redact_result(result)


_STATIC_KEYS = {"needs", "note", "status", "channel"}


def _redact_result(value: Any, key: str = "") -> Any:
    """Redact fetched/user content before it can reach an audit
    record or a prompt. Our own static capability strings (the
    not_configured explanation fields) are not content and pass
    through verbatim — redacting them would destroy the honest
    'what to configure' signal."""
    if key in _STATIC_KEYS:
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_result(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_result(v, k) for k, v in value.items()}
    return value


def audit_view(result: dict) -> dict:
    """The audit-record copy: same shape, long text fields truncated —
    the gateway's record proves what happened without becoming a
    second copy of the fetched content."""
    def trim(v: Any) -> Any:
        if isinstance(v, str):
            return v[:400] + ("…" if len(v) > 400 else "")
        if isinstance(v, list):
            return [trim(x) for x in v]
        if isinstance(v, dict):
            return {k: trim(x) for k, x in v.items()}
        return v
    return trim(result)


def declared_capabilities(enabled: set[str]) -> list[dict]:
    """What /health-style surfaces report: every channel, whether it
    is enabled, and whether it still needs a key."""
    out = []
    for name, info in CHANNEL_INFO.items():
        state = "enabled" if name in enabled else "not_configured"
        if info["needs_key"] and not os.environ.get("EXA_API_KEY"):
            state = "not_configured"
        out.append({"channel": name, "tool": info["tool"],
                    "state": state, "needs_key": info["needs_key"],
                    "description": info["description"]})
    return out
