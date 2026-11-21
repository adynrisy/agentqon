"""v21 Track 2 — mission modes: each mode runs end-to-end against the
stub LLM and produces its shaped deliverable; the mode is recorded on
the mission contract and visible in events; role packs register
lazily per tenant (the seeded roster count is untouched)."""
from __future__ import annotations

import copy

from app.modes import MODES, ensure_pack, resolve_phases, suggest_mode
from app.store import Store
from tests.conftest import MISSION_BODY, hdr


def _run(client, tenant, key, objective, extra_contract=None):
    body = copy.deepcopy(MISSION_BODY)
    body["title"] = objective[:60]
    body["contract"]["objective"] = objective
    if extra_contract:
        body["contract"].update(extra_contract)
    r = client.post("/v1/missions", headers=hdr(tenant, key), json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant), json={})
    assert run.status_code == 200, run.text
    return client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()


def _mode_events(detail, kind):
    return [e for e in detail["events"] if e["event_type"] == kind]


# ------------------------------------------------------------ unit level
def test_suggest_mode_is_conservative():
    assert suggest_mode("Should we buy or sell shares of ACME after its earnings report?") == "trading_firm"
    assert suggest_mode("Build a web app for tracking invoices") == "software_company"
    assert suggest_mode("Compare launch strategies for a new SMB invoicing product") is None
    assert suggest_mode("Write a poem about autumn") is None


def test_packs_register_with_attributed_personas(tmp_path):
    store = Store(str(tmp_path / "packs.db"))
    tenant = "org_packs"
    expected = {"metagpt": 13, "tradingagents": 12, "chatdev": 9, "camel": 7}
    for pack, n in expected.items():
        assert ensure_pack(store, tenant, pack) == n
        assert ensure_pack(store, tenant, pack) == n  # idempotent
    agents = {a["agent_id"] for a in store.list_agents(tenant)}
    assert "pack.metagpt.product-manager" in agents
    assert "pack.tradingagents.portfolio-manager" in agents
    body = store.get_persona("pack.camel.ai-assistant")
    assert body and "Provenance" in body and "camel-ai/camel" in body


def test_build_chain_phases_are_configurable():
    mode = {"id": "build_chain", **MODES["build_chain"]}
    default = resolve_phases(mode, {})
    configured = resolve_phases(mode, {"mode_config": {"phases": [
        {"key": "only", "title": "Only Phase", "role": "programmer",
         "task_type": "code", "instruction": "Write it."}]}})
    assert len(configured) == 1 and configured[0]["role"] == "programmer"
    bogus = resolve_phases(mode, {"mode_config": {"phases": [
        {"role": "not-a-role", "task_type": "code"}]}})
    assert bogus == default  # invalid config falls back, never shrinks silently


# ------------------------------------------------------------- API level
def test_modes_endpoint_lists_four_modes(client, tenant):
    r = client.get("/v1/modes", headers=hdr(tenant))
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["modes"]}
    assert ids == {"software_company", "trading_firm", "build_chain", "society"}
    sw = next(m for m in r.json()["modes"] if m["id"] == "software_company")
    assert [p["key"] for p in sw["phases"]] == ["prd", "design", "tasks", "code", "review"]


def test_software_company_mode_end_to_end(client, tenant):
    d = _run(client, tenant, "v21mode-sw01",
             "Build a web app for tracking invoices",
             {"mode": "software_company"})
    assert d["mission"]["status"] == "completed"
    assert d["mission"]["contract"]["mode"] == "software_company"
    assert _mode_events(d, "ModeSelected")
    phases = _mode_events(d, "ModePhaseCompleted")
    assert [e["payload"]["phase"] for e in phases] == \
        ["prd", "design", "tasks", "code", "review"]
    assert all(e["payload"]["agent"].startswith("pack.metagpt.")
               for e in phases)
    cands = d["candidates"]
    assert len(cands) == 1 and cands[0]["strategy"] == "software_company"
    doc = cands[0]["deliverable"]
    assert "Software Company" in doc and "## 1. Product Requirements" in doc
    assert "## 5. QA Review" in doc


def test_trading_firm_mode_end_to_end(client, tenant):
    d = _run(client, tenant, "v21mode-tf01",
             "Should we buy shares of ACME Corp after its earnings report?",
             {"mode": "trading_firm"})
    assert d["mission"]["status"] == "completed"
    phases = _mode_events(d, "ModePhaseCompleted")
    keys = [e["payload"]["phase"] for e in phases]
    assert keys[0] == "fundamentals" and keys[-1] == "decision"
    assert "bull" in keys and "bear" in keys
    doc = d["candidates"][0]["deliverable"]
    assert "Trading Firm" in doc and "Final Decision" in doc


def test_society_mode_end_to_end(client, tenant):
    d = _run(client, tenant, "v21mode-so01",
             "Organise the team's shared drive into a sane folder structure",
             {"mode": "society"})
    assert d["mission"]["status"] == "completed"
    keys = [e["payload"]["phase"] for e in _mode_events(d, "ModePhaseCompleted")]
    assert keys == ["framing", "execution", "challenge", "delivery"]
    assert "Agent Society" in d["candidates"][0]["deliverable"]


def test_build_chain_mode_with_custom_phases(client, tenant):
    d = _run(client, tenant, "v21mode-bc01",
             "Create a small command line tool that renames photos by date",
             {"mode": "build_chain",
              "mode_config": {"phases": [
                  {"key": "spec", "title": "Spec", "role": "chief-product-officer",
                   "task_type": "plan", "instruction": "Specify the tool."},
                  {"key": "code", "title": "Code", "role": "programmer",
                   "task_type": "code", "instruction": "Write the tool."}]}})
    assert d["mission"]["status"] == "completed"
    keys = [e["payload"]["phase"] for e in _mode_events(d, "ModePhaseCompleted")]
    assert keys == ["spec", "code"]


def test_classifier_default_assigns_trading_mode(client, tenant):
    body = copy.deepcopy(MISSION_BODY)
    body["title"] = "ACME position"
    body["contract"]["objective"] = \
        "Should we invest in shares of ACME Corp this quarter?"
    r = client.post("/v1/missions", headers=hdr(tenant, "v21mode-cls1"), json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    d = client.get(f"/v1/missions/{mid}", headers=hdr(tenant)).json()
    assert d["mission"]["contract"]["mode"] == "trading_firm"
    created = [e for e in d["events"] if e["event_type"] == "MissionCreated"]
    assert created and created[0]["payload"].get("mode") == "trading_firm"


def test_plain_mission_gets_no_mode(client, tenant):
    d = _run(client, tenant, "v21mode-none1",
             "Compare launch strategies for a new SMB invoicing product")
    assert d["mission"]["contract"].get("mode") is None
    assert not _mode_events(d, "ModeSelected")
    assert _mode_events(d, "PanelSelected")  # standard pipeline intact
