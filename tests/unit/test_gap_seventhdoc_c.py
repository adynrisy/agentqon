"""Gap-seventhdoc group C: multimodal pipeline framework (honest: no
invented extraction)."""
from app.multimodal import (
    ProvenanceTracker, check_alignment, check_cross_modal, check_structure,
    extract, ocr_gate, route_extraction)


def test_c11_ocr_gate_holds_low_confidence_fields():
    out = ocr_gate([{"name": "total", "confidence": 0.95},
                    {"name": "date", "confidence": 0.4}])
    assert out["accepted"] == ["total"]
    assert out["held"][0]["field"] == "date"


def test_c12_structure_loss_flags_misleading_flattening():
    out = check_structure(
        {"tables": ["t1"], "headings": ["h"], "page_order": [1, 2]},
        {"headings": ["h"], "page_order": [1, 2]})
    assert out["flattening_misleading"] is True
    assert "tables" in out["structure_lost"]
    ok = check_structure({"tables": ["t1"]}, {"tables": ["t1"]})
    assert ok["flattening_misleading"] is False


def test_c13_cross_modal_conflict_flagged():
    conflicts = check_cross_modal([
        {"key": "revenue", "modality": "document", "value": "12"},
        {"key": "revenue", "modality": "chart", "value": "19"}])
    assert len(conflicts) == 1 and conflicts[0]["key"] == "revenue"
    assert check_cross_modal([
        {"key": "revenue", "modality": "document", "value": "12"},
        {"key": "revenue", "modality": "chart", "value": "12"}]) == []


def test_c14_provenance_chain_and_hash():
    tr = ProvenanceTracker()
    h = ProvenanceTracker.hash_content(b"hello")
    tr.record("img1", "upload", "resized", h, "seated_model", "2026-10-24T00:00:00Z")
    tr.record("img1", "upload", "cropped", h, "seated_model", "2026-10-24T00:01:00Z")
    chain = tr.chain("img1")
    assert len(chain) == 2 and chain[0]["hash"] == h


def test_c15_alignment_flags_subtitle_and_frame_outside_audio():
    problems = check_alignment(
        [{"start": 1.0, "end": 2.0}, {"start": 50.0, "end": 51.0}],
        [{"start": 0.0, "end": 10.0}],
        [{"timestamp": 5.0}, {"timestamp": 99.0}])
    kinds = [p["kind"] for p in problems]
    assert kinds == ["subtitle_misaligned", "frame_misaligned"]


def test_c16_router_only_when_benefit_justifies_cost_and_no_fake_extraction():
    assert route_extraction(0.9, 5.0, 100.0)["route"] == "accept"
    assert route_extraction(0.4, 5.0, 100.0)["route"] == "second_method"
    assert route_extraction(0.4, 100.0, 5.0)["route"] == "keep_flagged"
    unavailable = extract({"media_id": "m1", "kind": "image"})
    assert unavailable["status"] == "unavailable"
    assert unavailable["text"] is None
    made = extract({"media_id": "m1", "kind": "image"},
                   model_extract=lambda m: {"text": "hello", "confidence": 0.9,
                                            "method": "seated_model"})
    assert made["status"] == "extracted" and made["text"] == "hello"
