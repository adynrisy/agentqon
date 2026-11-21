"""v21 Track 4 — reach gateway pack: every tool passes the gateway's
controls (allowlist / deny / budget / channel enablement), executes
against a stubbed network boundary, and reports upstream states
honestly (not_configured, unavailable, blocked — never faked)."""
import json
import tempfile

import app.reach as reach
from app.gateway import ToolGateway
from app.store import Store

HTML = ("<html><head><title>Example Story</title></head><body>"
        "<nav>menu junk</nav><article><h1>Example Story</h1>"
        "<p>The council approved the new transit plan on Tuesday after "
        "a long debate about funding and timelines for the region.</p>"
        "<p>Officials said construction begins in spring.</p>"
        "<script>var secret = 'sk-should-not-leak-123456';</script>"
        "</article></body></html>")

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>First Post</title><link>https://example.com/1</link>
<pubDate>Mon, 06 Oct 2026 10:00:00 GMT</pubDate>
<description>About the first post</description></item>
<item><title>Second Post</title><link>https://example.com/2</link>
<pubDate>Tue, 07 Oct 2026 10:00:00 GMT</pubDate>
<description>About the second post</description></item>
</channel></rss>"""

GH_META = json.dumps({"description": "A test repo", "stargazers_count": 42,
                      "language": "Python", "default_branch": "main",
                      "updated_at": "2026-10-01T00:00:00Z"})

REDDIT = json.dumps({"data": {"children": [
    {"data": {"title": "Top post", "author": "someone", "score": 128,
              "permalink": "/r/python/comments/abc/top_post/"}}]}})

YT_PAGE = ('<html><body>"captionTracks":[{"baseUrl":'
           '"https://www.youtube.com/api/timedtext?v=vid12345678",'
           '"languageCode":"en"}]</body></html>')
YT_CAPS = ('<?xml version="1.0"?><transcript>'
           '<text start="0" dur="1">Hello world</text>'
           '<text start="1" dur="1">this is a test</text></transcript>')


def stub_fetch(routes):
    def fake(url, headers=None, timeout=15.0):
        for needle, response in routes:
            if needle in url:
                return response
        return 404, "text/html", b"nope"
    return fake


def make_gateway(tmp, channels=()):
    store = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
    return ToolGateway(store, reach_channels=set(channels)), store


def mission(allow, deny=None, spent=0.0, limit=10.0, classification="internal"):
    return {"id": "mis_reach", "spent": spent, "budget_limit": limit,
            "contract": {"tools": {"allow": allow, "deny": deny or []},
                         "approval_policy": {"external_side_effects": "required"},
                         "data_classification": classification}}


def test_reach_denied_by_default_not_configured():
    with tempfile.TemporaryDirectory() as tmp:
        gw, store = make_gateway(tmp)  # no channels enabled
        d = gw.authorize_and_run("org_t", mission(["reach.read"]),
                                 "reach.read", {"url": "https://example.com"})
        assert d.allowed is False and d.reason == "REACH_NOT_CONFIGURED"
        inv = store.list_invocations("org_t", "mis_reach")
        assert inv and inv[0]["status"] == "denied"
        assert inv[0]["data"]["channel"] == "web"


def test_reach_allowlist_and_deny_and_budget_gates(monkeypatch):
    # v21.3: the budget gate is the internal runaway ceiling, not the
    # mission's stored limit — patched to the mission's $10 here so the
    # gate scenario is unchanged.
    monkeypatch.setattr("app.gateway.MISSION_BUDGET_CEILING_USD", 10.0)
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp, channels={"web"})
        d = gw.authorize_and_run("org_t", mission(["web.read"]),
                                 "reach.read", {"url": "https://example.com"})
        assert d.reason == "NOT_IN_ALLOW_LIST"
        d = gw.authorize_and_run(
            "org_t", mission(["reach.read"], deny=["reach.read"]),
            "reach.read", {"url": "https://example.com"})
        assert d.reason == "DENIED_BY_POLICY"
        d = gw.authorize_and_run(
            "org_t", mission(["reach.read"], spent=9.999, limit=10.0),
            "reach.read", {"url": "https://example.com"})
        assert d.reason == "BUDGET_EXCEEDED"


def test_reach_read_executes_and_audit_is_redacted(monkeypatch):
    monkeypatch.setattr(reach, "_fetch", stub_fetch([
        ("example.com", (200, "text/html", HTML.encode()))]))
    with tempfile.TemporaryDirectory() as tmp:
        gw, store = make_gateway(tmp, channels={"web"})
        d = gw.authorize_and_run("org_t", mission(["reach.read"]),
                                 "reach.read", {"url": "https://example.com/x"})
        assert d.allowed is True and d.result["status"] == "ok"
        assert d.result["title"] == "Example Story"
        assert "council approved the new transit plan" in d.result["text"]
        inv = store.list_invocations("org_t", "mis_reach")
        assert inv[0]["status"] == "completed"
        audited = json.dumps(inv[0]["data"])
        assert "sk-should-not-leak" not in audited  # script text never stored
        assert len(inv[0]["data"]["result"]["text"]) <= 401  # truncated view


def test_reach_rss_github_reddit(monkeypatch):
    monkeypatch.setattr(reach, "_fetch", stub_fetch([
        ("feed.xml", (200, "application/rss+xml", RSS.encode())),
        ("api.github.com/repos/o/r/readme", (200, "text/plain", b"# Readme body")),
        ("api.github.com", (200, "application/json", GH_META.encode())),
        ("reddit.com", (200, "application/json", REDDIT.encode())),
    ]))
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp, channels={"rss", "github", "reddit"})
        d = gw.authorize_and_run("org_t", mission(["reach.rss"]),
                                 "reach.rss", {"url": "https://example.com/feed.xml"})
        assert d.result["count"] == 2
        assert d.result["items"][0]["title"] == "First Post"
        d = gw.authorize_and_run("org_t", mission(["reach.github"]),
                                 "reach.github", {"repo": "o/r"})
        assert d.result["status"] == "ok" and d.result["stars"] == 42
        assert d.result["readme_excerpt"] == "# Readme body"
        d = gw.authorize_and_run("org_t", mission(["reach.reddit"]),
                                 "reach.reddit", {"subreddit": "python"})
        assert d.result["count"] == 1
        assert d.result["posts"][0]["score"] == 128


def test_reach_youtube_transcript_and_unavailable(monkeypatch):
    monkeypatch.setattr(reach, "_fetch", stub_fetch([
        ("api/timedtext", (200, "text/xml", YT_CAPS.encode())),
        ("watch?v=vid12345678", (200, "text/html", YT_PAGE.encode())),
        ("watch?v=nocaps12345", (200, "text/html", b"<html>no captions</html>")),
    ]))
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp, channels={"youtube"})
        d = gw.authorize_and_run(
            "org_t", mission(["reach.youtube"]), "reach.youtube",
            {"url": "https://www.youtube.com/watch?v=vid12345678"})
        assert d.result["status"] == "ok"
        assert d.result["transcript"] == "Hello world this is a test"
        d = gw.authorize_and_run(
            "org_t", mission(["reach.youtube"]), "reach.youtube",
            {"url": "https://www.youtube.com/watch?v=nocaps12345"})
        assert d.result["status"] == "unavailable"  # honest, not faked


def test_reach_exa_declared_but_not_configured(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    caps = {c["channel"]: c for c in reach.declared_capabilities({"exa"})}
    assert caps["exa"]["state"] == "not_configured"
    assert caps["exa"]["needs_key"] is True
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp, channels={"exa"})
        d = gw.authorize_and_run("org_t", mission(["reach.exa"]),
                                 "reach.exa", {"query": "anything"})
        assert d.allowed is True
        assert d.result["status"] == "not_configured"
        assert d.result["needs"].startswith("EXA_API_KEY")


def test_reach_ssrf_blocked_target(monkeypatch):
    # the real _fetch validates before any request; a private literal
    # target is refused by policy (no network touched in this test
    # because assert_resolved_host_safe raises first for literals).
    result = reach.read_page({"url": "http://127.0.0.1:8000/admin"})
    assert result["status"] == "blocked"
