"""Chat UI wiring for v21.4 (static check — same pattern as
test_ui_v15.py; pins the audit-hardening fixes so a UI refactor
cannot silently bring them back: authenticated export, capped /
debounced / sequenced registry rendering, per-panel fault
isolation, stale-token recovery, honest run status, SSE polling
fallback, model-chip refresh, chat-input preservation, toast
behaviour, double-submit guards and shipped-roster copy)."""
import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_export_uses_authenticated_fetch_not_window_open():
    html = _html()
    # a browser navigation cannot send the local-token header, so the
    # old window.open export landed on a raw 401 page in Shield mode
    assert "window.open(" not in html
    handler = re.search(r"\$\('exportBtn'\)\.onclick = [^;]+;", html)
    # AUDIT 2026-10-22 M2 (assertion retargeted, reason recorded):
    # this pin originally named chatBrief because the button fetched
    # the brief. The button is LABELED "Download proof pack" while
    # delivering the brief — a state lie — so the handler now calls
    # chatProofDownload, which downloads the proof pack. The intent
    # of the original pin is unchanged and still holds:
    # chatProofDownload also downloads via api()/fetch WITH the auth
    # headers (never window.open). The brief keeps its own honestly
    # labeled "Download brief" button in the chat thread (v34 pins
    # the distinction).
    assert handler and "chatProofDownload" in handler.group(0)


def test_registry_rendering_capped_debounced_sequenced():
    html = _html()
    # typing in the filter re-renders only the agent table, debounced
    assert 'oninput="agentFilterChanged()"' in html
    assert 'oninput="refreshLists()"' not in html
    assert "agentFilterChanged" in html and "_agentFilterTimer" in html
    # rendering is capped, with a "showing X of Y" note under the table
    assert "AGENT_RENDER_CAP = 100" in html
    assert "list.slice(0, AGENT_RENDER_CAP)" in html
    assert 'id="agentsNote"' in html and "Showing the first" in html
    # a sequencing token stops stale refreshes clobbering newer state
    assert "_refreshSeq" in html and "seq !== _refreshSeq" in html
    assert "window._agentsAll" in html and "renderAgents" in html


def test_panels_fail_in_isolation():
    html = _html()
    assert "Promise.allSettled" in html
    assert "Could not load missions" in html
    assert "Could not load the agent registry" in html
    # "Could not load usage" removed with the spend displays (owner hard
    # rule: no money figures anywhere) — the Usage money table it
    # reported on no longer exists; the card now holds only the
    # Source library.
    assert "Could not load sources" in html
    # loaders that used to swallow errors into permanently blank panels
    assert "Could not load the model catalog" in html
    assert "Could not load worlds" in html


def test_stale_token_recovery_banner():
    html = _html()
    assert 'id="sessionExpired"' in html
    assert "This tab's session expired" in html
    assert "agentqon setup --repair" in html
    # detected centrally in api(), not per call site
    assert "LOCAL_TOKEN_REQUIRED" in html
    assert "onTokenExpired" in html
    assert "tokenExpired" in html  # toast spam is suppressed once shown


def test_api_error_never_renders_undefined():
    html = _html()
    assert "d.error?d.error.message:(d.detail||r.status)" not in html
    assert "('HTTP ' + r.status)" in html


def test_run_status_is_honest():
    html = _html()
    # UPDATED 2026-10-22: the first three pins used to read the
    # Advanced New-mission form's run handler, which announced the
    # mission's real terminal status in #runStatus ("finalStatus ===
    # 'completed'", "Mission still running", "Mission failed — see
    # the mission card"). The owner removed that form — creation and
    # running live in the composer now — so the honesty contract is
    # pinned where it survives: runChatMission finishes ONLY on the
    # mission's real terminal state, a failed run renders its reason
    # permanently in the thread, and the poll cap admits "still
    # running" instead of implying an outcome.
    assert "const TERMINAL = ['completed', 'failed', 'cancelled', 'paused'];" in html
    assert "This run could not complete" in html
    assert "Still running — follow it under Your missions." in html
    # the comparison card appears only when candidates exist
    assert "(d.candidates && d.candidates.length)" in html


def test_model_chip_refreshed_after_connection_changes():
    html = _html()
    # boot + add + validate/action + rotate + delete + catalog sync
    assert html.count("loadModelState();") >= 5


def test_chat_input_cleared_only_after_create():
    html = _html()
    body = re.search(r"async function sendChat\(\)\{(.*?)\n\}", html, re.S).group(1)
    assert body.index("api('/v1/missions'") < body.index("$('chatInput').value = '';")
    # v21.4.3 supersedes the chatBusy pin: the global composer lock is
    # GONE (owner directive — the harness must always take the next
    # request). Double-firing is now prevented per mission by the
    # liveMissions set inside runChatMission.
    assert "chatBusy" not in html
    assert "liveMissions" in html


def test_watch_falls_back_to_polling_with_indicator():
    html = _html()
    assert 'id="liveStatus"' in html
    assert "startWatchPoll" in html and "stopWatch" in html
    assert "Checking for updates" in html


def test_toast_auto_dismisses_and_is_closable():
    html = _html()
    assert 'id="toastClose"' in html and 'name="toast-dismiss"' in html
    assert 'id="toastMsg"' in html
    assert "_toastTimer" in html


def test_double_submit_guards():
    html = _html()
    add = re.search(r"\$\('connAddBtn'\)\.onclick = async \(\) => \{(.*?)\n\};",
                    html, re.S).group(1)
    assert "btn.disabled = true" in add and "btn.disabled = false" in add
    assert "_worldBusy" in html
    assert "async function addBranch(wid){ if (_worldBusy) return;" in html
    assert "async function runWorld(wid){ if (_worldBusy) return;" in html


def test_copy_matches_shipped_roster():
    html = _html()
    # AUDIT 2026-10-22 L4 (assertion retargeted, reason recorded):
    # this pin originally required the baked-in "6,799" copy. A
    # roster count is live data, not copy — the folded card claimed
    # 6,799 forever, even after a user added a custom agent. The
    # counts are no longer baked into the markup at all; the
    # registry render writes the LIVE total into the fold summary
    # (v34 pins that). The stale-number guards below stay.
    assert "6,799" not in html and "6,516" not in html
    assert " agents on the roster. Open to check a record" in html, \
        "the fold summary line is written from the live roster count"
    assert "6,668" not in html and "6,385" not in html
    # no owner-personal example in the shipped welcome screen
    assert "PawPaw" not in html and "talking-cat" not in html
    assert "Grow my business page" in html
