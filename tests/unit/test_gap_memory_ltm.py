"""W4 (gap-memory) — consented cross-session long-term memory.

Covers: consent gating, the five-way reliability system,
provenance + timestamps, confidence validation (never a measured
probability), expiry (per-entry + per-class), contradiction
surfacing, scope enforcement, retrieval (TF-IDF/cosine + rerank,
explanations, normalized scores, diversity, stale-index rebuild),
deletion propagation (row + derived + caches, proved), access
control, secret scrubbing, usefulness, integrity, retention,
size accounting, backup verification, version history.
"""
import pytest

from app import longterm_memory as ltm
from app.longterm_memory import (ConsentRequired, DeletionNotConfirmed)
from app.store import Store

TENANT = "org_ltm_test"


def _store(tmp_path):
    return Store(str(tmp_path / "ltm.db"))


def _grant(store):
    ltm.grant_consent(store, TENANT, "u1")


def _retain(store, fact="User prefers short summaries.", **kw):
    kw.setdefault("scope", "user")
    kw.setdefault("scope_ref", "u1")
    kw.setdefault("topic", "preference summary style")
    return ltm.retain(store, TENANT, "u1", fact=fact, **kw)


def test_consent_gate_blocks_retain_without_approval(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ConsentRequired):
        _retain(s)
    assert ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                      topic="t", fact="Approved once.",
                      approved=True)["status"] == "active"
    _grant(s)
    assert _retain(s)["status"] == "active"
    assert ltm.revoke_consent(s, TENANT, "u1") is True
    with pytest.raises(ConsentRequired):
        ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                   topic="t2", fact="After revoke.")


def test_model_inferred_never_auto_trusted(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    proposed = ltm.propose_fact(
        s, TENANT, "u1", "user", "u1", "preference summary style",
        "User probably likes long summaries.")
    assert proposed["status"] == "proposed"
    assert proposed["reliability"] == "model_inferred"
    # Proposed facts never surface in active retrieval.
    assert ltm.read_for(s, TENANT, "user", "u1") == []
    assert ltm.retrieve(s, TENANT, "summary style", "user", "u1") == []
    approved = ltm.approve_fact(s, TENANT, proposed["memory_id"])
    assert approved["status"] == "active"
    # Approval makes it usable; it does NOT upgrade its authority.
    assert approved["reliability"] == "model_inferred"
    assert "not verified" in approved["reliability_label"]
    block = ltm.render_memory_block(
        ltm.retrieve(s, TENANT, "summary style", "user", "u1"))
    assert "not verified" in block
    assert "verified by a tool result" not in block


def test_dedup_recognizes_same_memory_twice(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    first = _retain(s)
    again = ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                       topic="Preference: Summary-Style!",
                       fact="user prefers SHORT summaries.")
    assert again["memory_id"] == first["memory_id"]


def test_provenance_timestamps_and_confidence_note(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    rec = ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                     topic="tool choice",
                     fact="Team uses pytest for Python tests.",
                     reliability="tool_confirmed",
                     provenance={"origin": "execution_confirmed",
                                 "detail": "mission m1 result"},
                     confidence=0.7)
    assert rec["provenance"]["origin"] == "execution_confirmed"
    assert rec["created_at"] and rec["last_verified_at"]
    assert rec["reliability_label"] == "verified by a tool result"
    block = ltm.render_memory_block([rec])
    assert "confidence 0.7" in block
    assert "not a measured probability" in block
    with pytest.raises(ValueError):
        ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                   topic="bad", fact="Bad confidence.",
                   reliability="explicitly_requested", confidence=1.5)
    with pytest.raises(ValueError):
        ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                   topic="bad", fact="Bad confidence.",
                   reliability="explicitly_requested", confidence=-0.1)


def test_expiry_per_entry_and_per_class(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    past = ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                      topic="old note", fact="This note expired.",
                      reliability="explicitly_requested",
                      expires_at="2020-01-01T00:00:00Z")
    assert ltm.read_for(s, TENANT, "user", "u1") == []
    expired = ltm.process_expiry(s, TENANT)
    assert past["memory_id"] in [m["memory_id"] for m in expired]
    assert s.get_memory(TENANT, past["memory_id"])["status"] == "expired"
    # Per-class TTL: a tool-confirmed fact expires ~365 days out;
    # an explicitly requested fact carries no automatic expiry.
    tool_rec = _retain(s, fact="Tool fact.", topic="tool fact",
                       reliability="tool_confirmed")
    from datetime import datetime
    exp = datetime.fromisoformat(
        tool_rec["expires_at"].replace("Z", "+00:00"))
    created = datetime.fromisoformat(
        tool_rec["created_at"].replace("Z", "+00:00"))
    assert 364 <= (exp - created).days <= 366
    user_rec = _retain(s, fact="User fact.", topic="user fact",
                       reliability="explicitly_requested")
    assert user_rec["expires_at"] is None


def test_contradiction_surfaces_instead_of_overwriting(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    first = _retain(s)
    cands = ltm.find_contradiction_candidates(
        s, TENANT, "user", "u1", "preference summary style",
        "User never prefers short summaries.")
    assert cands and cands[0]["existing"]["memory_id"] == first["memory_id"]
    newer = ltm.contradict(
        s, TENANT, "u1", "user", "u1", "preference summary style",
        "User never prefers short summaries.",
        reliability="explicitly_requested")
    assert first["memory_id"] in newer["contradicts"]
    stored_old = s.get_memory(TENANT, first["memory_id"])
    assert stored_old["status"] == "contested"  # kept, not overwritten
    conflicts = ltm.list_conflicts(s, TENANT)
    assert len(conflicts) == 1
    assert conflicts[0]["challengers"][0]["memory_id"] == newer["memory_id"]


def test_scope_enforcement_mission_never_leaks(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    _retain(s)  # a user-scope record owned by u1
    ltm.retain(s, TENANT, "u1", scope="mission", scope_ref="m1",
               topic="secret plan", fact="Mission one plan.",
               reliability="explicitly_requested")
    assert ltm.read_for(s, TENANT, "mission", "m1") != []
    assert ltm.read_for(s, TENANT, "mission", "m2") == []
    ltm.retain(s, TENANT, "u1", scope="project", scope_ref="p1",
               topic="p", fact="Project one fact.",
               reliability="explicitly_requested")
    assert ltm.read_for(s, TENANT, "project", "p2") == []
    assert ltm.read_for(s, TENANT, "project", "p1") != []
    # A member cannot read another user's user-scope records.
    assert ltm.read_for(s, TENANT, "user", "u1",
                        role="member", user_id="u2") == []
    assert ltm.read_for(s, TENANT, "user", "u1",
                        role="member", user_id="u1") != []


def test_retrieval_ranks_explains_and_normalizes(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
               topic="testing", fact="Team uses pytest for Python tests.",
               reliability="tool_confirmed")
    ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
               topic="cooking", fact="Sourdough starter needs feeding.",
               reliability="explicitly_requested")
    results = ltm.retrieve(s, TENANT, "pytest python tests",
                           "user", "u1")
    assert results and results[0]["topic"] == "testing"
    assert results[0]["retrieval_score"] == 1.0  # normalized top
    assert "pytest" in results[0]["matched_terms"]
    assert "Matched the words" in results[0]["explanation"]
    assert "verified by a tool result" in results[0]["explanation"]


def test_retrieval_diversity_caps_one_topic(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    for i in range(3):
        ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                   topic="testing", fact=f"Testing note {i} pytest.",
                   reliability="explicitly_requested")
    ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
               topic="debugging", fact="Debugging note pytest.",
               reliability="explicitly_requested")
    results = ltm.retrieve(s, TENANT, "pytest", "user", "u1",
                           limit=10, per_topic_cap=2)
    topics = [r["topic"] for r in results]
    assert topics.count("testing") <= 2


def test_deletion_propagates_to_derived_and_caches(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    orig = _retain(s)
    derived = ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                         topic="summary of note",
                         fact="Summary of the preference.",
                         reliability="true_at_a_time",
                         derived_from=[orig["memory_id"]])
    cached_answer = ltm.retain(
        s, TENANT, "u1", scope="user", scope_ref="u1",
        topic="cached answer", fact="Cached answer text.",
        reliability="true_at_a_time")
    cached_answer["source_memory_ids"] = [orig["memory_id"]]
    s.put_memory(TENANT, cached_answer)
    assert ltm.retrieve(s, TENANT, "short summaries", "user", "u1")
    assert ltm._INDEX_CACHE.get(TENANT) is not None
    with pytest.raises(DeletionNotConfirmed):
        ltm.delete_fact(s, TENANT, orig["memory_id"])
    assert s.get_memory(TENANT, orig["memory_id"]) is not None
    report = ltm.delete_fact(s, TENANT, orig["memory_id"], confirm=True)
    assert report["deleted"] is True and report["cache_cleared"] is True
    assert s.get_memory(TENANT, orig["memory_id"]) is None
    assert s.get_memory(TENANT, derived["memory_id"])["status"] \
        == "invalidated"
    assert s.get_memory(TENANT, cached_answer["memory_id"])["status"] \
        == "invalidated"
    assert set(report["invalidated"]) == {derived["memory_id"],
                                          cached_answer["memory_id"]}
    assert ltm._INDEX_CACHE.get(TENANT) is None
    assert ltm.retrieve(s, TENANT, "short summaries",
                        "user", "u1") == []


def test_access_control_and_secret_scrubbing(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    rec = ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                     topic="key note",
                     fact="The api_key: sk-live-abcdefgh is here.",
                     reliability="explicitly_requested")
    assert "sk-live" not in rec["fact"] and "[redacted]" in rec["fact"]
    assert ltm.can_write("member", "u1", "project", "p1") is False
    assert ltm.can_write("owner", "u1", "project", "p1") is True
    assert ltm.can_write("member", "u1", "user", "u1") is True
    assert ltm.can_read("viewer", "u1", {"scope": "user",
                                         "scope_ref": "u1"}) is False
    assert ltm.can_read("viewer", "u1", {"scope": "organization",
                                         "scope_ref": ""}) is True


def test_access_telemetry_logs_no_private_content(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    ltm.clear_access_log()
    rec = _retain(s, fact="A very private preference text.")
    ltm.read_for(s, TENANT, "user", "u1")
    events = [e for e in ltm.access_log()
              if e["memory_id"] == rec["memory_id"]]
    assert events
    assert "A very private preference text" not in str(events)
    assert all("fact" not in e for e in events)


def test_usefulness_integrity_retention_size_backup(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    helpful = _retain(s, fact="Helpful fact.", topic="helpful",
                      reliability="explicitly_requested")
    ltm.record_use(s, TENANT, helpful["memory_id"], helped=True)
    ltm.record_use(s, TENANT, helpful["memory_id"], helped=False)
    stored = s.get_memory(TENANT, helpful["memory_id"])
    assert stored["use_count"] == 2 and stored["helpful_count"] == 1
    # An old, never-used record is reported for pruning.
    old = _retain(s, fact="Old fact.", topic="old",
                  reliability="true_at_a_time")
    old["created_at"] = "2020-01-01T00:00:00Z"
    s.put_memory(TENANT, old)
    report = ltm.usefulness_report(s, TENANT)
    assert old["memory_id"] in [m["memory_id"]
                                for m in report["never_used"]]
    # Integrity: untampered rows pass; a tampered row is flagged.
    assert ltm.verify_integrity(s, TENANT)["flagged"] == []
    tampered = dict(helpful)
    tampered["fact"] = "Changed behind the store's back."
    s.put_memory(TENANT, tampered)
    assert helpful["memory_id"] in \
        ltm.verify_integrity(s, TENANT)["flagged"]
    # Retention: the old unused record is pruned; the used one stays.
    pruned = ltm.enforce_retention(s, TENANT)
    assert old["memory_id"] in [m["memory_id"] for m in pruned]
    assert s.get_memory(TENANT, helpful["memory_id"])["status"] == "active"
    # Size accounting counts active fact + topic characters.
    sizes = ltm.size_accounting(s, TENANT)
    assert sizes["total_chars"] > 0
    # Backup verification over a supplied export.
    live = [m for m in s.list_memories(TENANT)
            if m.get("kind") in (None, "fact")
            and m["status"] in ("active", "retention_pruned")]
    export = [{"memory_id": m["memory_id"],
               "fact_hash": m.get("fact_hash")} for m in live]
    assert ltm.verify_backup(s, TENANT, export)["ok"] is True
    assert ltm.verify_backup(s, TENANT, export[:-1])[
        "missing_from_backup"]


def test_version_history_and_stale_index_rebuild(tmp_path):
    s = _store(tmp_path)
    _grant(s)
    first = _retain(s)
    second = ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
                        topic="preference summary style",
                        fact="User prefers detailed summaries.",
                        reliability="explicitly_requested")
    assert second["version"] == first["version"] + 1
    assert second["supersedes"] == first["memory_id"]
    history = ltm.version_history(s, TENANT, second["memory_id"])
    assert [h["version"] for h in history] == [2, 1]
    # Retrieval after a new write serves the new record: the
    # fingerprint changed, so no stale index is served.
    before = ltm.retrieve(s, TENANT, "detailed summaries", "user", "u1")
    assert before and before[0]["fact"].startswith(
        "User prefers detailed")
    ltm.retain(s, TENANT, "u1", scope="user", scope_ref="u1",
               topic="preference summary style",
               fact="User prefers detailed summaries with examples.",
               reliability="explicitly_requested")
    after = ltm.retrieve(s, TENANT, "detailed summaries", "user", "u1")
    assert after and "examples" in after[0]["fact"]
