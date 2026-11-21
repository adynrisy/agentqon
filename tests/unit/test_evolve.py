"""v22 (R2): self-evolution — structured outcome lessons in the
memory layer, failure classification, ledger influence on model
picks (a repeatedly failing model is deprioritized), and the evolve
report (patterns, applied vs proposed tunings, honesty statement).
"""
from app import evolve
from app.config import Settings
from app.store import Store

TENANT = "org_evolve_test"


def _store(tmp_path):
    return Store(str(tmp_path / "e.db"))


def _lesson(store, **over):
    base = dict(subject_type="model", subject="model-a",
                outcome="failed", category="provider_error",
                cause="gateway returned HTTP 500", evidence_id="mis_1",
                source_kind="mission", source_id="mis_1")
    base.update(over)
    return evolve.record_lesson(store, TENANT, **base)


def test_lesson_written_and_read_back_from_the_memory_layer(tmp_path):
    store = _store(tmp_path)
    record = _lesson(store, category="provider_rate_limit",
                     cause="gateway returned HTTP 429 (after 8 attempts)")
    assert record["kind"] == "lesson"
    assert record["category"] == "provider_rate_limit"
    # It lives in the memory layer itself (same table as facts).
    raw = store.list_memories(TENANT)
    assert any(m["memory_id"] == record["memory_id"] for m in raw)
    lessons = evolve.list_lessons(store, TENANT)
    assert len(lessons) == 1
    assert lessons[0]["subject"] == "model-a"
    assert lessons[0]["evidence_id"] == "mis_1"
    # Lessons accumulate (they are events, not curated beliefs) —
    # a second identical outcome is a second record.
    _lesson(store, category="provider_rate_limit")
    assert len(evolve.list_lessons(store, TENANT)) == 2


def test_redaction_applies_to_lesson_causes(tmp_path):
    store = _store(tmp_path)
    _lesson(store, cause="failed with key sk-abcdefgh12345678 in play")
    lesson = evolve.list_lessons(store, TENANT)[0]
    assert "sk-abcdefgh" not in lesson["cause"]


def test_failure_classification():
    c = evolve.classify_failure_text
    assert c("gateway returned HTTP 429 (after 8 attempts)") == \
        "provider_rate_limit"
    assert c("The user denied this action") == "approval_denied"
    assert c("gateway timed out: ReadTimeout") == "timeout"
    assert c("model did not return valid JSON") == "output_format"
    assert c("LLM general output contained no deliverable") == \
        "output_format"
    assert c("No model is connected, so nothing can be generated") == \
        "provider_error"
    assert c("step cap reached (25 steps) without completion") == \
        "planning"
    assert c("something entirely unexpected") == "other"
    assert evolve.classify_gateway_error(
        type("E", (), {"category": "rate_limited"})()) == \
        "provider_rate_limit"
    assert evolve.classify_gateway_error(
        type("E", (), {"category": "timeout"})()) == "timeout"


def _entries(*model_ids):
    return [({"model_id": m, "capabilities": {"chat": "advertised"},
              "connection_id": f"conn_{m}"},
             {"id": f"conn_{m}", "kind": "direct_provider"})
            for m in model_ids]


def test_pick_without_lessons_matches_the_default_order(tmp_path):
    store = _store(tmp_path)
    settings = Settings(llm_model="model-a")
    entry, _ = evolve.pick_with_lessons(settings, store, TENANT,
                                        _entries("model-a", "model-b"))
    assert entry["model_id"] == "model-a"


def test_repeatedly_failing_model_is_deprioritized(tmp_path):
    store = _store(tmp_path)
    settings = Settings(llm_model="model-a")
    # model-a is the configured default — picked while its record is
    # clean, and after a single failure (below the threshold).
    for _ in range(evolve.DEPRIORITIZE_MIN_FAILURES):
        _lesson(store, subject="model-a")
    entry, _ = evolve.pick_with_lessons(settings, store, TENANT,
                                        _entries("model-a", "model-b"))
    assert entry["model_id"] == "model-b", \
        "a repeatedly failing model must yield to a clean alternative"
    # When EVERY candidate is failing, the default order still wins
    # (deprioritization never leaves the picker with nothing).
    for _ in range(evolve.DEPRIORITIZE_MIN_FAILURES):
        _lesson(store, subject="model-b")
    entry, _ = evolve.pick_with_lessons(settings, store, TENANT,
                                        _entries("model-a", "model-b"))
    assert entry["model_id"] == "model-a"


def test_a_single_failure_does_not_deprioritize(tmp_path):
    store = _store(tmp_path)
    settings = Settings(llm_model="model-a")
    _lesson(store, subject="model-a")
    entry, _ = evolve.pick_with_lessons(settings, store, TENANT,
                                        _entries("model-a", "model-b"))
    assert entry["model_id"] == "model-a"


def test_completions_never_penalize(tmp_path):
    store = _store(tmp_path)
    settings = Settings(llm_model="model-a")
    for _ in range(5):
        _lesson(store, subject="model-a", outcome="completed",
                category="none", cause="mission completed")
    entry, _ = evolve.pick_with_lessons(settings, store, TENANT,
                                        _entries("model-a", "model-b"))
    assert entry["model_id"] == "model-a"


def test_evolve_report_shape_patterns_and_honesty(tmp_path):
    store = _store(tmp_path)
    for _ in range(3):
        _lesson(store, subject="model-a", category="provider_rate_limit",
                cause="gateway returned HTTP 429")
    _lesson(store, subject="fs_write", subject_type="tool",
            category="approval_denied", cause="user denied fs_write",
            source_kind="agent_session", source_id="ags_1",
            evidence_id="ags_1")
    report = evolve.build_report(store, TENANT)
    assert report["tenant_id"] == TENANT
    patterns = {(p["category"], p["subject"]): p["count"]
                for p in report["failure_patterns"]}
    assert patterns[("provider_rate_limit", "model-a")] == 3
    assert patterns[("approval_denied", "fs_write")] == 1
    assert len(report["lessons"]) == 4
    # The deprioritized model appears as an APPLIED tuning.
    applied = report["tunings"]["applied"]
    assert any(t["kind"] == "model_deprioritization"
               and t["model"] == "model-a" for t in applied)
    # The honesty line is IN the payload.
    assert report["safety"]["self_modification"] == "never"
    assert "never modifies its own code" in report["safety"]["statement"]
    # Proposed tunings are labelled proposals, nothing more.
    for tuning in report["tunings"]["proposed"]:
        assert tuning["kind"] == "proposal"


def test_mission_failure_hook_writes_a_lesson(tmp_path):
    """The worker's failure path records lessons with the routed
    model as subject when a route decision exists."""
    from app.worker import _mark_mission_failed, _mission_lesson_quietly
    store = _store(tmp_path)
    _mission_lesson_quietly(
        store, TENANT, "mis_x", "failed",
        cause="RuntimeError: gateway returned HTTP 503 (after 4 attempts)")
    lessons = evolve.list_lessons(store, TENANT)
    assert len(lessons) == 1
    assert lessons[0]["category"] == "provider_error"
    assert lessons[0]["source_kind"] == "mission"
    # _mark_mission_failed on a missing mission must stay quiet.
    _mark_mission_failed(store, TENANT, "mis_missing", error="boom")
