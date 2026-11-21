"""v21.4.2 workstream I: INTERNAL_ERROR detail follows deployment mode.

The v21.4.0 hygiene pass sanitized every internal-error envelope —
right for a hosted multi-user deployment, wrong for the packaged
single-owner install, where the caller is the operator and the
sanitized text left them no way to see why anything failed.

Rule pinned here:
- local/dev auth modes → the envelope carries `detail` (the trimmed
  exception message);
- accounts mode + member/viewer caller → sanitized only, no detail;
- accounts mode + owner/admin caller → detail included.
"""
import dataclasses
import json
import types
import uuid

import app.main
from tests.conftest import MISSION_BODY, hdr


def _raise_boom(*args, **kwargs):
    raise RuntimeError("boom-marker-123")


def test_local_mode_run_failure_envelope_includes_detail(client, tenant, monkeypatch):
    """End-to-end: a mission run that blows up in dev (local) mode
    surfaces the real reason in the envelope's detail field."""
    monkeypatch.setattr("app.worker.run_job_inline_async", _raise_boom)
    r = client.post("/v1/missions", json=MISSION_BODY,
                    headers=hdr(tenant, f"key-{uuid.uuid4().hex[:10]}"))
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    run = client.post(f"/v1/missions/{mid}/run", headers=hdr(tenant))
    assert run.status_code == 500, run.text
    err = run.json()["error"]
    assert err["code"] == "INTERNAL_ERROR"
    assert "An internal error occurred" in err["message"], "the stable message stays"
    assert err["correlation_id"], "the correlation id stays"
    assert "boom-marker-123" in err.get("detail", ""), \
        "a local install's operator must see the actual reason"


class _FakeRequest:
    """internal_error only needs request.state for the correlation id."""
    def __init__(self):
        self.state = types.SimpleNamespace()


def _envelope(resp):
    return json.loads(resp.body)["error"]


def test_accounts_mode_member_gets_no_detail(monkeypatch):
    monkeypatch.setattr(app.main, "settings",
                        dataclasses.replace(app.main.settings, auth_mode="accounts"))
    monkeypatch.setattr(app.main, "identity",
                        lambda req: types.SimpleNamespace(role="member"))
    err = _envelope(app.main.internal_error(_FakeRequest(), RuntimeError("secret-cause"), "x"))
    assert err["code"] == "INTERNAL_ERROR"
    assert "detail" not in err, "hosted members keep the sanitized envelope exactly"
    assert "secret-cause" not in json.dumps(err)


def test_accounts_mode_admin_and_owner_get_detail(monkeypatch):
    monkeypatch.setattr(app.main, "settings",
                        dataclasses.replace(app.main.settings, auth_mode="accounts"))
    for role in ("admin", "owner"):
        monkeypatch.setattr(app.main, "identity",
                            lambda req, r=role: types.SimpleNamespace(role=r))
        err = _envelope(app.main.internal_error(_FakeRequest(), RuntimeError("secret-cause"), "x"))
        assert err.get("detail") == "secret-cause", f"accounts-mode {role} is an operator"


def test_local_mode_detail_does_not_depend_on_identity(monkeypatch):
    """In non-accounts modes the detail is unconditional — identity
    is never consulted (a local identity failure must not hide the
    reason from the machine's owner)."""
    monkeypatch.setattr(app.main, "settings",
                        dataclasses.replace(app.main.settings, auth_mode="dev"))

    def _boom_identity(req):
        raise AssertionError("identity must not be consulted outside accounts mode")

    monkeypatch.setattr(app.main, "identity", _boom_identity)
    err = _envelope(app.main.internal_error(_FakeRequest(), RuntimeError("local-cause"), "x"))
    assert err.get("detail") == "local-cause"
