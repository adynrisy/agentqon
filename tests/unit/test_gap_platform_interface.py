"""W7 §8 interface rules + §B UI polish (static evidence for the UI block).

Python rules are tested directly. The browser block in
apps/web/index.html is verified by marker strings (this subagent role
has no live browser, so no browser visit is claimed).
"""
import os
import re

from app import platform_interface as pif

UI_PATH = os.path.join(os.path.dirname(__file__), "..", "..",
                       "apps", "web", "index.html")


def test_loading_delay_and_stale_recovery():
    assert pif.should_show_loading(100) is False
    assert pif.should_show_loading(300) is True
    stale = pif.loading_state(started_at=0.0, now=60.0)
    assert stale["state"] == "stale" and stale["recoverable"] is True
    assert "longer than expected" in stale["message"]


def test_focus_restoration_and_tooltip_clamping():
    tracker = pif.FocusTracker()
    tracker.save("composer")
    assert tracker.restore_target() == "composer"
    pos = pif.tooltip_position(x=9999, y=9999, width=200, height=100,
                               viewport_width=800, viewport_height=600)
    assert pos["x"] + 200 <= 800 and pos["y"] + 100 <= 600
    assert pos["clamped"] is True
    assert pos["delay_ms"] == 400


def test_wrap_truncate_and_clipboard_failure():
    wrapped = pif.wrap_long_text("x" * 250, width=80)
    assert all(len(line) <= 80 for line in wrapped.split("\n"))
    cut = pif.truncate_with_indicator("y" * 300, limit=200)
    assert cut["truncated"] is True and "cut" in cut["text"]
    failed = pif.clipboard_result(success=False, text="ID-123")
    assert failed["copied"] is False and "ID-123" in failed["text_to_copy"]


def test_toast_dedup_notification_priority_and_empty_state():
    dedup = pif.ToastDedup()
    assert dedup.show("hello")["shown"] is True
    assert dedup.show("hello")["shown"] is False
    ordered = pif.sort_notifications([
        {"priority": "info", "text": "i"},
        {"priority": "error", "text": "e"},
        {"priority": "warning", "text": "w"}])
    assert [n["priority"] for n in ordered] == ["error", "warning", "info"]
    empty = pif.empty_state(what="missions", next_step="Start one from the composer.")
    assert "nothing" in empty["message"] and "Start one" in empty["next_step"]


def test_unsaved_shortcut_conflict_announce_animation_stale():
    unsaved = pif.UnsavedChanges()
    unsaved.mark_dirty()
    assert unsaved.may_navigate()["allowed"] is False
    registry = pif.ShortcutRegistry()
    assert registry.register("?", "help")["registered"] is True
    conflict = registry.register("?", "search")
    assert conflict["registered"] is False and conflict["conflict"] is True
    announced = pif.announce("Mission complete", kind="info")
    assert announced["aria_live"] == "polite" and announced["text"]
    assert pif.animation_policy(prefers_reduced_motion=True)["animations_enabled"] is False
    stale = pif.stale_indicator(data_age_seconds=600)
    assert stale["stale"] is True and "Stale" in stale["label"]


def test_optimistic_rollback_reverts_and_explains():
    update = pif.OptimisticUpdate(initial="enabled")
    update.apply_optimistic("disabled")
    assert update.value == "disabled"
    result = update.reject("the server rejected the disable")
    assert result["state"] == "reverted" and update.value == "enabled"
    assert "not saved" in result["explanation"]


def test_ui_state_restoration_and_stale_response_guard():
    store = pif.UIStateStore()
    store.save({"panel_width": 320})
    assert store.restore() == {"panel_width": 320}
    guard = pif.StaleResponseGuard()
    first = guard.next_request()
    second = guard.next_request()
    # A slower older response must NEVER overwrite newer state.
    assert guard.accept(first, {"old": True})["accepted"] is False
    assert guard.accept(second, {"new": True})["accepted"] is True
    assert guard.dropped_stale == 1


def test_ui_block_markers_present_in_index_html():
    with open(UI_PATH) as handle:
        html = handle.read()
    for marker in (
            "W7 platform polish",
            "w7ShortcutsHelp",
            "w7Announcer",
            "acceptResponse",
            "data-w7-tip",
            "data-copy-id",
            "data-w7-expand",
            "data-why-disabled",
            "w7-legend",
            "prefers-reduced-motion",
            ":focus-visible",
            "Estimated",
            "Graph legend",
            "Simplified view warning",
            "Minimum confidence",
    ):
        assert marker in html, f"UI marker missing: {marker}"
    # No color-only status: legend text names every stance in words.
    assert "Support" in html and "Oppose" in html and "Neutral" in html
