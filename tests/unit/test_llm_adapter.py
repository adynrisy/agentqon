"""Real LLM adapter tests (Ch.7) against a stub OpenAI-compatible transport,
plus the v20 no-model behaviour: with nothing connected, a mission run
fails with `model_not_connected` instead of falling back to canned
output (there is no canned output any more)."""
import asyncio
import json
import tempfile

import httpx

from app.adapters import AgentRequest
from app.config import Settings
from app.llm_adapter import LLMCriticAdapter, LLMResearchAdapter
from app.registry import SEED_AGENTS, seed_registry
from app.sources import SEED_SOURCES

SETTINGS = Settings(llm_base_url="http://stub.test/v1",
                    llm_api_key="test-key", llm_model="stub-model",
                    llm_price_in_per_m=10.0, llm_price_out_per_m=30.0)

RESEARCH_JSON = {
    "title": "Candidate — niche-first (LLM)",
    "summary": "Win one SMB segment first, then expand.",
    "claims": [
        {"text": "68% of surveyed SMBs adopted via a community recommendation.",
         "source_ids": ["src_seed_smb_survey_2026"], "confidence_category": "supported"},
        {"text": "An invented statistic with an invented source.",
         "source_ids": ["src_invented_999"], "confidence_category": "supported"},
    ],
    "assumptions": ["A reachable community exists"],
    "risks": ["Segment too small"],
    "estimated_cost_usd": 900,
    "time_to_learn": "5 weeks",
}

CRITIC_JSON = {"critiques": [
    {"target_candidate_id": "cand_x", "target_claim": "Some claim", "issue": "Weak source",
     "evidence_or_test": "kind=opinion", "severity": "high",
     "suggested_correction": "Add primary data", "confidence": "high"},
    {"bogus": True},  # malformed — must be dropped with a warning, not crash
]}


def stub_transport(captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["model"] = body.get("model")
        captured["has_auth"] = request.headers.get("authorization") == "Bearer test-key"
        system = body["messages"][0]["content"]
        payload = CRITIC_JSON if "adversarial critic" in system else RESEARCH_JSON
        return httpx.Response(200, json={
            "id": "chatcmpl-stub",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        })
    return httpx.MockTransport(handler)


def test_llm_research_adapter_parses_and_sanitizes():
    captured = {}
    adapter = LLMResearchAdapter(SETTINGS, transport=stub_transport(captured))
    manifest = next(a.model_dump() for a in SEED_AGENTS if a.strategy == "niche-first")
    req = AgentRequest(mission_id="m", task_id="t", objective="Launch invoicing",
                       deliverables=["Brief"], output_schema={})
    resp = asyncio.run(adapter.invoke(req, manifest))
    assert captured["model"] == "stub-model" and captured["has_auth"]
    out = resp.output
    assert out["strategy"] == "niche-first" and len(out["claims"]) == 2
    good, invented = out["claims"]
    assert good.source_ids == ["src_seed_smb_survey_2026"]
    # The invented source id must be stripped and the claim marked unsupported,
    # so the evaluator — not the model — decides what counts as evidence.
    assert invented.source_ids == [] and invented.confidence_category == "unsupported"
    assert any("src_invented_999" in w for w in resp.warnings)
    # Usage + cost come from the provider's token counts and configured prices.
    assert resp.usage["input_tokens"] == 1000 and resp.usage["output_tokens"] == 500
    assert resp.usage["cost_usd"] == round(1000 / 1e6 * 10 + 500 / 1e6 * 30, 6)


def test_llm_critic_adapter_validates_schema():
    captured = {}
    adapter = LLMCriticAdapter(SETTINGS, transport=stub_transport(captured))
    manifest = next(a.model_dump() for a in SEED_AGENTS if a.strategy == "critic")
    cand = {"candidate_id": "cand_x", "title": "T", "strategy": "niche-first",
            "claims": [{"text": "Some claim", "source_ids": [], "confidence_category": "supported"}]}
    req = AgentRequest(mission_id="m", task_id="t", objective="Launch",
                       deliverables=[], output_schema={"candidates": [cand]})
    resp = asyncio.run(adapter.invoke(req, manifest))
    critiques = resp.output["critiques"]
    assert len(critiques) == 1 and critiques[0]["severity"] == "high"
    assert any("malformed" in w for w in resp.warnings)


def test_local_endpoints_bypass_env_proxies_cloud_does_not():
    from app.llm_adapter import _ChatClient, _is_local_endpoint
    assert _is_local_endpoint("http://127.0.0.1:11434/v1") is True
    assert _is_local_endpoint("http://localhost:1234/v1") is True
    assert _is_local_endpoint("http://192.168.1.50:8000/v1") is True
    assert _is_local_endpoint("https://api.openai.com/v1") is False
    assert _is_local_endpoint("https://openrouter.ai/api/v1") is False
    local = _ChatClient(Settings(llm_base_url="http://127.0.0.1:11434/v1"))
    cloud = _ChatClient(Settings(llm_base_url="https://api.openai.com/v1"))
    assert local.trust_env is False and cloud.trust_env is True


def _mission_store(tmp):
    from app.store import Store
    store = Store(f"{tmp}/f.db", f"{tmp}/artifacts")
    seed_registry(store, "org_t")
    for s in SEED_SOURCES:
        store.put_source("org_t", s.model_dump())
    contract = {"objective": "Compare launch strategies", "deliverables": ["Brief"],
                "acceptance": ["Cited claims"],
                "constraints": {"max_cost_usd": 10},
                "tools": {"allow": ["web.read", "artifact.write"], "deny": []},
                "approval_policy": {"external_side_effects": "required"},
                "data_classification": "internal"}
    mission = store.create_mission("org_t", "tester", "No-model check", contract, 10.0)
    return store, mission


def test_orchestrator_fails_mission_when_no_model_connected():
    """v20 replacement for the old fallback test: an explicitly
    configured endpoint that does not answer means NO model is
    connected — the mission ends FAILED with `model_not_connected`
    on record and zero candidates, rather than completing with
    canned output."""
    from app.orchestrator import Orchestrator
    with tempfile.TemporaryDirectory() as tmp:
        store, mission = _mission_store(tmp)
        dead = Settings(llm_base_url="http://127.0.0.1:9/v1",
                        llm_api_key="ollama", llm_base_url_explicit=True)
        orch = Orchestrator(store, dead)
        detail = asyncio.run(orch.run_mission("org_t", mission["id"]))
        assert detail["mission"]["status"] == "failed"
        assert detail["candidates"] == []
        failed = [e for e in detail["events"] if e["event_type"] == "MissionFailed"]
        assert failed and failed[0]["payload"]["reason"] == "model_not_connected"
        assert any(e["event_type"] == "ModelNotConnected" for e in detail["events"])


def test_orchestrator_model_call_failure_fails_the_run():
    """An endpoint that ANSWERS the /models probe (so the gate passes)
    but refuses chat calls (401): the failure surfaces as an error
    with a ModelCallFailed event — never as substituted output."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import pytest

    from app.orchestrator import Orchestrator

    class RefusingHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, obj):
            body = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.endswith("/models"):
                return self._send(200, {"data": [{"id": "stub-model-1"}]})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            return self._send(401, {"error": "bad key"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), RefusingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store, mission = _mission_store(tmp)
            refusing = Settings(llm_base_url=base, llm_api_key="the-wrong-key",
                                llm_model="stub-model-1", llm_base_url_explicit=True,
                                llm_api_key_explicit=True)
            orch = Orchestrator(store, refusing)
            with pytest.raises(RuntimeError, match="Model call failed"):
                asyncio.run(orch.run_mission("org_t", mission["id"]))
            events = store.list_events("org_t", mission["id"])
            assert any(e["event_type"] == "ModelCallFailed" for e in events)
            assert store.list_candidates("org_t", mission["id"]) == []
    finally:
        server.shutdown()
