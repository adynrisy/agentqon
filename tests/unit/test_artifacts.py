"""W3 Artifacts: naming, IDs, type detection, integrity, versions,
diffs, duplicates, download verification, content-type validation,
large-file streaming, backup/recovery, final-output selection, and
retention — all owned by W3.
"""
from datetime import datetime, timedelta, timezone

from app.artifacts import (RETENTION_DAYS, ArtifactStore,
                           artifact_filename, detect_content_type,
                           diff_text, hash_file_streaming, retention_plan,
                           safe_filename, validate_content_type)


def _store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts")


def test_naming_predictable_collision_free_and_safe():
    name = artifact_filename("report", "Final Report", "abcdef0123456789",
                             existing=set())
    assert name.startswith("report-final-report-abcdef01")
    # Same content + title -> same name; different content -> different.
    assert artifact_filename("report", "Final Report", "abcdef0123456789",
                             existing=set()) == name
    other = artifact_filename("report", "Final Report", "9999999900000000",
                              existing=set())
    assert other != name
    # A taken name for other content gets a suffix, never an overwrite.
    suffixed = artifact_filename("report", "Final Report", "abcdef0123456789",
                                 existing={name})
    assert suffixed != name and suffixed.startswith(name.rsplit(".", 1)[0])
    assert ".." not in safe_filename("../../etc/passwd")
    assert "/" not in safe_filename("a/b\\c:d")


def test_unique_ids_stable_and_distinct(tmp_path):
    store = _store(tmp_path)
    first = store.save_bytes(b'{"a": 1}', mission_id="m1", kind="report",
                             title="Report", declared_type="json")
    again = store.get(first["artifact_id"])
    assert again["artifact_id"] == first["artifact_id"]
    assert again["content_hash"] == first["content_hash"]
    second = store.save_bytes(b'{"a": 2}', mission_id="m1", kind="report",
                              title="Report", declared_type="json")
    assert second["artifact_id"] != first["artifact_id"]


def test_type_detection_from_content_not_extension():
    assert detect_content_type(b'{"a": 1}') == "json"
    assert detect_content_type(b"\x89PNG\r\n\x1a\nxxxx") == "png"
    assert detect_content_type(b"%PDF-1.4 fake") == "pdf"
    assert detect_content_type(b"plain words here") == "text"
    # JSON bytes are JSON even if a caller called the file .txt.
    assert detect_content_type(b'{"a": 1}') != "text"


def test_integrity_mismatch_flagged(tmp_path):
    store = _store(tmp_path)
    rec = store.save_bytes(b"hello", mission_id="m1", kind="note",
                           title="Note", declared_type="text")
    ok, _ = store.verify_integrity(rec["artifact_id"])
    assert ok is True
    (store.root / rec["filename"]).write_bytes(b"tampered")
    ok, message = store.verify_integrity(rec["artifact_id"])
    assert ok is False
    assert "no longer matches" in message


def test_version_tracking_keeps_old_versions_readable(tmp_path):
    store = _store(tmp_path)
    v1 = store.save_bytes(b"version one", mission_id="m1", kind="report",
                          title="Report", declared_type="text")
    v2 = store.save_bytes(b"version two", mission_id="m1", kind="report",
                          title="Report", declared_type="text")
    assert v1["version"] == 1 and v2["version"] == 2
    assert store.get_version("m1", "report", 1)["artifact_id"] == v1["artifact_id"]
    assert store.read_bytes(v1["artifact_id"]) == b"version one"


def test_diff_generation_plain_words_and_unified(tmp_path):
    store = _store(tmp_path)
    store.save_bytes(b"line one\nline two\n", mission_id="m1", kind="report",
                     title="Report", declared_type="text")
    store.save_bytes(b"line one\nline three\n", mission_id="m1", kind="report",
                     title="Report", declared_type="text")
    diff = store.diff_versions("m1", "report", 1, 2)
    assert "1 line(s) added and 1 line(s) removed" in diff["summary"]
    assert "-line two" in diff["diff"] and "+line three" in diff["diff"]
    plain = diff_text(b"same\n", b"same\n")
    assert plain["changed"] is False


def test_duplicate_detection_returns_existing_record(tmp_path):
    store = _store(tmp_path)
    first = store.save_bytes(b"same content", mission_id="m1", kind="note",
                             title="Note", declared_type="text")
    second = store.save_bytes(b"same content", mission_id="m1", kind="note",
                              title="Note", declared_type="text")
    assert second["artifact_id"] == first["artifact_id"]
    assert second["duplicate_of_existing"] is True
    assert len(store.list_for_mission("m1")) == 1


def test_download_integrity_verification(tmp_path):
    store = _store(tmp_path)
    rec = store.save_bytes(b"payload", mission_id="m1", kind="file",
                           title="File", declared_type="text")
    ok, _ = store.verify_download(b"payload", rec["artifact_id"])
    assert ok is True
    ok, message = store.verify_download(b"payloax", rec["artifact_id"])
    assert ok is False
    assert "does not match" in message


def test_content_type_validation_refuses_mismatch():
    ok, message = validate_content_type(b"<html><body>hi</body></html>", "json")
    assert ok is False
    assert "refused" in message
    ok, _ = validate_content_type(b'{"a": 1}', "json")
    assert ok is True
    ok, _ = validate_content_type(b"not json at all {{{", "json")
    assert ok is False


def test_large_file_streams_without_loading_content_into_record(tmp_path):
    big = tmp_path / "big.bin"
    chunk = b"x" * 1024 * 1024
    with open(big, "wb") as fh:
        fh.write(b"PK\x03\x04")  # zip signature, so the header check passes
        for _ in range(6):
            fh.write(chunk)
    store = _store(tmp_path)
    rec = store.save_file(big, mission_id="m1", kind="archive",
                          title="Big archive", declared_type="zip",
                          cls="intermediate")
    # The record holds a reference, never the content.
    assert "x" * 100 not in str(rec)
    assert rec["size_bytes"] == 6 * 1024 * 1024 + 4
    assert rec["content_hash"] == hash_file_streaming(big)
    ok, _ = store.verify_integrity(rec["artifact_id"])
    assert ok is True


def test_backup_and_restore_round_trip(tmp_path):
    store = _store(tmp_path)
    rec = store.save_bytes(b"precious", mission_id="m1", kind="report",
                           title="Report", declared_type="text")
    backup = store.backup(rec["artifact_id"], tmp_path / "backup")
    assert backup["ok"] is True
    (store.root / rec["filename"]).write_bytes(b"destroyed")
    restored = store.restore(backup["backup_path"], rec["artifact_id"])
    assert restored["ok"] is True
    assert store.read_bytes(rec["artifact_id"]) == b"precious"
    # A mismatched backup is refused, never written over the file.
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"wrong")
    refused = store.restore(bad, rec["artifact_id"])
    assert refused["ok"] is False
    assert store.read_bytes(rec["artifact_id"]) == b"precious"


def test_final_output_selection_exactly_one_final(tmp_path):
    store = _store(tmp_path)
    draft = store.save_bytes(b"draft text", mission_id="m1", kind="report",
                             title="Draft", declared_type="text", cls="draft")
    final = store.save_bytes(b"final text", mission_id="m1", kind="report",
                             title="Final", declared_type="text", cls="draft")
    v2 = store.save_bytes(b"final v2 text", mission_id="m1", kind="report",
                          title="Final v2", declared_type="text", cls="draft")
    assert store.current_final("m1", "report") is None
    store.mark_final(final["artifact_id"])
    assert store.current_final("m1", "report")["artifact_id"] == final["artifact_id"]
    # Marking a second final unmarks the first: no report_final_v2 confusion.
    store.mark_final(v2["artifact_id"])
    assert store.current_final("m1", "report")["artifact_id"] == v2["artifact_id"]
    assert store.get(final["artifact_id"])["is_final"] is False
    assert store.get(draft["artifact_id"])["class"] == "draft"


def test_retention_policies_stated_and_due_listed():
    assert RETENTION_DAYS["final"] is None
    assert RETENTION_DAYS["draft"] == 90
    old = {"artifact_id": "art_old", "class": "draft",
           "created_at": (datetime.now(timezone.utc)
                          - timedelta(days=100)).isoformat()}
    fresh = {"artifact_id": "art_new", "class": "draft",
             "created_at": datetime.now(timezone.utc).isoformat()}
    final = {"artifact_id": "art_final", "class": "final",
             "created_at": (datetime.now(timezone.utc)
                            - timedelta(days=3650)).isoformat()}
    plan = retention_plan([old, fresh, final])
    assert plan["due"] == ["art_old"]
    assert plan["kept_count"] == 2
    assert "final" in plan["policy"]
