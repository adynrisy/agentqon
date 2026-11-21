"""Tool Gateway tests (Ch.12), including the textbook's own example test (Ch.21)."""
import tempfile

from app.gateway import ToolGateway
from app.store import Store


def make_gateway(tmp):
    store = Store(f"{tmp}/t.db", f"{tmp}/artifacts")
    return ToolGateway(store), store


def mission(spent=0.0, limit=10.0, allow=None, deny=None, classification="internal"):
    return {"id": "mis_test", "spent": spent, "budget_limit": limit,
            "contract": {"tools": {"allow": allow if allow is not None else ["web.read", "email.send"],
                                   "deny": deny or []},
                         "approval_policy": {"external_side_effects": "required"},
                         "data_classification": classification}}


def test_budget_cap_blocks_next_tool_call(monkeypatch):
    """The Ch.21 example, re-based for v21.3: budgets are not
    user-facing, so the gateway's only spend gate is the internal
    runaway ceiling. Patched down to the textbook's $1.00 here: spent
    0.99 of the ceiling, a 0.05 call would cross it and is blocked."""
    monkeypatch.setattr("app.gateway.MISSION_BUDGET_CEILING_USD", 1.00)
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp)
        d = gw.authorize_and_run("org_t", mission(spent=0.99, limit=1.00), "web.read", {}, estimated_cost=0.05)
        assert d.allowed is False
        assert d.reason == "BUDGET_EXCEEDED"


def test_unknown_tool_rejected_not_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp)
        d = gw.authorize_and_run("org_t", mission(), "teleport", {})
        assert d.allowed is False and d.reason == "UNKNOWN_TOOL"


def test_deny_list_wins_over_allow_list():
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp)
        d = gw.authorize_and_run("org_t", mission(deny=["email.send"]), "email.send", {"to": "x@y.z"})
        assert d.allowed is False and d.reason == "DENIED_BY_POLICY"


def test_high_risk_tool_requires_approval_with_preview():
    with tempfile.TemporaryDirectory() as tmp:
        gw, store = make_gateway(tmp)
        d = gw.authorize_and_run("org_t", mission(), "email.send", {"to": "x@y.z", "subject": "hi"})
        assert d.allowed is False and d.reason == "APPROVAL_REQUIRED" and d.approval_id
        approvals = store.list_approvals("org_t", "mis_test")
        assert len(approvals) == 1 and "email.send" in approvals[0]["preview"]


def test_low_risk_allowed_tool_executes_and_audits():
    with tempfile.TemporaryDirectory() as tmp:
        gw, store = make_gateway(tmp)
        d = gw.authorize_and_run("org_t", mission(), "web.read", {"url": "https://example.org"})
        assert d.allowed is True and d.result["status"] == "ok"
        invocations = store.list_invocations("org_t", "mis_test")
        assert invocations and invocations[0]["status"] == "completed"


def test_confidential_mission_blocks_external_actions():
    with tempfile.TemporaryDirectory() as tmp:
        gw, _ = make_gateway(tmp)
        d = gw.authorize_and_run("org_t", mission(classification="confidential"), "email.send", {})
        assert d.allowed is False and d.reason == "CLASSIFICATION_BLOCKS_EXTERNAL_ACTION"
