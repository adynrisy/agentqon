"""Phase 5 integration: live-research missions through the gateway.

A stub research provider stands in for the real web (the provider's own
HTTP behaviour is unit-tested, and a true live run is recorded in
PROJECT_STATUS). Verifies: corpus reaches the agents and gets cited,
provenance is stored, confidential missions stay on the seed corpus,
and provider failure falls back visibly.
"""
from __future__ import annotations

import pytest

import app.main as main
from tests.conftest import MISSION_BODY, hdr

LIVE_SOURCES = [
    {"source_id": "src_web_aaa111", "title": "Live Report A", "kind": "secondary",
     "published_at": "2026-03-14", "retrieved_at": "2026-10-10T00:00:00Z",
     "excerpt": "Live evidence A about invoicing adoption.",
     "url": "https://example.com/a", "publisher": "example.com",
     "content_hash": "a" * 64, "retrieval_method": "live-web:duckduckgo"},
    {"source_id": "src_web_bbb222", "title": "Live Report B", "kind": "secondary",
     "published_at": "2026-05-01", "retrieved_at": "2026-10-10T00:00:00Z",
     "excerpt": "Live evidence B about retention.",
     "url": "https://example.org/b", "publisher": "example.org",
     "content_hash": "b" * 64, "retrieval_method": "live-web:duckduckgo"},
    {"source_id": "src_web_ccc333", "title": "Live Report C", "kind": "secondary",
     "published_at": "2026-06-01", "retrieved_at": "2026-10-10T00:00:00Z",
     "excerpt": "Live evidence C about partnerships.",
     "url": "https://example.net/c", "publisher": "example.net",
     "content_hash": "c" * 64, "retrieval_method": "live-web:duckduckgo"},
]


class StubProvider:
    def __init__(self, sources=None, fail=False):
        self.sources = sources if sources is not None else LIVE_SOURCES
        self.fail = fail
        self.calls = 0

    def research(self, queries):
        self.calls += 1
        if self.fail:
            raise RuntimeError("network down")
        return self.sources


@pytest.fixture()
def live_gateway(monkeypatch):
    stub = StubProvider()
    monkeypatch.setattr(main.orchestrator.gateway, "research_provider", stub)
    monkeypatch.setattr(main.orchestrator.gateway, "research_mode", "live")
    # the gateway caches per mission id — fresh missions are unaffected
    return stub


def _run(client, tenant, body=None, key="live-res-001"):
    created = client.post("/v1/missions", headers=hdr(tenant, key),
                          json=body or MISSION_BODY)
    mid = created.json()["mission_id"]
    ran = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert ran.status_code == 200, ran.text
    return ran.json()


def _event_types(detail):
    return [e["event_type"] for e in detail["events"]]


def _cited_ids(detail):
    ids = set()
    for cand in detail["candidates"]:
        for claim in cand["claims"]:
            ids.update(claim["source_ids"])
    return ids


def test_live_research_mission_cites_live_sources(client, tenant, live_gateway):
    detail = _run(client, tenant)
    assert detail["mission"]["status"] == "completed"
    assert "ResearchCompleted" in _event_types(detail)
    cited = _cited_ids(detail)
    assert cited & {"src_web_aaa111", "src_web_bbb222", "src_web_ccc333"}
    assert not any(s.startswith("src_seed_") for s in cited)
    # provenance is stored in the tenant library
    sources = client.get("/v1/sources", headers=hdr(tenant)).json()["sources"]
    live = [s for s in sources if s["source_id"] == "src_web_aaa111"]
    assert live and live[0]["url"] == "https://example.com/a"
    assert live[0]["retrieval_method"] == "live-web:duckduckgo"


def test_confidential_mission_skips_live_web(client, tenant, live_gateway):
    body = {**MISSION_BODY, "contract": {**MISSION_BODY["contract"],
                                         "data_classification": "confidential"}}
    detail = _run(client, tenant, body=body, key="live-res-conf")
    assert detail["mission"]["status"] == "completed"
    assert "ResearchSkipped" in _event_types(detail)
    assert live_gateway.calls == 0  # provider never invoked: no egress
    assert all(s.startswith("src_seed_") for s in _cited_ids(detail))


def test_live_web_override_forces_research_for_confidential(client, tenant, live_gateway):
    contract = {**MISSION_BODY["contract"], "data_classification": "confidential",
                "tools": {**MISSION_BODY["contract"]["tools"], "live_web": True}}
    detail = _run(client, tenant, body={**MISSION_BODY, "contract": contract},
                  key="live-res-override")
    assert "ResearchCompleted" in _event_types(detail)
    assert live_gateway.calls >= 1


def test_provider_failure_falls_back_visibly(client, tenant, monkeypatch):
    stub = StubProvider(fail=True)
    monkeypatch.setattr(main.orchestrator.gateway, "research_provider", stub)
    monkeypatch.setattr(main.orchestrator.gateway, "research_mode", "live")
    detail = _run(client, tenant, key="live-res-fail")
    assert detail["mission"]["status"] == "completed"
    assert "ResearchFallback" in _event_types(detail)
    assert all(s.startswith("src_seed_") for s in _cited_ids(detail))


def test_event_stream_delivers_events(client, tenant):
    detail = _run(client, tenant, key="live-res-stream")
    mid = detail["mission"]["id"]
    with client.stream("GET", f"/v1/missions/{mid}/events/stream",
                       headers=hdr(tenant)) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "MissionCompleted" in body:
                break
    assert "MissionCreated" in body and "MissionCompleted" in body
