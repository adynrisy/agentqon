"""W11 sixth-document gap-fix engines: one named test per section (§1-§19).

Pure-stdlib engines in app/sixthdoc.py; no live providers, no UI, no
new dependencies. The known flake test_queue.py::test_failed_job_retries...
is untouched (reported, never edited away).
"""
import time

from app.sixthdoc import (
    Analytics, CacheEntry, ConcurrencyControl, ContextItem, Evidence,
    IncidentTracker, MigrationChange, MigrationPlan, Policy, Profiler,
    ProvenanceRegistry, ReferenceCollection, ReferenceDoc, Requirement,
    ResourceLifecycle, ResultCache, SessionManager, TempResource, ToolContract,
    ToolRegistry, adapt, alternative_branches, apply_exclusions,
    architecture_summary, artifact_merge_assistant, budget_allocation,
)


def _reqs():
    from app.sixthdoc import extract_requirements
    return extract_requirements("The report must include sources. Do not invent numbers. Prefer short text.")


# §1
def test_s1_task_understanding():
    from app.sixthdoc import (clarification_cost, completion_conditions, detect_ambiguity,
                              detect_conflicts, detect_scope_change, decomposition_preview,
                              extract_requirements, hidden_dependencies, track_negative_constraints)
    reqs = _reqs()
    assert any(r.kind == "mandatory" for r in reqs)
    assert track_negative_constraints(reqs)  # must-not-dos tracked
    assert detect_ambiguity("Do this or that, as needed.")
    conflicts = detect_conflicts([Requirement("Always publish the report", "mandatory"),
                                  Requirement("Never publish the report", "mandatory", negative=True)])
    assert conflicts
    prev = decomposition_preview("The report must include sources.")
    assert prev["completion_conditions"] and prev["complexity"]["estimated_steps"] >= 1
    assert hidden_dependencies("Use the latest data from the repo.") 
    scope = detect_scope_change(reqs, "The report must include sources. Do not invent numbers. Prefer short text. Add a chart.")
    assert scope["material_change"] and scope["preserved"]
    assert clarification_cost("Write a summary.")["decision"] in ("ask", "proceed-with-reversible-assumption")


# §2
def test_s2_context_packaging_binding_preservation():
    from app.sixthdoc import (compress_context, context_manifest, deduplicate_context,
                              detect_context_contradictions, overflow_prediction,
                              preservation_checklist, provenance_map)
    reqs = _reqs()
    checklist = preservation_checklist(reqs)
    assert checklist  # mandatory constraints listed BEFORE summarization
    items = [ContextItem("The report must include sources.", "instructions", mandatory=True),
             ContextItem("The report must include sources.", "instructions", mandatory=True),
             ContextItem("extra note", "model_notes")]
    assert len(deduplicate_context(items)) == 2
    assert context_manifest(items)["mandatory_items"] == 2
    comp = compress_context(items, checklist)
    assert comp["valid"] and not comp["missing"]
    for c in checklist:
        assert c in comp["text"]  # mandatory constraints survive compression
    assert overflow_prediction(items, 10)["would_overflow"]
    assert provenance_map(items)["item-0"]["source"] == "instructions"
    assert detect_context_contradictions([ContextItem("Always publish", "files"),
                                          ContextItem("Never publish", "files")])


# §3
def test_s3_session_lifecycle_model_separate():
    mgr = SessionManager()
    s = mgr.create("s1", "owner", "agent-a", "model-1")
    assert mgr.readiness_handshake(s, warmup_s=0.5)["ok"]
    assert s.state == "ready"
    mgr.set_model_server("model-1", True)
    s.state = "crashed"
    health = mgr.model_vs_session_health(s)
    assert health["session_failed_model_healthy"] is True  # agent failed, model healthy
    assert mgr.crash_recovery(s)["recovered"]
    mgr.pause(s)
    mgr.resume(s)
    assert s.state == "running"
    mgr.renew(s, 60.0)
    assert mgr.restart_policy(s, 3).startswith("stop")
    new = mgr.retire_replace(s, "s2")
    assert s.state == "retired" and new.session_id == "s2"
    mgr.graceful_shutdown(new)
    assert new.state == "closed"
    assert mgr.orphans({10}, {"s1": 99}) == ["s1"]
    assert mgr.expire_idle(now=time.time() + 99999) is not None
    assert len(new.events) >= 2  # lifecycle event history


# §4
def test_s4_tool_contracts_success_vs_accomplished():
    reg = ToolRegistry()
    reg.register(ToolContract("write_file", "1.0", {"required": ["path"]}, {"type": "object"},
                              examples=[{"path": "a.txt"}], dependencies=["fs"]))
    assert reg.check_input("write_file", {})["ok"] is False
    assert reg.check_input("write_file", {"path": "a.txt"})["ok"]
    # Tool succeeded but no evidence -> NOT accomplished (binding principle)
    assert reg.check_postcondition("write_file", {"success": True}, "file written")["accomplished"] is False
    assert reg.check_postcondition("write_file", {"success": True, "evidence": "file exists"}, "file written")["accomplished"]
    assert reg.contract_test("write_file")["ok"]
    assert reg.compatibility("write_file", {"available": ["fs"]})["compatible"]
    assert reg.compatibility("write_file", {"available": []})["missing"] == ["fs"]
    assert reg.invocation_preview("write_file", {"path": "a.txt"})["arguments"]["path"] == "a.txt"
    from app.sixthdoc import mark_truncation
    marked = mark_truncation("x" * 100, 10)
    assert marked["truncated"] and marked["notice"]  # truncation notice everywhere


# §5
def test_s5_provenance_build_pass_links_command():
    prov = ProvenanceRegistry()
    rec = prov.build_pass_record("pytest -q", 0, "12 passed")
    assert rec["passes"] and rec["command"] == "pytest -q" and rec["exit_code"] == 0
    prov.link_claim("the build passes", [rec["evidence_id"]])
    assert prov.citation_valid("the build passes")
    prov.link_claim("everything is perfect", [])
    assert "everything is perfect" in prov.unsupported_claims()
    assert prov.claim_warnings()
    assert prov.coverage_report(["the build passes", "everything is perfect"])["covered"] == 1
    assert prov.export_manifest()["evidence"]
    ev = Evidence("e2", "source", "doc", observed=False)
    prov.add(ev)
    prov.link_claim("inferred claim", ["e2"])
    assert any("inference" in w["warning"] for w in prov.claim_warnings())


# §6
def test_s6_concurrency_stale_rejected_and_idempotent():
    cc = ConcurrencyControl()
    assert cc.write("r1", 0, "v1")["ok"]
    assert cc.write("r1", 0, "v2")["ok"] is False  # stale write rejected
    assert cc.stale_write_rejected("r1", 0)
    assert cc.acquire_lock("f", "a")["acquired"]
    assert cc.acquire_lock("f", "b")["acquired"] is False
    assert cc.lock_visibility()["f"]["owner"] == "a"
    assert cc.detect_deadlock({"a": "b", "b": "a"})
    assert cc.suppress_duplicate("ev1") is False
    assert cc.suppress_duplicate("ev1") is True  # duplicate suppressed
    assert cc.handle_out_of_order(1, 5)["action"] == "ignore-stale-snapshot"
    assert cc.idempotent("k", "done")["reused"] is False
    assert cc.idempotent("k", "done")["reused"] is True
    assert cc.reconcile({"r1": 1}, {})[0]["problem"] == "record without stored file"
    assert cc.external_modification("f", "aa", "bb")["modified_outside"]
    assert cc.mission_conflict_warning("m1", "m2", ["a.txt"])["warning"]


# §7
def test_s7_output_protocols_envelope_and_repair():
    from app.sixthdoc import (detect_malformed, enforce_size, make_envelope,
                              migrate_output, repair_json, standard_error, tool_call_result,
                              validate_envelope)
    env = make_envelope("done", result="ok", finish_reason="truncated")
    assert env["partial"] and env["continuation"]
    assert validate_envelope(env, ["status", "result"])["ok"]
    assert validate_envelope({}, ["status"])["ok"] is False
    assert migrate_output({"a": 1}, "output-v1", "output-v2")["data"]["schema_version"] == "output-v2"
    assert migrate_output({}, "output-v2", "output-v9")["ok"] is False
    assert standard_error("timeout")["message"] and "504" not in standard_error("timeout")["message"]
    assert repair_json('{"a": 1,}')["ok"]  # safe repair, re-validated
    assert repair_json('not json at all')["ok"] is False
    assert detect_malformed("{bad")["malformed"]
    sized = enforce_size("x" * 50, 10)
    assert sized["truncated"] and sized["continuation"]
    assert tool_call_result("t", False, None, "timeout")["message"]


# §8
def test_s8_profiling_answers_slow_because_of():
    p = Profiler()
    p.record("file_scan", 5.0)
    p.record("model", 1.0)
    p.record("database", 0.5)
    p.record("approval_wait", 2.0, "waited for a person")
    assert p.diagnose_slowness()["cause"] == "file_scan"
    assert p.slow_steps(2.0)
    assert p.critical_path()[0].stage == "file_scan"
    p.cache_hits, p.cache_misses = 3, 1
    assert p.cache_effectiveness()["hit_rate"] == 0.75
    assert p.regression_alert(1.0)["regression"]
    assert p.benchmark([1.0, 3.0])["mean_s"] == 2.0


# §9
def test_s9_adaptive_policies_bounded_and_never_silent():
    assert adapt("stronger_model_on_verify_failure", {"verify_failures": 1, "escalations": 0}).decision == "escalate-model"
    assert adapt("stronger_model_on_verify_failure", {"verify_failures": 1, "escalations": 2}).decision == "keep-model"
    assert adapt("fallback_provider", {"provider_failed": True}).decision == "use-fallback-provider"
    assert adapt("stop_after_unproductive_retries", {"unproductive_retries": 3}).decision == "stop"
    assert adapt("human_review_on_uncertainty", {"uncertainty": "high"}).decision == "ask-person"
    assert adapt("extra_budget_for_critical", {"critical_unresolved": True, "extra_fraction": 0.9}).decision == "refuse-extra-budget"
    assert adapt("deeper_testing_after_risky_change", {"risky": True}).decision == "deeper-tests"
    assert adapt("context_size_adaptation", {"over_limit": True}).decision == "shrink-context"
    assert adapt("sequential_when_parallelism_helps_little", {"parallel_gain": 1.0}).decision == "run-sequentially"
    from app.sixthdoc import refuse_silent_security_change
    assert refuse_silent_security_change({"weakens_security": True})["allowed"] is False
    assert refuse_silent_security_change({"bypasses_budget": True})["allowed"] is False
    d = adapt("timeout_from_history", {"history_s": [10.0, 20.0]})
    assert d.decision.startswith("timeout-")


# §10
def test_s10_cache_never_on_similar_prompts_alone():
    cache = ResultCache()
    assert ResultCache.eligible("deterministic", False)
    assert ResultCache.eligible("deterministic", True) is False  # sensitive never cached
    k1 = ResultCache.build_key("summarize", {"f": "a"}, ["read"], {"os": "linux"}, "fresh", "v1")
    k2 = ResultCache.build_key("summarize", {"f": "b"}, ["read"], {"os": "linux"}, "fresh", "v1")
    assert k1 != k2  # same-looking prompt, different inputs -> different key
    k3 = ResultCache.build_key("summarize", {"f": "a"}, ["write"], {"os": "linux"}, "fresh", "v1")
    assert k1 != k3  # permissions are part of the key
    cache.put(CacheEntry(k1, "out", "v1", "p1", "u1", provenance="file-a"))
    assert cache.get(k1, "p1", "u1")["hit"]
    assert cache.get(k1, "p2", "u1")["hit"] is False  # per-project isolation
    assert cache.get(k1, "p1", "u2")["hit"] is False  # per-user isolation
    assert cache.invalidate_on_change("file-a") == 1
    assert cache.get(k1, "p1", "u1")["hit"] is False
    cache.put(CacheEntry(k1, "out", "v1", "p1", "u1"))
    assert cache.invalidate(key=k1) == 1
    assert cache.effectiveness()["entries"] == 0


# §11
def test_s11_reference_data_distinct_from_skills():
    col = ReferenceCollection("manuals")
    doc = ReferenceDoc("d1", "The widget guide explains widgets.", "v1", metadata={"project": "p1"})
    col.register(doc)
    assert col.index_incremental(ReferenceDoc("d1", "The widget guide explains widgets well.", "v2"))["updated"]
    col.register(ReferenceDoc("d2", "The widget guide explains widgets well.", "v1", metadata={"project": "p1"}))
    assert col.duplicates()  # same text, two ids
    assert col.version_compare("d1", ReferenceDoc("d1", "other", "v3"))["changed"]
    assert col.chunk_map(col.docs["d1"], size=10)
    assert col.retrieve("widget", ["p1"], "u1")
    assert not col.retrieve("widget", ["other-project"], "u1")  # permission-aware
    assert col.snapshot("s1")["docs"] == 2
    assert col.restore("s1")["restored"]
    assert col.cleanup_deleted({"d1"}) == ["d2"]
    assert col.stale_warnings(7, {"d1": 30})


# §12
def test_s12_incidents_heuristic_never_proof():
    tr = IncidentTracker()
    inc = tr.repeated_auth_failures(6)
    assert inc and inc["severity"] == "high" and inc["certainty"] == "observed"
    assert tr.secret_exposure("key sk-abcdefgh1234 here")["severity"] == "critical"
    heur = tr.unusual_tool_pattern(["read"] * 12)
    assert heur and heur["certainty"] == "heuristic"
    assert "not proof" in heur["wording"]
    assert tr.contain(heur["incident_id"], ["pause the session"])["contained"]
    assert tr.revoke_credentials(heur["incident_id"], "tok-1")["credential_revoked"]
    assert tr.terminate_session(heur["incident_id"], "s1")["session_ended"] == "s1"
    assert tr.timeline(heur["incident_id"])
    assert tr.recovery_checklist("x")
    assert tr.export(heur["incident_id"])["found"] is not False
    assert tr.review(heur["incident_id"], "loop confirmed, not an attack")["recorded"]
    assert tr.correlate(["repeated_auth_failure"])


# §13
def test_s13_text_confusables_and_encoding_no_mangle():
    from app.sixthdoc import (confusable_warnings, convert_encoding, detect_language,
                              filename_check, grapheme_count, locale_number,
                              normalize_text, preserve_code_in_translation,
                              search_normalize, token_estimate)
    assert normalize_text("é") == "é"
    assert grapheme_count("é") == 1  # base + combining = one grapheme
    assert detect_language("привет мир тест")["uncertainty"] == "low"
    assert confusable_warnings("аdmin")  # Cyrillic a in identifier
    bad = convert_encoding(b"\xff\xfe bad", "utf-8")
    assert bad["ok"] is False  # refused, not silently mangled
    assert convert_encoding("hello".encode("utf-8"), "utf-8")["ok"]
    assert locale_number(1234.5, "de") == "1.234,50"
    assert search_normalize("Émile") == "emile"
    assert token_estimate("one two three") >= 3
    assert preserve_code_in_translation("Use `foo()` here.")["code_spans_kept"] == ["`foo()`"]
    assert filename_check("аdmin.txt")["ok"] is False


# §14
def test_s14_centralized_policy_folds_scattered_guards():
    pol = Policy()
    assert pol.check("steps", 201)["allowed"] is False  # step cap limit 200 folded in
    assert pol.check("steps", 25)["allowed"]
    assert pol.check("tool_calls_per_task", 101)["action"] == "cancel"
    assert pol.check("queued_jobs", 101)["action"] == "warn"  # soft warning
    assert pol.check("queued_jobs", 101, authorized_override=True)["allowed"]
    assert pol.check("artifact_bytes", 999999999)["action"] == "cancel"
    assert pol.check("artifact_bytes", 999999999, authorized_override=True)["allowed"] is False  # hard: no override
    assert "step_cap" in pol.folded_guards()
    pol.quotas_per_project["p1"] = {"tool_calls_per_task": 10}
    assert pol.check("tool_calls_per_task", 11, project="p1")["allowed"] is False


# §15
def test_s15_migration_code_rollback_not_db_rollback():
    plan = MigrationPlan("22.5.0", "22.6.0")
    plan.add(MigrationChange("db-1", "database", False, "needs a down-migration"))
    plan.add(MigrationChange("cfg-1", "config", True, "restore old file"))
    feas = plan.rollback_feasible()
    assert feas["fully_reversible"] is False
    assert feas["database_changes_need_own_rollback"] == ["db-1"]
    assert plan.dry_run()["ok"] is False  # irreversible + no backup
    plan.backup()
    assert plan.dry_run()["ok"]
    assert plan.preflight(False, True)["ok"] is False
    assert plan.apply(fail_at="cfg-1")["ok"] is False
    assert plan.integrity_check(False)["ok"] is False
    assert plan.health_check()["ok"]
    assert "22.5.0" in plan.release_notes()
    assert plan.deprecated_warnings({"old": 1}, ["old"])


# §16
def test_s16_analytics_minimized_and_w1_cost_seam():
    an = Analytics(cost_metric=lambda m: 2.0)
    an.record({"task_type": "report", "completed": True, "prompt": "SECRET PROMPT TEXT",
               "file_contents": "SECRET FILE", "corrections": 1, "retries": 2})
    stored = an.missions[0]
    assert "prompt" not in stored and "file_contents" not in stored  # privacy: minimized
    an.record({"task_type": "report", "completed": False})
    assert an.completion_rate_by_type()["report"] == 0.5
    assert an.rates()["correction_rate"] == 0.5
    assert an.cost_per_success()["cost_per_success"] == 2.0  # reads W1 metric, not recomputed
    assert Analytics().cost_per_success()["available"] is False  # seam unwired -> honest gap
    assert an.before_after("x")["before_completion"] >= 0
    assert an.time_saved() == 0.0


# §17
def test_s17_docgen_extracted_vs_assumed():
    from app.sixthdoc import (api_docs_from_spec, changelog_draft, config_reference,
                              docs_affected_by_change, docs_vs_config_comparison,
                              architecture_summary, troubleshooting_from_incidents,
                              undocumented_env_vars, undocumented_interfaces,
                              validate_links_examples)
    s = architecture_summary({"app/x.py": ["a", "b"]})
    assert s["extracted_from_code"] and s["assumed"] == []
    assert undocumented_interfaces(["a", "b"], ["a"]) == ["b"]
    assert config_reference({"x": 1})[0]["extracted"]
    spec = api_docs_from_spec({"endpoints": [{"method": "GET", "path": "/v1/x"}, {"path": "/bad"}]})
    assert len(spec["documented"]) == 1 and spec["rejected_unvalidated"] == 1
    assert docs_affected_by_change(["a.py"], {"doc.md": ["a.py"]}) == ["doc.md"]
    assert undocumented_env_vars(["A", "B"], ["A"]) == ["B"]
    assert changelog_draft(["fix x"])["extracted_from_record"] == ["fix x"]
    assert troubleshooting_from_incidents([{"kind": "k", "certainty": "observed", "detail": "d"}])[0]["verified"]
    assert validate_links_examples(["see https://example.com/x"], {"https://example.com/x"})[0]["known"]
    cmp_ = docs_vs_config_comparison(["a"], ["a", "b"])
    assert cmp_["actual_only"] == ["b"]


# §18
def test_s18_resource_lifecycle_never_deletes_referenced():
    rl = ResourceLifecycle()
    rl.allocate(TempResource("w1", "u1", "workspace", created_at=time.time() - 99999,
                             referenced_by=["mission-active"]))
    rl.allocate(TempResource("w2", "u1", "workspace", created_at=time.time() - 99999))
    rl.allocate(TempResource("w3", "u1", "artifact", created_at=time.time() - 99999, retained=True))
    out = rl.cleanup()
    assert out["deleted"] == ["w2"]
    assert "w1" in out["protected"] and "w3" in out["protected"]  # referenced/retained survive
    assert rl.retention_tier(TempResource("x", "u", "workspace", referenced_by=["m"])) == "referenced"
    assert rl.reserve_disk(1)["reserved"]
    assert rl.reserve_disk(rl.quota_bytes + 1)["reserved"] is False
    assert rl.orphan_processes({1}, {"p": 9}) == ["p"]
    assert rl.storage_accounting()["total"] == 2
    dry = rl.cleanup(dry_run=True)
    assert isinstance(dry, dict)


# §19
def test_s19_unusual_all_ten():
    from app.sixthdoc import (alternative_branches, artifact_merge_assistant,
                              change_impact_preview, deadline_feasibility,
                              environment_fingerprint, execution_differential,
                              progress_confidence, requirement_to_evidence_map,
                              trust_boundary_map, unproductive_work_detector)
    # 1 differential
    d = execution_differential([{"s": 1}, {"s": 2}], [{"s": 1}, {"s": 3}])
    assert d["first_divergence_at"] == 1
    # 2 unproductive
    u = unproductive_work_detector([{"tool": "read", "progress": False}] * 4)
    assert u["unproductive"]
    # 3 branches
    b = alternative_branches({"a": {"verified": True, "score": 2}, "b": {"verified": False, "score": 9}})
    assert b["best_verified"] == "a"  # unverified never wins
    # 4 fingerprint
    f = environment_fingerprint({"py": "3.12"}, {"x": "1.0"}, {"m": "local"}, {"k": "v"})
    assert f["fingerprint"]
    # 5 requirement -> evidence (critical path #1) — REUSE: proven judgment
    # is W12's build_proof_map core (app/integrity.py), not a second map.
    reqs = _reqs()
    m = requirement_to_evidence_map(reqs, {reqs[0].text: "artifact-1"})
    assert m["map"][reqs[0].text] == "artifact-1" and m["uncovered"]
    assert m["verdict"] == "not_proven"  # uncovered requirements stay unproven
    assert m["proven_count"] == 1 and m["total_count"] == len(reqs)
    assert len(m["proof_rows"]) == len(reqs)
    assert requirement_to_evidence_map(
        reqs, {r.text: "a" for r in reqs})["verdict"] == "proven"
    # 6 impact preview — REUSE: traversal is W12's blast_radius_preview
    # core (app/eval_ops.py), transitive; this layer only shapes inputs.
    imp = change_impact_preview(["a.py"], {"a.py": ["b.py"]}, {"b.py": ["t1"]}, {"b.py": ["w1"]})
    assert imp["affected_tests"] == ["t1"] and imp["affected_workflows"] == ["w1"]
    assert imp["blast_radius_cores"]  # core output carried, proving delegation
    transitive = change_impact_preview(["a.py"], {"a.py": ["b.py"], "b.py": ["c.py"]},
                                       {"c.py": ["t1"]}, {})
    assert "c.py" in transitive["affected_files"]  # transitive, not direct-only
    assert transitive["affected_tests"] == ["t1"]
    # 7 deadline
    assert deadline_feasibility(100, [10, 20], 5)["feasible"]
    assert deadline_feasibility(10, [100], 5)["feasible"] is False
    # 8 merge
    assert artifact_merge_assistant({"a": "x", "b": "x"})["merged"] == "x"
    assert artifact_merge_assistant({"a": "x", "b": "y"})["merged"] is None  # never blind overwrite
    # 9 trust boundary
    tb = trust_boundary_map()
    assert any("never" in str(x).lower() or "refused" in str(x).lower() for x in tb["boundaries"])
    # 10 confidence
    pc = progress_confidence([{"verified": True}, {"verified": False}])
    assert pc["measured_progress"] == 0.5 and pc["uncertain_estimate"] is None
