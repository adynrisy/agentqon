"""W5 gap-fix: conversation management (expansion A)."""
import re
from pathlib import Path

import pytest

from app.conversation import (ActionLedger, DraftStore, MessageThread,
                              SendGuard, actions_for_block,
                              cancel_description, copy_content,
                              sanitize_streaming_markdown, should_scroll,
                              status_label)


def test_stable_ids_survive_rerender():
    thread = MessageThread("conv1")
    a = thread.add("user", "hello")
    thread.add("assistant", "hi")
    first = thread.render()
    second = thread.render()
    assert [m["message_id"] for m in first] == [m["message_id"] for m in second]
    assert first[0]["message_id"] == a.message_id


def test_all_status_labels_plain_words():
    for status in ("draft", "waiting", "generating", "completed", "failed",
                   "cancelled", "interrupted"):
        label = status_label(status)
        assert label and "{" not in label and "HTTP" not in label
    assert status_label("bogus") == "Status unknown"
    with pytest.raises(ValueError):
        MessageThread("c").add("user", "x", status="bogus")


def test_partial_stays_marked_and_retry_starts_clean():
    thread = MessageThread("c")
    msg = thread.add("assistant", "", status="generating")
    thread.mark_partial(msg.message_id, "half an answer…")
    kept = thread.get(msg.message_id)
    assert kept.partial is True and kept.status == "failed"
    assert kept.content == "half an answer…"
    retry = thread.retry(msg.message_id)
    assert retry.message_id != msg.message_id
    assert retry.content == ""
    assert retry.status == "waiting"
    assert retry.retried_from == msg.message_id
    # The original partial is untouched by the retry.
    assert thread.get(msg.message_id).content == "half an answer…"


def test_edit_requires_explicit_branch_or_replace():
    thread = MessageThread("c")
    msg = thread.add("user", "original")
    with pytest.raises(ValueError):
        thread.edit(msg.message_id, "edited", mode="")
    replaced = thread.edit(msg.message_id, "replaced", mode="replace")
    assert replaced.message_id == msg.message_id
    assert replaced.content == "replaced"
    assert replaced.edit_history[0]["content"] == "original"
    branched = thread.edit(msg.message_id, "branched", mode="branch")
    assert branched.message_id != msg.message_id
    assert branched.parent_id == msg.message_id
    assert thread.get(msg.message_id).content == "replaced"


def test_regeneration_never_repeats_external_actions():
    ledger = ActionLedger()
    assert ledger.record("email-1", "email.send")["already_done"] is False
    assert ledger.record("email-1", "email.send")["already_done"] is True
    ok, reason = ledger.regen_may_repeat("email-1")
    assert ok is False and "already ran" in reason
    ok, _ = ledger.regen_may_repeat("email-2")
    assert ok is True


def test_copy_copies_content_never_metadata():
    thread = MessageThread("c")
    msg = thread.add("assistant", "the answer")
    copied = copy_content(msg)
    assert copied == "the answer"
    assert msg.message_id not in copied and "failed" not in copied


def test_streaming_markdown_unclosed_fence_renders_sanely():
    text, incomplete, notes = sanitize_streaming_markdown(
        "Here is code:\n```python\nprint(1)")
    assert incomplete is True
    assert text.count("```") == 2  # closed for rendering
    assert "raw" not in text  # no markup soup marker needed; fence balanced
    clean, incomplete2, _ = sanitize_streaming_markdown(
        "Done:\n```\nx = 1\n```")
    assert incomplete2 is False


def test_streaming_markdown_unclosed_link_becomes_plain_text():
    text, incomplete, _ = sanitize_streaming_markdown("see [the guide](http")
    assert incomplete is True
    assert "[" not in text


def test_code_block_actions_only_where_supported():
    assert actions_for_block({"language": "python"}) == ["copy", "download"]
    diff_actions = actions_for_block({"language": "diff"})
    assert "apply-patch" in diff_actions
    assert "open-file" in actions_for_block({"language": "python",
                                             "path": "src/app.py"})
    assert "open-file" not in actions_for_block({"language": "python",
                                                 "path": "/etc/passwd"})
    assert "open-file" not in actions_for_block({"language": "python",
                                                 "path": "../secret.py"})
    assert "run-test" in actions_for_block({"language": "python",
                                            "path": "tests/test_x.py"})
    assert "run-test" not in actions_for_block({"language": "python",
                                                "path": "src/app.py"})


def test_scroll_never_drags_reader_from_earlier_content():
    assert should_scroll(user_at_bottom=True) is True
    assert should_scroll(user_at_bottom=False) is False


def test_draft_survives_navigation_and_refresh(tmp_path):
    path = tmp_path / "drafts.json"
    store = DraftStore(path)
    store.save("conv1", "unsent thought")
    # Navigation: same store, new view — draft still there.
    assert store.get("conv1") == "unsent thought"
    # Refresh: new store object reading the same file.
    assert DraftStore(path).get("conv1") == "unsent thought"
    store.clear("conv1")
    assert store.get("conv1") == ""


def test_double_submit_sends_once():
    guard = SendGuard()
    assert guard.claim("send-1") is True
    assert guard.claim("send-1") is False
    assert guard.claim("send-2") is True


def test_cancellation_states_what_stopped():
    assert "writing the reply" in cancel_description("generation")
    assert "tool was running" in cancel_description("tool_run")
    assert "still completing" in cancel_description("external_pending")


def test_ui_block_present_with_required_behaviours():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "apps/web/index.html").read_text()
    assert "window.AQConversation" in html
    assert "agentqon-chat-draft" in html
    assert "aqAtBottom" in html
    assert "data-aq-copy" in html
    assert "Stopped while writing the reply" in html
