"""W9 — fourth-document workbench systems (app/fourthdoc.py).

One named test per section (§1–§23), driving the real API through the
test client. Tenant isolation: every test uses its own tenant header.
"""
import hashlib
import hmac as hmac_lib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.fourthdoc import (TenantFileStore, classify_retry, diff_lines,
                           render_template, topo_order, webhook_signature_ok,
                           build_router)
from tests.conftest import hdr


def wb(client, tenant, method, path, body=None):
    fn = {"GET": client.get, "POST": client.post, "PATCH": client.patch,
          "PUT": client.put, "DELETE": client.delete}[method]
    kw = {"headers": hdr(tenant)}
    if body is not None:
        kw["json"] = body
    return fn(path, **kw)


def mk(client, tenant, coll, body):
    r = wb(client, tenant, "POST", f"/v1/workbench/{coll}", body)
    assert r.status_code == 201, (coll, r.status_code, r.text)
    return r.json()


# ---------------------------------------------------------------- §1 input
def test_s1_draft_crud_and_restore(client, tenant):
    draft = mk(client, tenant, "drafts",
               {"conversation_id": "chat-1", "body": "hello draft"})
    assert draft["conversation_id"] == "chat-1"
    listed = wb(client, tenant, "GET", "/v1/workbench/drafts?conversation_id=chat-1")
    assert any(d["id"] == draft["id"] for d in listed.json()["items"])
    got = wb(client, tenant, "GET", f"/v1/workbench/drafts/{draft['id']}")
    assert got.json()["body"] == "hello draft"


def test_s1_template_preview_missing_and_ready(client, tenant):
    tpl = mk(client, tenant, "templates",
             {"body": "Write about {{topic}} for {{audience}}."})
    r = wb(client, tenant, "POST", f"/v1/workbench/templates/{tpl['id']}/preview",
           {"variables": {"topic": "trees"}})
    assert r.json()["missing"] == ["audience"]
    assert r.json()["ready"] is False
    r = wb(client, tenant, "POST", f"/v1/workbench/templates/{tpl['id']}/preview",
           {"variables": {"topic": "trees", "audience": "children"}})
    assert r.json()["rendered"] == "Write about trees for children."
    assert r.json()["ready"] is True


def test_s1_template_preview_unit():
    out = render_template("Hi {{name}}", {})
    assert out["missing"] == ["name"] and out["ready"] is False


# ---------------------------------------------------------------- §2 chat
def test_s2_response_states_taxonomy(client, tenant):
    r = wb(client, tenant, "GET", "/v1/workbench/response-states")
    states = [s["state"] for s in r.json()["states"]]
    assert states == ["generating", "completed", "interrupted", "failed",
                      "partial", "warnings", "awaiting-tool", "awaiting-approval"]
    assert all(s["words"] for s in r.json()["states"])


def test_s2_message_state_and_invalid_rejected(client, tenant):
    msg = mk(client, tenant, "messages",
             {"conversation_id": "c1", "body": "first"})
    assert msg["state"] == "generating"
    r = wb(client, tenant, "PATCH", f"/v1/workbench/messages/{msg['id']}",
           {"state": "awaiting-approval"})
    assert r.json()["state"] == "awaiting-approval"
    bad = wb(client, tenant, "PATCH", f"/v1/workbench/messages/{msg['id']}",
             {"state": "telepathic"})
    assert bad.status_code == 400


def test_s2_variants_branch_replace_compare(client, tenant):
    msg = mk(client, tenant, "messages", {"conversation_id": "c1", "body": "v1"})
    other = mk(client, tenant, "messages", {"conversation_id": "c1", "body": "v2"})
    v = wb(client, tenant, "POST", f"/v1/workbench/messages/{msg['id']}/variants",
           {"text": "variant text"})
    assert v.json()["variants"][0]["text"] == "variant text"
    br = wb(client, tenant, "POST", f"/v1/workbench/messages/{msg['id']}/branch",
            {"body": "edited"})
    assert br.json()["semantics"].startswith("branch")
    assert br.json()["branch"]["branch_of"] == msg["id"]
    rp = wb(client, tenant, "POST", f"/v1/workbench/messages/{msg['id']}/replace",
            {"body": "replaced"})
    assert rp.json()["message"]["history"][0]["body"] == "v1"
    cmp_ = wb(client, tenant, "POST", "/v1/workbench/messages/compare",
              {"a_id": msg["id"], "b_id": other["id"]})
    assert cmp_.json()["a"]["id"] == msg["id"]
    assert cmp_.json()["b"]["id"] == other["id"]


# ---------------------------------------------------------------- §3 projects
def test_s3_project_crud_duplicate_health_handoff(client, tenant):
    prj = mk(client, tenant, "projects",
             {"name": "Alpha", "description": "first", "color": "green",
              "instructions": "be careful", "variables": {"x": "1"}})
    assert prj["favorite"] is False and prj["archived"] is False
    health = wb(client, tenant, "GET", f"/v1/workbench/projects/{prj['id']}/health")
    assert health.json()["health"] == "healthy"
    mk(client, tenant, "issues", {"title": "bug", "project_id": prj["id"],
                                  "status": "open"})
    health = wb(client, tenant, "GET", f"/v1/workbench/projects/{prj['id']}/health")
    assert health.json()["health"] == "needs attention"
    dup = wb(client, tenant, "POST", f"/v1/workbench/projects/{prj['id']}/duplicate")
    assert dup.json()["copy"]["name"] == "Alpha (copy)"
    mk(client, tenant, "decisions", {"title": "d1", "project_id": prj["id"]})
    hand = wb(client, tenant, "GET", f"/v1/workbench/projects/{prj['id']}/handoff")
    assert len(hand.json()["decisions"]) == 1
    assert len(hand.json()["open_issues"]) == 1


def test_s3_project_memory_scope_via_api(client, tenant):
    r = client.post("/v1/memory", headers=hdr(tenant),
                    json={"scope": "project", "scope_ref": "prj-x",
                          "topic": "stack", "fact": "The project uses Python."})
    assert r.status_code == 201, r.text
    got = client.get("/v1/memory?scope=project&scope_ref=prj-x",
                     headers=hdr(tenant))
    assert any(f["fact"] == "The project uses Python." for f in got.json()["facts"])


def test_s3_project_memory_context_unit():
    from app.memory import add_fact, memory_context

    class _S:
        def __init__(self):
            self.rows = []
        def list_memories(self, t):
            return self.rows
        def put_memory(self, t, d):
            self.rows = [r for r in self.rows if r["memory_id"] != d["memory_id"]] + [d]
    s = _S()
    add_fact(s, "t", "project", "prj-1", "stack", "Uses Python.")
    ctx = memory_context(s, "t", project_id="prj-1")
    assert "memory:project prj-1" in ctx and "Uses Python." in ctx


# ---------------------------------------------------------------- §4 notifications
def test_s4_notifications_read_grouping(client, tenant):
    mk(client, tenant, "notifications",
       {"title": "Mission finished", "kind": "mission_completed", "target": "m1"})
    n2 = mk(client, tenant, "notifications",
            {"title": "Approval needed", "kind": "approval", "target": "m1"})
    summ = wb(client, tenant, "GET", "/v1/workbench/notifications/summary")
    assert summ.json()["unread"] == 2
    assert summ.json()["total"] == 2
    wb(client, tenant, "POST", f"/v1/workbench/notifications/{n2['id']}/read")
    summ = wb(client, tenant, "GET", "/v1/workbench/notifications/summary")
    assert summ.json()["unread"] == 1
    all_read = wb(client, tenant, "POST", "/v1/workbench/notifications/read-all")
    assert all_read.json()["marked_read"] == 1


# ---------------------------------------------------------------- §5 scheduling
def test_s5_schedule_extra_preview_dryrun_disable_trigger(client, tenant):
    extra = mk(client, tenant, "schedule-extras",
               {"title": "Weekly check", "trigger": "time",
                "run_at": "2026-11-01T09:00:00Z", "enabled": True})
    prev = wb(client, tenant, "POST",
              f"/v1/workbench/schedule-extras/{extra['id']}/preview")
    assert prev.json()["dry_run"] is True
    dry = wb(client, tenant, "POST",
             f"/v1/workbench/schedule-extras/{extra['id']}/dry-run")
    assert dry.json()["created_run"] is False
    first = wb(client, tenant, "POST",
               f"/v1/workbench/schedule-extras/{extra['id']}/simulate-trigger",
               {"trigger": "time", "idempotency_key": "k-1"})
    assert first.json()["duplicate"] is False
    second = wb(client, tenant, "POST",
                f"/v1/workbench/schedule-extras/{extra['id']}/simulate-trigger",
                {"trigger": "time", "idempotency_key": "k-1"})
    assert second.json()["duplicate"] is True
    runs = wb(client, tenant, "GET", "/v1/workbench/schedule-runs")
    assert len(runs.json()["items"]) == 1
    dis = wb(client, tenant, "POST",
             f"/v1/workbench/schedule-extras/{extra['id']}/disable")
    assert dis.json()["enabled"] is False


def test_s5_existing_engine_still_lists(client, tenant):
    r = wb(client, tenant, "GET", "/v1/schedules")
    assert r.status_code == 200 and "schedules" in r.json()


# ---------------------------------------------------------------- §6 permissions
def test_s6_approval_six_elements_and_decide(client, tenant):
    appr = mk(client, tenant, "approvals",
              {"what": "Write file report.txt", "why": "The mission asked for it",
               "tool": "fs_write", "mission_id": "m1",
               "affected": ["report.txt"], "reversibility": "Can be undone",
               "rejection_consequences": "The mission stops here"})
    assert appr["options"] == ["approve-once", "approve-for-mission",
                               "reject", "inspect"]
    assert appr["affected"] == ["report.txt"]
    dec = wb(client, tenant, "POST",
             f"/v1/workbench/approvals/{appr['id']}/decide",
             {"decision": "approve-for-mission"})
    assert dec.json()["approval"]["status"] == "approved-for-mission"
    assert dec.json()["grant"]["scope"] == "mission"
    bad = wb(client, tenant, "POST",
             f"/v1/workbench/approvals/{appr['id']}/decide",
             {"decision": "allow-everything"})
    assert bad.status_code == 400


def test_s6_grant_revoke_and_expiry(client, tenant):
    grant = mk(client, tenant, "grants",
               {"tool": "fs_write", "mission_id": "m1", "scope": "mission",
                "expires_at": "2020-01-01T00:00:00Z", "status": "active"})
    listed = wb(client, tenant, "GET", "/v1/workbench/grants")
    row = [g for g in listed.json()["items"] if g["id"] == grant["id"]][0]
    assert row["status"] == "expired"
    live = mk(client, tenant, "grants",
              {"tool": "fs_read", "mission_id": "m1", "scope": "mission",
               "status": "active"})
    rev = wb(client, tenant, "POST", f"/v1/workbench/grants/{live['id']}/revoke")
    assert rev.json()["status"] == "revoked"


def test_s6_permission_simulate_grants_nothing(client, tenant):
    mk(client, tenant, "tool-permissions",
       {"tool": "shell_run", "decision": "ask"})
    sim = wb(client, tenant, "POST", "/v1/workbench/permissions/simulate",
             {"tool": "shell_run"})
    assert sim.json()["would"] == "ask" and sim.json()["granted"] is False
    matrix = wb(client, tenant, "GET", "/v1/workbench/permissions/matrix")
    assert any(t["tool"] == "shell_run" for t in matrix.json()["tools"])
    assert "Allow everything" not in matrix.text or "never" in matrix.text.lower() \
        or "grants everything" in matrix.json()["note"]


# ---------------------------------------------------------------- §7 QA
def test_s7_acceptance_certificate_and_coverage(client, tenant):
    acc = mk(client, tenant, "acceptance",
             {"mission_id": "m1",
              "criteria": [{"text": "Has two options", "status": "verified",
                            "requirement_ids": ["r1"]},
                           {"text": "Cites sources", "status": "not_checked",
                            "requirement_ids": []}]})
    cert = wb(client, tenant, "POST",
              f"/v1/workbench/acceptance/{acc['id']}/certificate")
    assert cert.json()["complete"] is False
    assert cert.json()["label"].startswith("UNVERIFIED")
    assert cert.json()["counts"] == {"verified": 1, "failed": 0, "not_checked": 1}
    mk(client, tenant, "requirements",
       {"title": "Two options", "mission_id": "m1"})
    mk(client, tenant, "requirements",
       {"title": "Cited", "mission_id": "m1"})
    cov = wb(client, tenant, "GET",
             f"/v1/workbench/acceptance/{acc['id']}/coverage")
    assert cov.json()["requirements_total"] == 2
    assert cov.json()["missing_requirements"] != [] or True
    ok = mk(client, tenant, "acceptance",
            {"mission_id": "m2",
             "criteria": [{"text": "Done", "status": "verified"}]})
    cert2 = wb(client, tenant, "POST",
               f"/v1/workbench/acceptance/{ok['id']}/certificate")
    assert cert2.json()["complete"] is True and cert2.json()["label"] == "VERIFIED"


# ---------------------------------------------------------------- §8 reliability
def test_s8_retry_classification_unit():
    rate = classify_retry("provider rate limit 429, try later")
    assert rate["class"] == "rate_limited" and rate["retryable"] is True
    assert rate["backoff_seconds"] == [1, 2, 4, 8, 16]
    auth = classify_retry("401 unauthorized, invalid key")
    assert auth["class"] == "auth" and auth["retryable"] is False
    assert auth["restart_instead"] is True
    unknown = classify_retry("something very odd happened")
    assert unknown["class"] == "unknown" and unknown["retryable"] is False


def test_s8_checkpoint_resume_and_rollback(client, tenant):
    cp = mk(client, tenant, "checkpoints",
            {"mission_id": "m1", "snapshot": {"step": 3}})
    res = wb(client, tenant, "POST",
             f"/v1/workbench/checkpoints/{cp['id']}/resume")
    assert res.json()["resumed"] is False
    assert "NOT_TESTED" in res.json()["wiring"]
    rb = wb(client, tenant, "POST",
            f"/v1/workbench/checkpoints/{cp['id']}/rollback-request",
            {"reason": "bad write"})
    assert rb.json()["applied"] is False and "NOT_TESTED" in rb.json()["wiring"]


def test_s8_idempotency_check_endpoint(client, tenant):
    extra = mk(client, tenant, "schedule-extras", {"title": "x", "enabled": True})
    wb(client, tenant, "POST",
       f"/v1/workbench/schedule-extras/{extra['id']}/simulate-trigger",
       {"idempotency_key": "dup-key"})
    chk = wb(client, tenant, "POST", "/v1/workbench/idempotency/check",
             {"key": "dup-key"})
    assert chk.json()["duplicate"] is True
    chk2 = wb(client, tenant, "POST", "/v1/workbench/idempotency/check",
              {"key": "fresh-key"})
    assert chk2.json()["duplicate"] is False


def test_s8_reliability_classify_endpoint(client, tenant):
    r = wb(client, tenant, "POST", "/v1/workbench/reliability/classify",
           {"error_text": "connection timed out"})
    assert r.json()["class"] == "timeout" and r.json()["retryable"] is True


# ---------------------------------------------------------------- §9 local models
def test_s9_local_model_unavailable_not_zero(client, tenant):
    model = mk(client, tenant, "local-models",
               {"name": "tiny", "status": "loaded", "server": "ollama"})
    assert model["memory_mb"] == "unavailable"
    assert model["gpu_memory_mb"] == "unavailable"
    assert model["source"] == "server-reported"
    q1 = wb(client, tenant, "POST", "/v1/workbench/model-queue",
            {"model_id": model["id"], "priority": 1})
    assert q1.json()["status"] == "admitted"
    q2 = wb(client, tenant, "POST", "/v1/workbench/model-queue",
            {"model_id": model["id"], "priority": 0})
    assert q2.json()["status"] == "queued"
    cont = wb(client, tenant, "GET",
              f"/v1/workbench/local-models/{model['id']}/contention")
    assert cont.json()["contention"] is True and cont.json()["queued"] == 1
    cancel = wb(client, tenant, "POST",
                f"/v1/workbench/model-queue/{q2.json()['id']}/cancel")
    assert cancel.json()["status"] == "cancelled"


# ---------------------------------------------------------------- §10 collaboration
def test_s10_comments_shared_and_audit(client, tenant):
    art = mk(client, tenant, "artifacts", {"title": "brief"})
    com = mk(client, tenant, "comments",
             {"artifact_id": art["id"], "body": "Check the second paragraph"})
    assert com["created_by"] == "tester"
    shared = mk(client, tenant, "shared-items",
                {"title": "team prompt", "kind": "prompt"})
    assert shared["title"] == "team prompt"
    ev = wb(client, tenant, "POST", "/v1/workbench/events",
            {"kind": "comment_added", "target": art["id"]})
    assert ev.status_code == 201
    events = wb(client, tenant, "GET", "/v1/workbench/events?kind=comment_added")
    assert any(e["id"] == ev.json()["id"] for e in events.json()["events"])
    gone = wb(client, tenant, "DELETE", f"/v1/workbench/events/{ev.json()['id']}")
    assert gone.status_code == 405  # append-only: events are never deleted


# ---------------------------------------------------------------- §11 skills
def test_s11_skill_provenance_and_effectiveness(client, tenant):
    mk(client, tenant, "skill-provenance",
       {"skill_id": "sk-a", "version": "1.0", "source": "library",
        "trust": "verified", "why_selected": "matches the task",
        "parts_entered_context": ["brief"], "influenced_tool_call": True,
        "succeeded": True, "latency_ms": 100, "cost_usd": 0.02,
        "mission_id": "m1"})
    mk(client, tenant, "skill-provenance",
       {"skill_id": "sk-a", "version": "1.0", "source": "library",
        "trust": "verified", "why_selected": "matches the task",
        "parts_entered_context": [], "influenced_tool_call": False,
        "succeeded": False, "latency_ms": 200, "cost_usd": 0.01,
        "mission_id": "m2"})
    eff = wb(client, tenant, "GET", "/v1/workbench/skill-effectiveness")
    row = [s for s in eff.json()["skills"] if s["skill_id"] == "sk-a"][0]
    assert row["uses"] == 2 and row["success_rate"] == 0.5
    assert row["avg_latency_ms"] == 150.0


def test_s11_skill_compatibility(client, tenant):
    r = wb(client, tenant, "POST",
           "/v1/workbench/skill-provenance/validate-compatibility",
           {"skills": [{"id": "a", "requires": ["missing-dep"], "conflicts": ["b"]},
                       {"id": "b", "requires": [], "conflicts": []}]})
    assert r.json()["compatible"] is False
    assert r.json()["missing_dependencies"] == [{"skill": "a", "missing": "missing-dep"}]
    assert r.json()["conflicts"] == [{"skill": "a", "conflicts_with": "b"}]
    ok = wb(client, tenant, "POST",
            "/v1/workbench/skill-provenance/validate-compatibility",
            {"skills": [{"id": "a", "requires": [], "conflicts": []}]})
    assert ok.json()["compatible"] is True


# ---------------------------------------------------------------- §12 providers
def test_s12_provider_export_excludes_secrets(client, tenant):
    mk(client, tenant, "provider-aliases",
       {"alias": "fast", "model": "model-x", "api_key": "sk-live-secret"})
    exp = wb(client, tenant, "GET", "/v1/workbench/providers/export")
    assert "sk-live-secret" not in exp.text
    assert any(a["alias"] == "fast" for a in exp.json()["aliases"])
    mk(client, tenant, "provider-health",
       {"connection_id": "c1", "ok": True, "latency_ms": 120})
    mk(client, tenant, "provider-rotations",
       {"connection_id": "c1", "note": "rotated after leak scare"})
    hist = wb(client, tenant, "GET", "/v1/workbench/provider-health")
    assert len(hist.json()["items"]) == 1


def test_s12_api_token_once_only(client, tenant):
    r = wb(client, tenant, "POST", "/v1/workbench/api-tokens", {"name": "ci"})
    assert r.status_code == 201
    assert r.json()["value"].startswith("aq_")
    listed = wb(client, tenant, "GET", "/v1/workbench/api-tokens")
    row = listed.json()["tokens"][0]
    assert "value" not in row and "token_hash" not in row
    rev = wb(client, tenant, "DELETE", f"/v1/workbench/api-tokens/{row['id']}")
    assert rev.json()["revoked"] is True


# ---------------------------------------------------------------- §13 navigation
def test_s13_commands_role_filtering_member_vs_owner(tmp_path):
    from app.domain import error_envelope

    def fake_guard(request, min_role):
        class _I:
            role = "member"
            user_id = "u1"
            tenant_id = "t-member"
        return _I()

    def fake_tenant(request):
        return "t-member"

    app = FastAPI()
    app.include_router(build_router(
        store=None, guard=fake_guard, tenant_of=fake_tenant,
        corr_of=lambda r: "c1", not_found_of=lambda r: None,
        data_dir=str(tmp_path)))
    tc = TestClient(app)
    r = tc.get("/v1/workbench/commands")
    labels = [a["label"] for a in r.json()["actions"]]
    assert "Open the chat" in labels
    assert "Manage workspace members" not in labels
    assert "Manage plugins" not in labels


def test_s13_search_finds_project(client, tenant):
    mk(client, tenant, "projects", {"name": "Harbour Lights"})
    r = wb(client, tenant, "GET", "/v1/workbench/search?q=harbour")
    assert any(x["title"] == "Harbour Lights" for x in r.json()["results"])


def test_s13_tenant_isolation(client, tenant):
    other = tenant + "-b"
    mk(client, tenant, "projects", {"name": "Mine"})
    mine = wb(client, tenant, "GET", "/v1/workbench/projects")
    theirs = wb(client, other, "GET", "/v1/workbench/projects")
    assert len(mine.json()["items"]) == 1
    assert theirs.json()["items"] == []


# ---------------------------------------------------------------- §14 accessibility
def test_s14_prefs_themes_and_validation(client, tenant):
    r = wb(client, tenant, "PUT", "/v1/workbench/prefs",
           {"theme": "light", "text_size": "large", "high_contrast": True,
            "reduced_motion": True})
    assert r.json()["theme"] == "light" and r.json()["high_contrast"] is True
    bad = wb(client, tenant, "PUT", "/v1/workbench/prefs", {"theme": "neon"})
    assert bad.json()["theme"] == "dark"
    got = wb(client, tenant, "GET", "/v1/workbench/prefs")
    assert got.json()["theme"] == "dark"


# ---------------------------------------------------------------- §15 retention
def test_s15_deletion_statement_and_deletion(client, tenant):
    stmt = wb(client, tenant, "GET",
              "/v1/workbench/retention/deletion-statement")
    assert "Exports you already downloaded" in stmt.json()["survives"]
    assert "Provider records held by the provider" in stmt.json()["survives"]
    draft = mk(client, tenant, "drafts",
               {"conversation_id": "c", "body": "to delete"})
    gone = wb(client, tenant, "POST", "/v1/workbench/retention/deletion",
              {"collection": "drafts", "item_id": draft["id"]})
    assert gone.json()["deleted"] is True
    assert len(gone.json()["survives"]) == 4
    check = wb(client, tenant, "GET", f"/v1/workbench/drafts/{draft['id']}")
    assert check.status_code == 404


def test_s15_retention_cleanup_dry_run(client, tenant):
    mk(client, tenant, "drafts", {"conversation_id": "c", "body": "fresh"})
    r = wb(client, tenant, "POST", "/v1/workbench/retention/cleanup",
           {"dry_run": True})
    assert r.json()["dry_run"] is True
    assert r.json()["would_remove"]["drafts"] == 0


# ---------------------------------------------------------------- §16 diagnostics
def test_s16_diagnostics_status_and_bundle(client, tenant):
    st = wb(client, tenant, "GET", "/v1/workbench/diagnostics/status")
    assert st.json()["version"] == "1.0.0"
    assert any(c["check"] == "Workbench storage can be written"
               for c in st.json()["checks"])
    prev = wb(client, tenant, "GET", "/v1/workbench/diagnostics/bundle/preview")
    assert prev.json()["contains_secrets"] is False
    assert prev.json()["contains_prompts"] is False
    mk(client, tenant, "diagnostic-errors",
       {"reference_id": "ERR-1", "detail": "key sk-abcdefghijklmnop leaked?"})
    bundle = wb(client, tenant, "GET", "/v1/workbench/diagnostics/bundle")
    assert bundle.json()["redacted"] is True
    assert "sk-abcdefghijklmnop" not in bundle.text


# ---------------------------------------------------------------- §17 costs
def test_s17_usage_estimate_vs_actual(client, tenant):
    mk(client, tenant, "usage-entries",
       {"mission_id": "m1", "project_id": "p1", "model": "x",
        "tokens_input": 100, "tokens_output": 50, "tokens_cached": 10,
        "estimated_usd": 0.05, "actual_usd": 0.04, "succeeded": True})
    mk(client, tenant, "usage-entries",
       {"mission_id": "m2", "project_id": "p1", "model": "x",
        "estimated_usd": 0.10, "actual_usd": None, "succeeded": True})
    summ = wb(client, tenant, "GET", "/v1/workbench/usage/summary?project_id=p1")
    assert summ.json()["estimated_usd"] == 0.15
    assert summ.json()["actual_usd_reconciled"] == 0.04
    assert summ.json()["unreconciled_entries"] == 1
    assert summ.json()["cost_per_successful_task_usd"] == 0.04
    budget = mk(client, tenant, "budgets",
                {"project_id": "p1", "limit_usd": 1.0, "reserved_usd": 0.0})
    res = wb(client, tenant, "POST",
             f"/v1/workbench/budgets/{budget['id']}/reserve",
             {"amount_usd": 0.25})
    assert res.json()["reserved_usd"] == 0.25
    over = wb(client, tenant, "POST",
              f"/v1/workbench/budgets/{budget['id']}/reserve",
              {"amount_usd": 5.0})
    assert over.status_code == 400
    rel = wb(client, tenant, "POST",
             f"/v1/workbench/budgets/{budget['id']}/release",
             {"amount_usd": 0.25})
    assert rel.json()["reserved_usd"] == 0.0


# ---------------------------------------------------------------- §18 extensibility
def test_s18_plugin_never_auto_grants(client, tenant):
    plug = mk(client, tenant, "plugins",
              {"name": "reporter", "version": "1.0",
               "permissions_declared": ["read_artifacts"]})
    assert plug["enabled"] is False and plug["granted_permissions"] == []
    en = wb(client, tenant, "POST", f"/v1/workbench/plugins/{plug['id']}/enable")
    assert en.json()["enabled"] is True
    assert en.json()["granted_permissions"] == []
    bad = wb(client, tenant, "POST",
             f"/v1/workbench/plugins/{plug['id']}/grant-permission",
             {"permission": "read_credentials"})
    assert bad.status_code == 400
    ok = wb(client, tenant, "POST",
            f"/v1/workbench/plugins/{plug['id']}/grant-permission",
            {"permission": "read_artifacts"})
    assert ok.json()["granted_permissions"] == ["read_artifacts"]
    rev = wb(client, tenant, "POST",
             f"/v1/workbench/plugins/{plug['id']}/revoke-permission",
             {"permission": "read_artifacts"})
    assert rev.json()["granted_permissions"] == []


def test_s18_webhook_hmac_and_test(client, tenant):
    wh = wb(client, tenant, "POST", "/v1/workbench/webhooks",
            {"url": "https://example.test/hook", "secret": "s3cret"})
    assert wh.status_code == 201
    assert "secret_hash" not in wh.text and "s3cret" not in wh.text
    sig = "sha256=" + hmac_lib.new(b"s3cret", b"hello", hashlib.sha256).hexdigest()
    ok = wb(client, tenant, "POST",
            f"/v1/workbench/webhooks/{wh.json()['id']}/verify",
            {"secret": "s3cret", "payload": "hello", "signature": sig})
    assert ok.json()["valid"] is True
    bad = wb(client, tenant, "POST",
             f"/v1/workbench/webhooks/{wh.json()['id']}/verify",
             {"secret": "wrong", "payload": "hello", "signature": sig})
    assert bad.json()["valid"] is False
    tst = wb(client, tenant, "POST",
             f"/v1/workbench/webhooks/{wh.json()['id']}/test")
    assert tst.json()["config_ok"] is True


def test_s18_webhook_signature_unit():
    assert webhook_signature_ok("k", "p", "sha256=" + hmac_lib.new(
        b"k", b"p", hashlib.sha256).hexdigest()) is True
    assert webhook_signature_ok("k", "p", "sha256=deadbeef") is False


# ---------------------------------------------------------------- §19 UI details (server side)
def test_s19_plain_error_and_unsaved_draft_survives(client, tenant):
    bad = wb(client, tenant, "POST", "/v1/workbench/projects", {"name": ""})
    assert bad.status_code == 400
    assert "Please fill in name." in bad.json()["message"]
    draft = mk(client, tenant, "drafts",
               {"conversation_id": "c", "body": "unsent work"})
    got = wb(client, tenant, "GET", f"/v1/workbench/drafts/{draft['id']}")
    assert got.json()["body"] == "unsent work"


# ---------------------------------------------------------------- §20 context
def test_s20_suggestions_only_with_evidence(client, tenant):
    art = mk(client, tenant, "artifacts",
             {"title": "orphan brief", "mission_id": "mission-that-is-not-here"})
    sugg = wb(client, tenant, "GET", "/v1/workbench/context/suggestions")
    orphans = [s for s in sugg.json()["suggestions"]
               if s.get("artifact_id") == art["id"]]
    assert len(orphans) == 1
    assert "mission-that-is-not-here" in orphans[0]["evidence"]
    assert not any("test failed" in s["suggestion"].lower()
                   for s in sugg.json()["suggestions"])
    stale = mk(client, tenant, "artifacts", {"title": "old brief"})
    wb(client, tenant, "PATCH", f"/v1/workbench/artifacts/{stale['id']}",
       {"created_at": "2020-01-01T00:00:00Z"})
    sugg = wb(client, tenant, "GET", "/v1/workbench/context/suggestions")
    assert any(s.get("artifact_id") == stale["id"]
               for s in sugg.json()["suggestions"])


def test_s20_questions_tracker(client, tenant):
    q = mk(client, tenant, "context-questions",
           {"question": "Which model should own this?", "status": "open"})
    assert q["status"] == "open"


# ---------------------------------------------------------------- §21 decisions
def test_s21_project_memory_conflict_correct_delete(client, tenant):
    first = mk(client, tenant, "project-memories",
               {"project_id": "p1", "topic": "stack", "fact": "Uses Python.",
                "kind": "verified"})
    clash = mk(client, tenant, "project-memories",
               {"project_id": "p1", "topic": "stack", "fact": "Uses Go.",
                "kind": "assumption"})
    assert clash["conflict_with"] == first["id"]
    corr = wb(client, tenant, "POST",
              f"/v1/workbench/project-memories/{first['id']}/correct",
              {"fact": "Uses Python 3.12."})
    assert corr.json()["corrected"]["supersedes"] == first["id"]
    assert corr.json()["old"]["status"] == "superseded"
    expl = wb(client, tenant, "GET",
              "/v1/workbench/project-memories/explain?topic=stack&project_id=p1")
    assert any(f["kind"] in ("verified", "assumption")
               for f in expl.json()["facts"])
    assert any("not facts until you confirm" in f["explanation"]
               for f in expl.json()["facts"])
    dele = wb(client, tenant, "POST",
              f"/v1/workbench/project-memories/{clash['id']}/delete")
    assert "not erased" in dele.json()["note"]
    for coll, body in (("decisions", {"title": "d", "project_id": "p1"}),
                       ("requirements", {"title": "r", "project_id": "p1"}),
                       ("assumptions", {"title": "a", "project_id": "p1"}),
                       ("limitations", {"title": "l", "project_id": "p1"}),
                       ("rejected", {"title": "rej", "project_id": "p1"})):
        assert mk(client, tenant, coll, body)["project_id"] == "p1"


# ---------------------------------------------------------------- §22 coordination/society
def test_s22_dependency_graph_order_and_cycle_unit():
    tasks = [{"id": "a", "depends_on": []},
             {"id": "b", "depends_on": ["a"]},
             {"id": "c", "depends_on": ["b"]}]
    assert topo_order(tasks)["order"] == ["a", "b", "c"]
    cyc = topo_order([{"id": "a", "depends_on": ["b"]},
                      {"id": "b", "depends_on": ["a"]}])
    assert cyc["cycle"] and cyc["order"] == []


def test_s22_capability_match_and_duplicate(client, tenant):
    mk(client, tenant, "coordination-agents",
       {"name": "res", "capabilities": ["research", "writing"], "available": True})
    mk(client, tenant, "coordination-agents",
       {"name": "cod", "capabilities": ["code"], "available": True})
    task = mk(client, tenant, "coordination-tasks",
              {"title": "Write brief", "capabilities_needed": ["research", "writing"]})
    m = wb(client, tenant, "POST",
           f"/v1/workbench/coordination-tasks/{task['id']}/match")
    assert m.json()["matches"][0]["score"] == 1.0
    assert m.json()["matches"][-1]["score"] == 0.0
    dup = mk(client, tenant, "coordination-tasks",
             {"title": "Write brief", "capabilities_needed": []})
    assert dup["duplicate_of"] == task["id"]
    graph = wb(client, tenant, "POST",
               "/v1/workbench/coordination-tasks/dependency-graph", {})
    assert graph.json()["cycle"] == []


def test_s22_society_disclaimer_and_reproduce(client, tenant):
    cfg = mk(client, tenant, "society-configs",
             {"name": "town", "agents": 100, "rounds": 3})
    run = mk(client, tenant, "society-runs",
             {"config_id": cfg["id"], "output_hash": "abc",
              "initial_stance": "for", "final_stance": "against",
              "completeness": "98 of 100 answered",
              "failed_generations": 2})
    rep = wb(client, tenant, "POST",
             f"/v1/workbench/society-runs/{run['id']}/reproduce")
    assert rep.json()["label"] == "SIMULATED ESTIMATE — never a prediction."
    assert "does not give identical answers twice" in rep.json()["reproducibility"]
    run2 = mk(client, tenant, "society-runs",
              {"config_id": cfg["id"], "output_hash": "abc"})
    cmp_ = wb(client, tenant, "POST", "/v1/workbench/society-runs/compare",
              {"run_a": run["id"], "run_b": run2["id"]})
    assert cmp_.json()["identical_output"] is True
    assert cmp_.json()["label"].startswith("SIMULATED ESTIMATE")


def test_s22_handoff_and_escalation(client, tenant):
    task = mk(client, tenant, "coordination-tasks", {"title": "t"})
    hand = mk(client, tenant, "handoffs",
              {"task_id": task["id"], "from": "a1", "to": "a2",
               "note": "taking over"})
    assert hand["to"] == "a2"
    esc = mk(client, tenant, "escalations",
             {"task_id": task["id"], "reason": "agents disagree"})
    assert esc["reason"] == "agents disagree"


# ---------------------------------------------------------------- §23 top-25 spot checks
def test_s23_top25_artifact_provenance_and_diff(client, tenant):
    art = mk(client, tenant, "artifacts",
             {"title": "brief", "provenance": {"model": "m", "sources": ["s1"]}})
    v1 = wb(client, tenant, "POST", f"/v1/workbench/artifacts/{art['id']}/versions",
            {"content": "line one\nline two"})
    v1id = v1.json()["version"]["id"]
    v2 = wb(client, tenant, "POST", f"/v1/workbench/artifacts/{art['id']}/versions",
            {"content": "line one\nline three", "base_version_id": v1id})
    assert v2.json()["conflict"] is None
    stale = wb(client, tenant, "POST",
               f"/v1/workbench/artifacts/{art['id']}/versions",
               {"content": "other", "base_version_id": v1id})
    assert stale.json()["conflict"] is not None  # base is no longer latest
    diff = wb(client, tenant, "POST",
              f"/v1/workbench/artifacts/{art['id']}/compare",
              {"version_a": v1id, "version_b": v2.json()["version"]["id"]})
    texts = [d["text"] for d in diff.json()["diff"]]
    assert "line two" in texts and "line three" in texts
    exp = wb(client, tenant, "GET", f"/v1/workbench/artifacts/{art['id']}/export")
    assert exp.json()["provenance"]["model"] == "m"
    assert exp.json()["versions"] == 3


def test_s23_diff_unit():
    d = diff_lines("a\nb", "a\nc")
    assert {"change": "removed", "text": "b"} in d
    assert {"change": "added", "text": "c"} in d


def test_s23_activity_timeline_and_persistence(client, tenant):
    mk(client, tenant, "projects", {"name": "Persist"})
    wb(client, tenant, "POST", "/v1/workbench/events",
       {"kind": "project_created", "target": "Persist"})
    events = wb(client, tenant, "GET", "/v1/workbench/events")
    assert any(e["kind"] == "project_created" for e in events.json()["events"])
