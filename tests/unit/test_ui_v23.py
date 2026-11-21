"""v21.4.2 UI contracts (static checks, same pattern as test_ui_v22.py):

A — the model chip reads the tenant-scoped /v1/model-state, not /health
    (whose model block covers platform sources only and never saw an
    active BYOK connection).
B — the agent roster is cached on the device (IndexedDB + memory
    fallback): a cheap /v1/agents/version probe decides cache vs one
    full download; filtering is client-side over the cache;
    refreshLists() fetches no roster data at all; pollers pause while
    the tab is hidden.
C — a failed chat run leaves a persistent in-thread message with the
    outcome, the reason and the mission id (not just a 6s toast).
E — the chat composer picks a MODEL (#chatModel, Auto default) instead
    of a privacy class (#chatClass is gone); a pick pins the mission
    contract exactly like the dashboard form; chat classification is
    the fixed 'internal'.
"""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(html: str, name: str) -> str:
    """Extract one function's body by brace matching, skipping string
    and template literals and comments so braces inside them do not
    throw the count off."""
    m = re.search(r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(", html)
    assert m, f"function {name} not found"
    i = html.index("{", m.end() - 1)
    depth, j = 0, i
    while j < len(html):
        ch = html[j]
        if ch in "'\"`":
            j += 1
            while j < len(html) and html[j] != ch:
                j += 2 if html[j] == "\\" else 1
        elif ch == "/" and html[j + 1:j + 2] == "/":
            j = html.index("\n", j)
        elif ch == "/" and html[j + 1:j + 2] == "*":
            j = html.index("*/", j) + 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces in {name}")


# ------------------------------------------------------------------ A
def test_model_chip_uses_tenant_model_state_not_health():
    body = _body(_html(), "loadModelState")
    assert "api('/v1/model-state')" in body
    assert "api('/health')" not in body and "fetch('/health')" not in body, \
        "the chip must not READ /health — it never counts BYOK (a comment may say why)"
    assert "Model status unknown" in body, "the error fallback text is preserved"


# ------------------------------------------------------------------ B
def test_roster_cache_flow_exists():
    html = _html()
    assert "indexedDB" in html, "the roster cache lives in IndexedDB"
    assert "rosterCacheGet" in html and "rosterCachePut" in html
    assert "rosterCacheDrop" in html
    assert "_rosterMemCache" in html, "in-memory fallback when IndexedDB is unavailable"
    assert "/v1/agents/version" in html, "freshness comes from the cheap version probe"


def test_load_registry_probes_version_before_any_full_fetch():
    body = _body(_html(), "loadRegistry")
    probe = body.index("/v1/agents/version")
    full = body.index("api('/v1/agents')")
    assert probe < full, "the version probe must precede the one full roster download"
    assert "rosterCacheGet" in body and "rosterCachePut" in body
    assert "Could not load the agent registry" in body


def test_filtering_is_client_side_over_the_cache():
    html = _html()
    render = _body(html, "renderAgents")
    assert "window._agentsAll" in render, "rendering reads the cached roster"
    assert "api(" not in render, "filtering/rendering makes NO network calls"
    filt = _body(html, "agentFilterChanged")
    assert "api(" not in filt, "no per-keystroke network — the filter is purely local"


def test_refresh_lists_fetches_no_roster_data():
    body = _body(_html(), "refreshLists")
    assert "/v1/agents" not in body, "refreshLists must not fetch agents or agents/stats"
    assert "loadRegistry" not in body
    assert "loadSuitability" not in body, "suitability keeps its own small dashboard call"
    assert "api('/v1/missions')" in body, "the slim refresh still loads missions"


def test_registry_loads_via_refresh_all_and_mutations_sync_the_cache():
    html = _html()
    allb = _body(html, "refreshAll")
    assert "loadRegistry()" in allb and "loadSuitability()" in allb
    life = _body(html, "setLife")
    assert "rosterCacheSyncLocal" in life and "refreshLists()" not in life
    save = _body(html, "saveCustomAgent")
    assert "rosterCacheDrop" in save and "loadRegistry()" in save
    assert "refreshLists()" not in save
    dele = _body(html, "deleteCustomAgent")
    assert "rosterCacheDrop" in dele and "loadRegistry()" in dele


def test_pollers_pause_while_tab_hidden():
    html = _html()
    assert html.count("document.hidden") >= 2, \
        "both the mission-events poller and the watch poller pause when hidden"


def test_chat_paths_trigger_no_roster_work():
    html = _html()
    for fn in ("sendChat", "runChatMission", "openChatMission", "renderRecent"):
        body = _body(html, fn)
        assert "refreshLists" not in body and "loadRegistry" not in body, fn
        assert "/v1/agents" not in body, fn


# ------------------------------------------------------------------ C
def test_failed_run_leaves_a_persistent_thread_message():
    body = _body(_html(), "runChatMission")
    assert "runError" in body, "the /run error is captured, not toast-only"
    assert "could not complete" in body, "a persistent in-thread outcome message is appended"
    assert "It is saved in your missions." in body, "the persistent message says where the run is saved, in plain words (no id on the face — P-15)"


def test_render_thread_shows_failure_reason_and_cancelled_state():
    body = _body(_html(), "renderThread")
    assert "MissionFailed" in body, "the reason is read from the MissionFailed event"
    for key in ("fp.message", "fp.error", "fp.reason"):
        assert key in body, f"payload reason fallback {key}"
    # Polish extension (a): the failed/cancelled thread lines drop the
    # mission id from the face (P-15 precedent — the mission is found by
    # title in Missions), so the old "esc(m.id)" assertion is replaced.
    assert "This mission failed" in body and "Open it from Missions to see the full event log." in body
    assert "Mission ${esc(m.id)}" not in body, "no mission id on the failed/cancelled face lines"
    assert "cancelled" in body, "cancelled gets its own explicit branch"


# ------------------------------------------------------------------ E
def test_chat_composer_has_model_select_not_privacy_select():
    html = _html()
    assert 'id="chatModel"' in html
    assert "Auto — AgentQon picks the best available model" in html
    assert 'id="chatClass"' not in html, "the privacy selector is gone from the composer"
    assert "populateChatModels" in html
    models = _body(html, "loadModels")
    assert "populateChatModels" in models, "the picker refreshes wherever the catalog reloads"


def test_send_chat_pins_only_when_a_model_is_picked():
    body = _body(_html(), "sendChat")
    assert "data_classification: 'internal'" in body, "chat classification is the fixed default"
    assert "pickedModel" in body
    pin = body.index("pinned_model_id")
    cond = body.index("if (pickedModel)")
    assert cond < pin, "pinned_model_id is sent only under the picked-model condition"
    assert "routing_mode = 'pinned_model'" in body or 'routing_mode = "pinned_model"' in body \
        or "routing_mode: 'pinned_model'" in body or 'routing_mode="pinned_model"' in body \
        or "routing_mode = 'pinned_model'" in body
