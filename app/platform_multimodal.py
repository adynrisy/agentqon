"""AgentQon — multi-modal pass-through, gated by capability probes.

Image and audio inputs reach the agent ONLY where the seated model's
capability probe actually supports them. The probe record distinguishes
two honesty levels:
- "probed": the capability was verified by a real probe run.
- "declared": the provider claims it, but it has not been probed here.

Only "probed" support opens the gate. A declared-but-unprobed
capability is treated as unsupported until probed — claiming support
from a brochure is how faked results happen.

Where the model does not support the input, the gate returns a
plain-words refusal and NO result at all. There is deliberately no
fallback that pretends to have seen the image or heard the audio
(for example, no invented description). A refusal is honest; a
fabricated description is not.
"""
from __future__ import annotations

import time

KINDS = ("image", "audio")
SUPPORTED_SOURCES = ("probed",)


class CapabilityGate:
    def __init__(self) -> None:
        # model -> {kind: {"supported": bool, "source": str, "at": float}}
        self._probes: dict[str, dict] = {}

    def record_probe(self, model: str, capabilities: dict) -> dict:
        """Record a probe result. ``capabilities`` maps kind ->
        (supported, source) where source is 'probed' or 'declared'.
        Unknown kinds are ignored, never stored as support."""
        entry = {}
        for kind, value in capabilities.items():
            if kind not in KINDS:
                continue
            supported, source = value
            entry[kind] = {"supported": bool(supported), "source": source,
                           "at": time.time()}
        self._probes[model] = entry
        return {model: dict(entry)}

    def supports(self, model: str, kind: str) -> bool:
        probe = self._probes.get(model, {}).get(kind)
        if probe is None:
            return False
        return bool(probe["supported"]) and probe["source"] in SUPPORTED_SOURCES

    def gate(self, *, model: str, kind: str, payload: object) -> dict:
        """The single entry point for multi-modal inputs. Returns
        either {"allowed": True, "payload": ...} or a refusal with a
        plain-words explanation and NO payload-derived result."""
        if kind not in KINDS:
            return {"allowed": False,
                    "reason": (f"Input type {kind!r} is not supported. "
                               "Only image and audio inputs are accepted.")}
        if payload is None:
            return {"allowed": False,
                    "reason": "No input was supplied, so there is nothing to pass on."}
        if self.supports(model, kind):
            return {"allowed": True, "model": model, "kind": kind,
                    "payload": payload,
                    "note": "Passed through: this model's capability was verified by probe."}
        probe = self._probes.get(model, {}).get(kind)
        if probe is None:
            reason = (f"The seated model ({model}) has not been probed for "
                      f"{kind} input, so this input cannot be used. "
                      "Probe the model first, or choose a model that has "
                      f"been probed for {kind}.")
        elif not probe["supported"]:
            reason = (f"The seated model ({model}) does not support {kind} "
                      "input. The input was not processed, and no result "
                      "has been invented in its place.")
        else:
            reason = (f"The seated model ({model}) is only declared to "
                      f"support {kind} input — it has not been verified by "
                      "probe here, so the input is not passed through. "
                      "Probe the model first.")
        return {"allowed": False, "reason": reason}
