"""v20 model-state resolution (app/modelstate.py): BYOK → env →
OmniRoute → Ollama, with local endpoints probed — a configured-but-
dead local model is NOT connected, and probing one never hangs."""
import time

from app import modelstate
from app.config import Settings
from tests import stub_llm


def test_probe_of_dead_endpoint_returns_none_fast():
    started = time.monotonic()
    assert modelstate.probe_models("http://127.0.0.1:9/v1") is None
    assert time.monotonic() - started < 5  # short timeout, no hang


def test_explicit_dead_local_endpoint_is_not_connected():
    dead = Settings(llm_base_url="http://127.0.0.1:9/v1",
                    llm_base_url_explicit=True)
    state = modelstate.platform_state(dead)
    assert state.connected is False
    assert "127.0.0.1:9" in state.message


def test_auto_detect_with_nothing_listening_is_not_connected(monkeypatch):
    monkeypatch.setattr(modelstate, "OMNIROUTE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(modelstate, "OLLAMA_URL", "http://127.0.0.1:99/v1")
    state = modelstate.platform_state(Settings())
    assert state.connected is False
    assert state.message  # the human fixes are always attached


def test_stub_endpoint_resolves_connected_via_env():
    state = modelstate.platform_state(stub_llm.settings())
    assert state.connected is True
    assert state.source == "env"
    assert state.model == stub_llm.STUB_MODEL


def test_cloud_endpoint_with_key_is_connected_without_probe():
    cloud = Settings(llm_base_url="https://api.example.invalid/v1",
                     llm_api_key="real-key", llm_model="some-model",
                     llm_base_url_explicit=True, llm_api_key_explicit=True)
    state = modelstate.platform_state(cloud)
    assert state.connected is True and state.source == "env"
    assert state.model == "some-model"


def test_cloud_endpoint_without_key_is_not_connected():
    cloud = Settings(llm_base_url="https://api.example.invalid/v1",
                     llm_api_key="ollama", llm_model="some-model",
                     llm_base_url_explicit=True)
    state = modelstate.platform_state(cloud)
    assert state.connected is False
    assert "AGENTQON_LLM_API_KEY" in state.message


class _FakeStore:
    def __init__(self, conns, catalog):
        self._conns, self._catalog = conns, catalog

    def list_connections(self, tenant_id):
        return self._conns

    def list_catalog(self, tenant_id, connection_id=None):
        return self._catalog


def test_byok_state_prefers_configured_model_over_first_catalog_row():
    """Regression (audit 2026-10-12): a gateway catalog lists an
    image model first alphabetically; model-state must name the
    tenant's real chat model, not that first row."""
    conns = [{"id": "c_gw", "state": "active", "kind": "direct_provider",
              "base_url": "http://localhost:20128/v1"},
             {"id": "c_loc", "state": "active", "kind": "local_endpoint",
              "base_url": "http://127.0.0.1:11434/v1"}]
    catalog = [{"connection_id": "c_gw", "model_id": "aihorde/2DN",
                "capabilities": {"chat": "advertised"}},
               {"connection_id": "c_loc", "model_id": "qwen2.5:1.5b",
                "capabilities": {"chat": "advertised"}}]
    state = modelstate.resolve_state(Settings(), _FakeStore(conns, catalog),
                                     "org_demo")
    assert state.connected is True and state.source == "byok"
    assert state.model == "qwen2.5:1.5b"
    assert state.base_url == "http://127.0.0.1:11434/v1"


def test_byok_state_matches_provider_prefixed_default_model():
    conns = [{"id": "c_gw", "state": "active", "kind": "direct_provider",
              "base_url": "http://localhost:20128/v1"}]
    catalog = [{"connection_id": "c_gw", "model_id": "aihorde/2DN",
                "capabilities": {"chat": "advertised"}},
               {"connection_id": "c_gw",
                "model_id": "ollama-local/qwen2.5:1.5b",
                "capabilities": {"chat": "advertised"}}]
    state = modelstate.resolve_state(Settings(), _FakeStore(conns, catalog),
                                     "org_demo")
    assert state.model == "ollama-local/qwen2.5:1.5b"
