"""AgentQon — multimodal quality pipeline framework (gap-seventhdoc, group C).

No extraction pipeline exists in the tree today (verified: the only
'ocr' hit is a display-name abbreviation), so this module BUILDS the
pipeline as a method-routed framework: extraction interfaces, confidence
gates, provenance, alignment validation, consistency checks, routing.

HONESTY RULE (binding): extraction itself runs ONLY via a caller-
supplied seated-model function where the seated model supports
vision/multimodal input. With no such function, extract() returns
status 'unavailable' — extraction results are NEVER invented and OCR
output is NEVER faked. Any extraction method needing a new dependency
is BLOCKED, not added (stdlib only here).
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional

OCR_LOW_CONFIDENCE = 0.70


def extract(media: dict, model_extract: Optional[Callable] = None) -> dict:
    """Run extraction through the seated model, or honestly report that
    extraction is unavailable. media: {media_id, kind, source}."""
    if model_extract is None:
        return {"media_id": media.get("media_id"), "status": "unavailable",
                "reason": "No seated model with vision/multimodal input is "
                "available for this extraction, so no text was produced.",
                "text": None, "method": None}
    result = model_extract(media)
    return {"media_id": media.get("media_id"), "status": "extracted",
            "text": result.get("text"), "method": result.get("method", "seated_model"),
            "confidence": result.get("confidence")}


def ocr_gate(fields: list[dict]) -> dict:
    """Low-confidence text is flagged; uncertain fields are HELD from
    important decisions pending another extraction method."""
    flagged, held, accepted = [], [], []
    for f in fields:
        conf = float(f.get("confidence", 0.0))
        if conf < OCR_LOW_CONFIDENCE:
            flagged.append(f["name"])
            held.append({"field": f["name"], "confidence": conf,
                         "reason": "Confidence is below the gate, so this "
                         "field is held from important decisions until "
                         "another extraction method checks it."})
        else:
            accepted.append(f["name"])
    return {"flagged": flagged, "held": held, "accepted": accepted}


def check_structure(original: dict, extracted: dict) -> dict:
    """Tables, headings, footnotes, columns, page order, references must
    survive extraction. Flattening that loses structure is flagged as
    potentially misleading."""
    lost = []
    for key in ("tables", "headings", "footnotes", "columns", "references"):
        if original.get(key) and not extracted.get(key):
            lost.append(key)
    order_ok = original.get("page_order") in (None, extracted.get("page_order"))
    if not order_ok:
        lost.append("page_order")
    return {"structure_lost": lost,
            "flattening_misleading": bool(lost),
            "reason": ("Extraction kept the document's structure." if not lost
                       else "Extraction lost: " + ", ".join(lost) + ". "
                       "Reading the flat text alone could mislead.")}


def check_cross_modal(claims: list[dict]) -> list[dict]:
    """Flag conflicts across modalities for the same key: e.g. a
    document value vs an embedded chart, a transcript timestamp vs a
    video scene. claim: {key, modality, value}."""
    by_key: dict[str, dict] = {}
    conflicts = []
    for c in claims:
        prev = by_key.get(c["key"])
        if prev and str(prev["value"]) != str(c["value"]):
            conflicts.append({"key": c["key"],
                              "reason": f"{prev['modality']} says "
                              f"'{prev['value']}' but {c['modality']} says "
                              f"'{c['value']}'.",
                              "first": prev, "second": c})
        else:
            by_key[c["key"]] = c
    return conflicts


class ProvenanceTracker:
    """Origin, transformations, timestamps, hashes, extraction methods
    for images, audio, video, documents."""

    def __init__(self):
        self.records: dict[str, list[dict]] = {}

    def record(self, media_id: str, origin: str, transformation: str,
               content_hash: str, method: str, timestamp: str) -> dict:
        entry = {"media_id": media_id, "origin": origin,
                 "transformation": transformation, "hash": content_hash,
                 "method": method, "timestamp": timestamp}
        self.records.setdefault(media_id, []).append(entry)
        return entry

    def chain(self, media_id: str) -> list[dict]:
        return list(self.records.get(media_id, []))

    @staticmethod
    def hash_content(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


def check_alignment(subtitles: list[dict], audio_segments: list[dict],
                    frames: list[dict], tolerance_s: float = 0.5) -> list[dict]:
    """Subtitles to audio, video frames to timestamps: a subtitle whose
    window overlaps no audio segment (beyond tolerance) is misaligned;
    a frame timestamp outside every segment is misaligned."""
    problems = []
    for sub in subtitles:
        ok = any(seg["start"] - tolerance_s <= sub["start"]
                 and sub["end"] <= seg["end"] + tolerance_s
                 for seg in audio_segments)
        if not ok:
            problems.append({"kind": "subtitle_misaligned", "item": sub,
                             "reason": "This subtitle lines up with no spoken "
                             "audio segment."})
    for fr in frames:
        ok = any(seg["start"] - tolerance_s <= fr["timestamp"] <= seg["end"] + tolerance_s
                 for seg in audio_segments)
        if not ok:
            problems.append({"kind": "frame_misaligned", "item": fr,
                             "reason": "This video frame falls outside every "
                             "audio segment."})
    return problems


def route_extraction(confidence: float, second_method_cost: float,
                     expected_benefit: float) -> dict:
    """Uncertain content goes to a second method / different model /
    human review ONLY when the expected benefit justifies the cost.
    Otherwise it stays, flagged, rather than spending on a low-value
    re-check."""
    if confidence >= OCR_LOW_CONFIDENCE:
        return {"route": "accept", "reason": "Confidence passes the gate."}
    if expected_benefit > second_method_cost:
        return {"route": "second_method",
                "reason": "Confidence is low and the expected benefit of "
                "a second check is worth its cost."}
    return {"route": "keep_flagged",
            "reason": "Confidence is low, but a second check would cost "
            "more than it is expected to gain, so the content stays "
            "flagged instead of being re-checked."}
