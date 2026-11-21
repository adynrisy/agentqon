"""W1 §2 token-saving utilities: each function reports what it removed
and marks partial output as partial. Governing metric remains total
cost per successfully completed task (see test_cost_engine regression)."""
from app import context_compact as cc


def test_repeated_sentences_detected_and_removed():
    text = "Cats sleep a lot. Cats sleep a lot. Dogs bark."
    assert cc.detect_repeated_sentences(text)[0]["count"] == 2
    out = cc.remove_repeated_sentences(text)
    assert out["removed_sentences"] == 1
    assert out["text"].count("Cats sleep a lot.") == 1


def test_dedup_tool_results_references_first():
    results = [{"id": "r1", "tool": "search", "content": "abc"},
               {"id": "r2", "tool": "search", "content": "abc"},
               {"id": "r3", "tool": "read", "content": "xyz"}]
    out = cc.dedup_tool_results(results)
    assert out["duplicates_removed"] == 1
    assert out["results"][1]["reference_to"] == "r1"


def test_strip_redundant_metadata_only_duplicates_and_empties():
    obj = {"a": "same", "b": "same", "c": "unique", "meta": {}}
    out = cc.strip_redundant_metadata(obj)
    assert out["data"] == {"a": "same", "c": "unique"}
    assert set(out["removed_keys"]) == {"b", "meta"}


def test_history_filtering_by_relevance_not_recency():
    turns = [{"content": "unrelated weather chat"},
             {"content": "the database index is slow"},
             {"content": "more weather chat, very recent"}]
    out = cc.filter_history_by_relevance(turns, "database index slow", keep_top_n=1)
    contents = [t["content"] for t in out["turns"]]
    assert "the database index is slow" in contents
    assert "more weather chat, very recent" not in contents
    assert out["dropped"] == 1


def test_compact_timestamp_shortens():
    assert cc.compact_timestamp("2026-10-24T13:43:52+00:00") == "10-24 13:43"
    assert cc.compact_timestamp("not-a-date") == "not-a-date"


def test_identifier_aliasing_unambiguous_mapping():
    long_id = "a" * 30
    text = f"Use {long_id} then check {long_id} again."
    out = cc.alias_identifiers(text, [long_id])
    assert out["text"].count("A1") == 2
    assert out["mapping"] == {long_id: "A1"}
    assert out["reverse_mapping"] == {"A1": long_id}
    # short single-use identifiers are left alone
    assert cc.alias_identifiers("Use abc once.", ["abc"])["mapping"] == {}


def test_structured_compaction_marks_cuts():
    obj, cut = cc.compact_structured({"items": list(range(20)), "empty": "",
                                      "long": "x" * 600})
    assert cut > 0
    assert any("more items hidden" in str(v) for v in obj["items"])
    assert "shortened" in obj["long"]
    assert "empty" not in obj


def test_repeated_error_suppression_keeps_count():
    out = cc.suppress_repeated_errors(["boom", "boom", "boom", "other"])
    assert out["suppressed_repeats"] == 2
    assert "repeated 3 times" in out["errors"][0]


def test_relevant_log_extraction():
    lines = ["INFO start", "ERROR disk full", "INFO tick", "ERROR disk full again"]
    out = cc.extract_relevant_logs(lines, ["error"])
    assert out["kept"] == 2 and out["dropped"] == 2


def test_diff_context_only_changes():
    out = cc.diff_context("a\nb\nc", "a\nB\nc\nd")
    assert any("B" in ln for ln in out["changed_lines"])
    assert out["unchanged_lines_suppressed"] >= 2


def test_file_range_selection_states_slice():
    lines = [f"line {i}" for i in range(1, 101)]
    out = cc.select_file_range(lines, 50, 52)
    assert out["total_lines"] == 100
    assert "slice" in out["note"]
    assert out["lines"][2] == "line 50"


def test_search_result_dedup_by_url():
    results = [{"url": "https://example.com/a", "title": "A"},
               {"url": "https://example.com/a/", "title": "A again"},
               {"url": "https://example.com/b", "title": "B"}]
    out = cc.dedup_search_results(results)
    assert out["duplicates_removed"] == 1
    assert len(out["results"]) == 2


def test_output_budget_per_step_allocates():
    budgets = cc.budget_output_per_step(["summary", "full detail"], 300)
    assert budgets["full detail"] > budgets["summary"]
    assert sum(budgets.values()) == 300


def test_unchanged_state_suppressed_changed_sent():
    assert cc.suppress_unchanged_state({"a": 1}, {"a": 1})["suppressed"] is True
    changed = cc.suppress_unchanged_state({"a": 1}, {"a": 2})
    assert changed["suppressed"] is False and changed["value"] == {"a": 2}


def test_repeated_prefix_detection():
    prefix = "You are a helpful assistant. " * 10
    prompts = [prefix + "Answer Q1", prefix + "Answer Q2", "Different prompt"]
    out = cc.detect_repeated_prefix(prompts)
    assert out["prompts_affected"] == 2
    assert out["estimated_tokens"] > 0
    assert cc.detect_repeated_prefix(["short", "other"])["prompts_affected"] == 0


def test_context_contribution_measurement():
    sections = [{"name": "history", "content": "word " * 500},
                {"name": "task", "content": "do the thing"}]
    out = cc.measure_context_contribution(sections, query="do the thing")
    assert out["total_estimated_tokens"] > 0
    assert out["largest_section"]["name"] == "history"
    assert out["least_relevant_section"]["name"] == "history"
    assert abs(sum(s["share_pct"] for s in out["sections"]) - 100.0) < 0.1
