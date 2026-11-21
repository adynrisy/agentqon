"""Unit tests for app/chat_attachments.py — the composer + button's
server side. Pure functions: no server, no model, no network.

Covers the safety contract the feature promises: filenames can never
escape the tenant folder, caps are enforced, only allowlisted types
are stored, and the model context is honest about what it contains
(text folded in, images/PDFs named but never pretended to be read).
"""
import base64
import json

import pytest

from app.chat_attachments import (
    AttachmentError,
    build_context_section,
    safe_filename,
    safe_tenant,
    validate_and_store,
)


def _item(name, raw, media_type=""):
    return {"name": name, "media_type": media_type, "size": len(raw),
            "content_b64": base64.b64encode(raw).decode()}


def test_safe_filename_strips_directories_and_dots():
    assert safe_filename("../etc/passwd") == "passwd"
    assert safe_filename("a\\b\\c.txt") == "c.txt"
    assert safe_filename("...hidden") == "hidden"
    assert safe_filename("") == "file"
    assert safe_filename("...") == "file"
    assert safe_filename("my file!.txt") == "my_file_.txt"
    assert len(safe_filename("x" * 200 + ".txt")) <= 100


def test_safe_tenant_never_escapes():
    assert safe_tenant("org_demo") == "org_demo"
    assert safe_tenant("../../evil") == "evil"
    assert safe_tenant("") == "tenant"


def test_store_text_and_image_and_meta(tmp_path):
    metas = validate_and_store(tmp_path, "org_demo", "msg1", [
        _item("notes.txt", b"hello world", "text/plain"),
        _item("photo.png", b"\x89PNG fake", "image/png"),
    ])
    assert [m["name"] for m in metas] == ["notes.txt", "photo.png"]
    assert [m["kind"] for m in metas] == ["text", "image"]
    assert metas[0]["text_chars"] == len("hello world")
    assert metas[0]["_text"] == "hello world"
    base = tmp_path / "attachments" / "org_demo" / "msg1"
    assert (base / "notes.txt").read_bytes() == b"hello world"
    assert (base / "photo.png").read_bytes() == b"\x89PNG fake"
    meta = json.loads((base / "meta.json").read_text())
    assert meta["message_id"] == "msg1"
    assert all("_text" not in f and "content_b64" not in f
               for f in meta["files"]), "meta.json never carries contents"


def test_store_traversal_name_lands_inside(tmp_path):
    metas = validate_and_store(tmp_path, "org_demo", "msg1",
                               [_item("../../evil.txt", b"x")])
    assert metas[0]["stored_name"] == "evil.txt"
    base = (tmp_path / "attachments" / "org_demo" / "msg1").resolve()
    assert (base / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_store_rejects_disallowed_type_size_mismatch_and_bad_b64(tmp_path):
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m", [_item("run.exe", b"x")])
    bad = _item("a.txt", b"hello")
    bad["size"] = 999
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m", [bad])
    bad2 = _item("a.txt", b"hello")
    bad2["content_b64"] = "!!!not-base64!!!"
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m", [bad2])
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m", [])


def test_store_caps_count_per_file_and_total(tmp_path):
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m",
                           [_item(f"f{i}.txt", b"x") for i in range(6)])
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m",
                           [_item("big.txt", b"A" * 5_000_001)])
    with pytest.raises(AttachmentError):
        validate_and_store(tmp_path, "t", "m",
                           [_item(f"f{i}.txt", b"B" * 4_000_000)
                            for i in range(3)])


def test_store_duplicate_names_get_suffix_and_unreadable_flagged(tmp_path):
    metas = validate_and_store(tmp_path, "t", "m", [
        _item("a.txt", b"one"), _item("a.txt", b"two"),
        _item("bin.txt", b"\xff\xfe\x00bad"),
    ])
    assert [m["stored_name"] for m in metas] == ["a.txt", "a-2.txt", "bin.txt"]
    assert metas[2]["kind"] == "text-unreadable"
    assert "_text" not in metas[2]


def test_context_section_folds_text_and_names_images_honestly():
    metas = [
        {"name": "notes.txt", "size": 5, "kind": "text", "_text": "hello"},
        {"name": "photo.png", "size": 10, "kind": "image"},
        {"name": "doc.pdf", "size": 20, "kind": "pdf"},
        {"name": "bin.txt", "size": 3, "kind": "text-unreadable"},
    ]
    section = build_context_section(metas)
    assert "--- notes.txt ---" in section and "hello" in section
    assert "photo.png (image, 10 bytes)" in section
    assert "the model reads text only" in section
    assert "doc.pdf (PDF, 20 bytes)" in section
    assert "its text was not extracted" in section
    assert "could not be read as text" in section
    assert build_context_section([]) == ""


def test_context_section_truncates_long_text_with_a_note():
    long_text = "x" * 25_000
    section = build_context_section(
        [{"name": "big.md", "size": len(long_text), "kind": "text",
          "_text": long_text}])
    assert section.count("x") == 20_000
    assert "big.md continues past the included part" in section
