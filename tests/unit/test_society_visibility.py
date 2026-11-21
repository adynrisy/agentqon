"""Society visibility (2026-10-24, Zohaib's "fix the simulations
thing"): the diagnostic proved the engine completes runs — the
defects were visibility and honesty. These tests pin the fixes:

  * LIVE PROGRESS — a running record shows movement WITHIN a round:
    progress + the round's first answers are persisted per call,
    truthfully from stored state, before the round commits.
  * HONEST PACE — the sizer's estimate rides measured pace (a past
    run's actual per-call pace first, the self-test timing second)
    and is flagged rough when no measurement exists, so no surface
    may print a precise figure the harness cannot back.
  * PARTIAL-ROUND DURABILITY — if the server dies mid-round, the
    calls that landed stay on the record and the sweeper's note
    says how far the run got.

Same bench discipline as test_society.py: scripted fakes through the
client_factory seam, bookkeeping asserted exactly.
"""
import asyncio
import os
import tempfile
import threading
import time

import pytest

import app.society as society
from app.society import SocietyService, size_society, summarize
from app.store import Store
from tests.unit.test_society import (ScriptedGateway, TENANT,
                                     _FakeOrchestrator, _seed_roster,
                                     _service, _wait_done)

TASK = "Quick sanity check on a niche b2b pricing change"  # 25 tier


def _store():
    return Store(os.path.join(tempfile.mkdtemp(), "soc.db"))


class SlowGateway(ScriptedGateway):
    """The scripted room, but every call takes real time — so a
    poller can watch the record move while round 1 is still open."""

    def __init__(self, delay=0.25, **kw):
        super().__init__(**kw)
        self.delay = delay

    async def chat_json(self, model, system, user, temperature=0.4):
        await asyncio.sleep(self.delay)
        return await super().chat_json(model, system, user,
                                       temperature=temperature)


class BlockingGateway(ScriptedGateway):
    """Lead calls land instantly; table calls hang until released —
    a run frozen mid-round, exactly where the old record went dark
    and where a server death used to erase the round."""

    def __init__(self, release: threading.Event, **kw):
        super().__init__(**kw)
        self.release = release

    async def chat_json(self, model, system, user, temperature=0.4):
        if "reactions" in system:  # a table call
            while not self.release.is_set():
                time.sleep(0.02)
        return await super().chat_json(model, system, user,
                                       temperature=temperature)


def _poll_until(store, society_id, pred, timeout=15):
    end = time.time() + timeout
    record = None
    while time.time() < end:
        record = store.get_society(TENANT, society_id)
        if record is not None and pred(record):
            return record
        time.sleep(0.02)
    raise AssertionError(f"condition never met; last record state: "
                         f"{record and record.get('state')}, "
                         f"progress: {record and record.get('progress')}")


# ------------------------------------------------------- live progress
def test_progress_moves_within_round_one_before_it_commits():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, SlowGateway(delay=0.25))
    record = svc.create_society(TENANT, "tester", task_text=TASK)
    assert record["sizer"]["size"] == 25
    svc.start_run(TENANT, record["society_id"])

    # The old record stayed at zero until the whole round committed
    # (~5 min on a slow local model). Now a mid-round snapshot must
    # exist: round 1 open, some calls landed, their answers already
    # on the record — while the round has NOT committed (the digest
    # is only rebuilt when a round closes).
    mid = _poll_until(
        store, record["society_id"],
        lambda r: r["state"] == "running"
        and 1 <= (r.get("progress") or {}).get("calls_done", 0) <= 5
        and r.get("digest") == "")
    progress = mid["progress"]
    assert progress["round"] == 1 and progress["rounds_cap"] == 5
    assert progress["calls_expected"] == 6  # 3 leads + 3 tables
    assert 1 <= progress["calls_done"] < 6
    assert progress["responded"] == len(mid["events"]) >= 1
    assert all(e["round"] == 1 for e in mid["events"])
    assert mid["calls_made"] == progress["calls_done"]
    assert progress["updated_at"] >= mid["run_started_at"]

    done = _wait_done(store, TENANT, record["society_id"])
    assert done["state"] == "done", done.get("error")
    assert done["calls_made"] == done["sizer"]["est_calls"] == 30
    # The run timed its own calls — the pace record that makes the
    # NEXT society's estimate measured instead of guessed.
    assert done["pace"]["calls"] == 30 and done["pace"]["seconds"] > 0
    assert done["run_started_at"] and done["run_ended_at"] \
        >= done["run_started_at"]
    # Terminal progress still describes the final round truthfully.
    assert done["progress"]["round"] == 5
    assert done["progress"]["calls_done"] == 6


def test_progress_rides_the_view_and_the_list():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, SlowGateway(delay=0.3))
    record = svc.create_society(TENANT, "tester", task_text=TASK)
    svc.start_run(TENANT, record["society_id"])
    live = _poll_until(
        store, record["society_id"],
        lambda r: (r.get("progress") or {}).get("calls_done", 0) >= 1)
    view = svc.view(live)
    assert view["progress"]["calls_done"] >= 1
    assert view["summary"]["progress"]["round"] == 1
    rows = svc.list_views(TENANT)
    row = next(r for r in rows
               if r["society_id"] == record["society_id"])
    assert row["progress"]["calls_done"] >= 1
    _wait_done(store, TENANT, record["society_id"])


# --------------------------------------------------------- honest pace
def test_sizer_pace_precedence_and_rough_flag():
    plain = size_society(objective="Compare launch strategies")
    assert plain["pace_source"] == "guess" and plain["est_rough"] is True

    probed = size_society(objective="Compare launch strategies",
                          latency_ms=4000)
    assert probed["pace_source"] == "self_test"
    assert probed["est_rough"] is False
    assert probed["est_seconds"] == round(probed["est_calls"] * 4 / 4)

    measured = size_society(objective="Compare launch strategies",
                            history_call_seconds=30)
    assert measured["pace_source"] == "history"
    assert measured["est_rough"] is False
    assert measured["size"] == 100  # 105 calls x 30s / 4 fits the fence
    assert measured["est_seconds"] == round(measured["est_calls"] * 30 / 4)

    both = size_society(objective="Compare launch strategies",
                        latency_ms=1000, history_call_seconds=30)
    assert both["pace_source"] == "history", \
        "a real run's pace outranks a one-token probe"

    slow = size_society(objective="Compare launch strategies",
                        history_call_seconds=120)
    assert slow["size"] == 25, "measured slowness steps the room down"
    assert "slowly" in slow["reason"] and "120s a call" in slow["reason"]


def test_history_pace_flows_from_a_finished_run():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, gw, _ = _service(store, SlowGateway(delay=0.05))
    first = svc.create_society(TENANT, "tester", task_text=TASK)
    assert first["sizer"]["pace_source"] == "guess"
    assert first["sizer"]["est_seconds"] == 45  # 30 calls at the default
    svc.start_run(TENANT, first["society_id"])
    done = _wait_done(store, TENANT, first["society_id"])
    assert done["state"] == "done" and done["pace"]["calls"] == 30

    second = svc.create_society(TENANT, "tester", task_text=TASK)
    assert second["sizer"]["pace_source"] == "history"
    assert second["sizer"]["est_rough"] is False
    # Measured pace (~0.05s/call, clamped to the 1s floor) — nowhere
    # near the 45s the unmeasured default would have printed.
    assert second["sizer"]["est_seconds"] <= 10


def test_confirmation_message_drops_fake_precision_when_rough():
    store = _store()
    _seed_roster(store, TENANT, count=200)
    svc, gw, _ = _service(store)
    big = svc.create_society(
        TENANT, "tester",
        task_text="Will this announcement go viral and spread to "
                  "the general public everywhere")
    assert big["sizer"]["requires_confirmation"] is True
    assert big["sizer"]["est_rough"] is True
    with pytest.raises(society.ConfirmationRequired) as exc:
        svc.start_run(TENANT, big["society_id"])
    message = str(exc.value)
    assert "model calls" in message and "time is unknown" in message
    assert f"~{big['sizer']['est_seconds']}s" not in message, \
        "no precise figure may ride an unmeasured estimate"

    # Give the tenant a measured past on the seated model and the
    # same fence speaks in seconds again — backed ones.
    past = dict(big)
    past.update({"society_id": "soc_past000001", "state": "done",
                 "pace": {"calls": 30, "seconds": 90.0},
                 "run_started_at": 1000.0, "run_ended_at": 1090.0})
    store.put_society(TENANT, past)
    big2 = svc.create_society(
        TENANT, "tester",
        task_text="Will this announcement go viral and spread to "
                  "the general public everywhere")
    assert big2["sizer"]["pace_source"] == "history"
    with pytest.raises(society.ConfirmationRequired) as exc2:
        svc.start_run(TENANT, big2["society_id"])
    assert "on the seated model" in str(exc2.value)
    assert "time is unknown" not in str(exc2.value)


# --------------------------------------------- partial-round durability
def test_partial_round_survives_and_the_sweeper_says_how_far():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    release = threading.Event()
    svc, gw, _ = _service(store, BlockingGateway(release))
    record = svc.create_society(TENANT, "tester", task_text=TASK)
    svc.start_run(TENANT, record["society_id"])

    # Round 1 frozen mid-flight: the 3 lead calls have landed, the
    # 3 table calls have not. The old record showed NOTHING here.
    frozen = _poll_until(
        store, record["society_id"],
        lambda r: (r.get("progress") or {}).get("calls_done", 0) == 3)
    assert frozen["state"] == "running"
    assert frozen["progress"]["calls_expected"] == 6
    assert len(frozen["events"]) == 3
    assert all(e["round"] == 1 for e in frozen["events"])
    assert summarize(frozen)["responded"] == 3

    # The server dies here. A fresh process (a new service over the
    # same store, holding no live handles) reads the record: the
    # sweeper marks it interrupted, the note says how far it got,
    # and the partial round's answers are still on record.
    svc2, _, _ = _service(store)
    swept = svc2.get_society(TENANT, record["society_id"])
    assert swept["state"] == "interrupted"
    assert "interrupted" in swept["error"]
    assert "3 of 6 calls" in swept["error"]
    assert "round 1" in swept["error"]
    assert len(swept["events"]) == 3
    world = svc2.view(swept)["world_output"]
    assert world is not None and world["responded_count"] == 3
    assert world["label"] == society.LABEL

    release.set()  # let the orphaned engine drain so nothing leaks
    _poll_until(store, record["society_id"],
                lambda r: r["state"] in ("done", "capped", "failed",
                                         "cancelled"))


def test_sweeper_message_variants_stay_plain():
    store = _store()
    _seed_roster(store, TENANT, count=40)
    svc, _, _ = _service(store)

    fresh = svc.create_society(TENANT, "tester", task_text="anything")
    fresh["state"] = "running"  # died before a single call landed
    store.put_society(TENANT, fresh)
    swept = svc.get_society(TENANT, fresh["society_id"])
    assert swept["state"] == "interrupted"
    assert "no rounds had completed" in swept["error"]

    other = svc.create_society(TENANT, "tester", task_text="anything")
    other["state"] = "running"  # died between rounds: a full round
    other["events"] = [{"round": 1, "agent_id": "a", "kind": "lead",
                        "table_id": None, "stance": "support",
                        "intensity": 0.5, "action": "share",
                        "line": "on record", "target_agent_id": None}]
    other["progress"] = {"round": 2, "rounds_cap": 5, "calls_done": 0,
                         "calls_expected": 6, "responded": 0,
                         "updated_at": time.time()}
    store.put_society(TENANT, other)
    swept2 = svc.get_society(TENANT, other["society_id"])
    assert "the rounds that completed are still on record" \
        in swept2["error"]
