"""Library import endpoint: admin-gated, installs the subagents
mega-library into the caller's workspace only (mirrors
test_agency_api.py style, with a fixture library via env var)."""
from __future__ import annotations

from tests.conftest import hdr


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_import_library_endpoint_scoped(client, tenant, tmp_path,
                                        monkeypatch):
    root = tmp_path / "lib"
    _write(root, "acme__pack/agents/researcher.md",
           "---\nname: researcher\ndescription: Market research analyst."
           "\n---\n\nYou research markets.\n")
    _write(root, "acme__pack/personas/chef/SOUL.md",
           "You cook with fire.\n")
    _write(root, "acme__pack/personas/chef/IDENTITY.md",
           "Name: Chef\n")
    monkeypatch.setenv("OMNIMIND_SUBAGENT_LIBRARY", str(root))
    r = client.post("/v1/agents/import-library", headers=hdr(tenant))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and body["lifecycle"] == "active"
    assert body["duplicates_dropped"] == 0
    agents = client.get("/v1/agents", headers=hdr(tenant)).json()["agents"]
    lib = [a for a in agents if a["agent_id"].startswith("lib.")]
    assert len(lib) == 2
    assert all(a["origin"] == "subagents-library" for a in lib)
    researcher = next(a for a in lib if "researcher" in a["agent_id"])
    assert "research.web" in researcher["capabilities"]
    # the suitability ranking can see library agents: the endpoint
    # serves a top-12 window over rank_panel, and with the full agency
    # roster auto-seeded (v20.1) two fixture agents rank below that
    # window for "research" — so assert on the ranking itself, over
    # exactly the agent list the API serves.
    from app.main import store as api_store
    from app.suitability import rank_panel
    full = rank_panel(api_store.list_agents(tenant), "research",
                      api_store.list_agent_stats(tenant), k=10000)
    assert any(a["agent_id"].startswith("lib.") for a in full)
    # v20.1: a fresh tenant auto-seeds its OWN copy of the library on
    # first touch (resolved through the same library_dir, here the
    # env-var fixture) — the rows are scoped per tenant, not shared
    # with the tenant that imported first.
    other = client.get("/v1/agents", headers=hdr("org_nolib")).json()["agents"]
    other_lib = [a for a in other if a["agent_id"].startswith("lib.")]
    assert len(other_lib) == 2
    assert all(a["origin"] == "subagents-library" for a in other_lib)


def test_import_library_missing_library_is_clean_error(client, tenant,
                                                        tmp_path,
                                                        monkeypatch):
    monkeypatch.setattr("app.library.library_dir",
                        lambda: tmp_path / "nope")
    r = client.post("/v1/agents/import-library", headers=hdr(tenant))
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
