"""v28 UI contracts — the dedupe pass + professional error/waiting pass
(2026-10-22, Zohaib: "I can still see some duplicate things on the
screen" + "put some of the best error handling in the harness and
the waiting queues more professional and perfect").

Dedupe — one surface per fact:
- model status lives ONLY in the sidebar pill (the topbar chip is
  gone); the roster total lives ONLY in the registry's table note
  (the header count is gone); view switching lives ONLY in the
  sidebar (the topbar Chat/Advanced switch is gone).
- the first-run connection is the deliberate exception (Zohaib's
  orders later the same day): a first-time user must see the FULL
  model connection on the main screen and never hunt through
  Advanced for it — and not boxed in a card: it is a proper
  landing page that takes over the main screen until a model
  seats. The Advanced card remains the management surface.
- the Advanced Skill-library strip duplicated the sidebar Skills
  entry and is gone; the composer's "Terms" panel held no terms
  (only a repeated note about the picker) and is gone.
- the model picker serves Chat only: hidden on Advanced/Skills.
- on Advanced, the sidebar's recent list + workspace footer stand
  down (Your missions + the workspace box are the surfaces there).

Errors — one plain dialect, never silent, always recoverable:
- friendlyErrorText/friendlyError translate raw fetch failures,
  aborts, 429s and HTTP 5xx into what happened + what to do next;
  toast() routes every message through the translator.
- a failed conversation offers Try again in place; a failed
  mission run offers Run it again in the thread.

Waiting — every wait is a visible queue, not a freeze:
- the chat mission live box carries a queue line (stage + elapsed,
  provider-wait aware) and poll failures surface after 3 strikes
  instead of being swallowed; agent sessions carry the same
  status line; the Advanced job poll reports elapsed per attempt.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def _body(html: str, name: str) -> str:
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


# ------------------------------------------------------------ dedupe
def test_one_surface_per_fact_in_the_chrome():
    html = _html()
    for gone in ('id="modelChip"', 'id="rosterCount"', 'id="viewChatBtn"',
                 'id="viewDashBtn"', 'id="cardSkills"', 'id="skillsOpenBtn"',
                 'id="optBtn"', 'id="optPanel"'):
        assert gone not in html, gone + " — a duplicate surface that was removed"
    assert 'id="sideModel"' in html, "the sidebar pill is the one model-status surface"
    state = _body(html, "loadModelState")
    assert "modelChip" not in state and "sideModel" in state
    assert "updateConnectCard(connected)" in state


def test_view_scoped_stand_downs():
    html = _html()
    # picker-everywhere (Muse restyle, spec §6/§8): the single topbar
    # picker no longer stands down outside Chat — no per-view rule
    # hides it, and the picker itself is present.
    assert 'body[data-view="dash"] .topmodel' not in html, \
        "the picker does not stand down on Advanced"
    assert 'body[data-view="skills"] .topmodel' not in html, \
        "the picker does not stand down on Skills"
    assert 'id="chatModel"' in html, "the single topbar picker exists"
    assert 'body[data-view="dash"] #recentRow' in html, \
        "on Advanced the sidebar record stands down — Your missions is the surface"
    assert 'id="recentLabel"' in html


def test_first_run_card_carries_the_full_connection_form():
    html = _html()
    for fid in ('id="ccBaseUrl"', 'id="ccApiKey"', 'id="ccLabel"',
                'id="ccConnectBtn"', 'id="ccStatus"', 'id="ccDetectBtn"'):
        assert fid in html, fid + " — the first-time connection lives on the main screen"
    key = _body(html, "connectWithApiKey")
    assert "direct_provider" in key, "the main-screen form creates the BYOK connection"
    state = _body(html, "updateConnectCard")
    assert "connected ? 'none' : 'block'" in state, \
        "the landing is on the main screen exactly while no model is connected"
    assert 'class="card" id="connectCard"' not in html, \
        "the connection is a landing page now, not a boxed card"
    assert "landingOn" in state and "$('chatView').style.display = 'none'" in state, \
        "while it shows, the chat view stands down — the landing owns the screen"
    assert 'class="lhero"' in html, "the landing carries a real page headline"


# ------------------------------------------------------------ errors
def test_friendly_error_language_is_central():
    html = _html()
    fe = _body(html, "friendlyErrorText")
    for needle in ("failed to fetch", "rate-limiting", "HTTP 5", "try again"):
        assert needle.lower() in fe.lower(), needle
    toast = _body(html, "toast")
    assert "friendlyErrorText(msg)" in toast, \
        "every toast is translated — no raw 'Failed to fetch' reaches the user"
    conv = _body(html, "sendConversation")
    assert "friendlyError(e)" in conv and 'name="chat-retry"' in conv, \
        "a failed conversation explains itself plainly and offers Try again"
    assert "retryLastChat" in html and "_lastChatText" in html


def test_failures_offer_recovery_in_place():
    run = _body(_html(), "runChatMission")
    assert 'name="mission-run-retry"' in run and "chatRunExisting" in run, \
        "a failed run offers Run it again in the thread"
    assert "friendlyError(runError)" in run


# ------------------------------------------------------------ waiting
def test_mission_live_box_is_a_queue_not_a_freeze():
    run = _body(_html(), "runChatMission")
    assert 'id="qmeta-${mid}"' in run, "the live box carries a queue line"
    assert "fmtElapsed(Date.now() - startedAt)" in run, "the queue line shows elapsed time"
    assert "Waiting on the provider" in run, "rate-limit waits read as waits"
    assert "failStreak >= 3" in run and "hiccup" in run, \
        "poll failures surface after 3 strikes instead of being swallowed silently"


def test_agent_and_dashboard_waits_show_elapsed():
    html = _html()
    start = _body(html, "startAgentSession")
    assert 'id="astatus-${sid}"' in start, "agent sessions carry a status line"
    assert "st.fails++" in start or "fails++" in start, \
        "agent poll failures are counted, not swallowed"
    # UPDATED 2026-10-22: this pin used to read "fmtElapsed(Date.now()
    # - runT0)" — the Advanced New-mission form's external-worker job
    # poll. The owner removed that form (creation lives in the
    # composer now), and with it the form's job poll. The elapsed-
    # while-waiting contract survives in the composer's mission
    # live box (pinned directly in the test above: fmtElapsed(
    # Date.now() - startedAt) in runChatMission).
    assert "fmtElapsed(Date.now() - startedAt)" in html, \
        "the surviving mission wait (composer live box) reports elapsed time"
    reg = _body(html, "loadRegistry")
    assert "Loading the roster" in reg, "the registry shows a loading state, not a silent blank"
