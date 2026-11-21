"""Phase 5 unit tests: DDG parsing, page extraction, provider bounds
and SSRF filtering — all against a mock transport (no network)."""
from __future__ import annotations

import httpx

from app.research import LiveWebResearcher, _DdgParser, _TextExtractor, source_id_for_url

SEARCH_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fads.example.com%2Faclick%3Fid%3D1&amp;rut=x">Ad result</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Freport&amp;rut=abc">SMB Invoicing Report 2026</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Freport">The market grew steadily with adoption concentrated in small firms.</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=http%3A%2F%2F169.254.169.254%2Flatest&amp;rut=def">Metadata trap</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=http%3A%2F%2F169.254.169.254%2Flatest">should never be fetched</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fblog.example.org%2Fanalysis&amp;rut=ghi">Segment analysis</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fblog.example.org%2Fanalysis">Niche launches outperformed broad ones in retention.</a>
</body></html>
"""

ARTICLE = """<html><head><title>SMB Invoicing Report 2026</title>
<meta property="article:published_time" content="2026-03-14T09:00:00Z"></head>
<body><nav>menu junk</nav><script>var x = 1;</script>
<p>Small businesses adopted invoicing tools at a record pace this year, with
community recommendations driving most first purchases and retention clearly
higher for narrowly focused products than for broad platforms overall.</p>
</body></html>"""


def test_ddg_parser_decodes_and_labels():
    p = _DdgParser()
    p.feed(SEARCH_HTML)
    urls = [r["url"] for r in p.results]
    assert "https://example.com/report" in urls
    assert "https://blog.example.org/analysis" in urls
    report = next(r for r in p.results if "report" in r["url"])
    assert report["title"] == "SMB Invoicing Report 2026"
    assert "market grew" in report["snippet"]


def test_text_extractor_skips_junk_and_finds_date():
    x = _TextExtractor()
    x.feed(ARTICLE)
    assert x.title == "SMB Invoicing Report 2026"
    assert x.published == "2026-03-14"
    text = x.text()
    assert "record pace" in text and "menu junk" not in text and "var x" not in text


def _transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if "duckduckgo" in request.url.host:
            return httpx.Response(200, text=SEARCH_HTML,
                                  headers={"content-type": "text/html"})
        if request.url.host == "example.com":
            return httpx.Response(200, text=ARTICLE,
                                  headers={"content-type": "text/html"})
        if request.url.host == "blog.example.org":
            return httpx.Response(200, text=ARTICLE.replace(
                "SMB Invoicing Report 2026", "Segment analysis"),
                headers={"content-type": "text/html"})
        return httpx.Response(404, text="nope")
    return httpx.MockTransport(handler)


def test_researcher_end_to_end_with_provenance():
    r = LiveWebResearcher(max_fetches=5, transport=_transport())
    sources = r.research(["smb invoicing market"])
    urls = {s["url"] for s in sources}
    # ads filtered by the researcher; the metadata-IP result is dropped
    # at fetch time by the SSRF policy (its snippet alone is too thin a
    # corpus entry only when no snippet exists — here it has one, so the
    # guard assertion is on the fetch path test below).
    assert "https://example.com/report" in urls
    assert "https://blog.example.org/analysis" in urls
    assert not any("ads.example.com" in u for u in urls)
    rep = next(s for s in sources if s["url"] == "https://example.com/report")
    assert rep["source_id"] == source_id_for_url(rep["url"])
    assert rep["publisher"] == "example.com"
    assert rep["published_at"] == "2026-03-14"
    assert rep["retrieval_method"] == "live-web:duckduckgo"
    assert rep["content_hash"] and "record pace" in rep["excerpt"]


def test_researcher_fetch_blocks_metadata_ip():
    r = LiveWebResearcher(transport=_transport())
    import pytest
    from app.netpolicy import UrlPolicyError
    with pytest.raises(UrlPolicyError):
        r.fetch(r._client(), "http://169.254.169.254/latest/meta-data")


def test_researcher_respects_fetch_bound():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "duckduckgo" in request.url.host:
            return httpx.Response(200, text=SEARCH_HTML,
                                  headers={"content-type": "text/html"})
        calls["n"] += 1
        return httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})

    r = LiveWebResearcher(max_fetches=1, transport=httpx.MockTransport(handler))
    r.research(["q"])
    assert calls["n"] <= 1


# ---------------- LLM adapter consumes the live corpus ----------------
def test_llm_adapter_cites_live_corpus_and_strips_invented():
    import asyncio
    from app.adapters import AgentRequest
    from app.config import load_settings
    from app.llm_adapter import LLMResearchAdapter

    adapter = LLMResearchAdapter(load_settings())

    class FakeResult:
        provider_request_id = "fake-1"
        usage = {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0}
        data = {"title": "Live-grounded candidate", "summary": "Built from live sources.",
                "claims": [{"text": "Live claim A", "source_ids": ["src_web_aaa111"],
                            "confidence_category": "supported"},
                           {"text": "Invented claim", "source_ids": ["src_made_up"],
                            "confidence_category": "supported"}],
                "assumptions": [], "risks": [], "estimated_cost_usd": 1,
                "time_to_learn": "2 weeks"}

    class FakeClient:
        async def chat_json(self, system, user, **kw):
            assert "src_web_aaa111" in user  # live library reached the prompt
            assert "src_seed_smb_survey_2026" not in user
            return FakeResult()

    adapter.client = FakeClient()
    req = AgentRequest(mission_id="m", task_id="t", objective="obj",
                       deliverables=["d"], output_schema={},
                       sources=[{"source_id": "src_web_aaa111", "kind": "secondary",
                                 "published_at": "2026-03-14", "excerpt": "Live evidence A"}])
    resp = asyncio.run(adapter.invoke(req, {"agent_id": "agent.research.alpha",
                                            "version": "1.3.0", "strategy": "niche-first"}))
    claims = resp.output["claims"]
    assert claims[0].source_ids == ["src_web_aaa111"]
    assert claims[1].source_ids == [] and claims[1].confidence_category == "unsupported"
    assert any("Stripped invented" in w for w in resp.warnings)
