"""§16 Replay — reproduce a run, or prove why it cannot be reproduced.

SAFETY RULE: when the original run performed external actions, replay
defaults to safe inspection/test mode — it never blindly resends payments,
emails, deletions or irreversible requests. Tool I/O is captured only
under privacy controls (secrets redacted before storage).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

EXTERNAL_STEPS = {"email.send", "payment.send", "file.delete", "publish",
                  "remote.change"}


def redact_io(payload: dict) -> dict:
    out = {}
    for key, value in payload.items():
        if key.lower() in ("password", "token", "secret", "api_key", "apiKey"):
            out[key] = "[redacted]"
        else:
            out[key] = value
    return out


@dataclass
class TraceStep:
    step_id: str
    kind: str
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    model: str = ""
    skill_version: str = ""
    ok: bool = True


@dataclass
class Trace:
    trace_id: str
    plan: list[str] = field(default_factory=list)
    steps: list[TraceStep] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    app_version: str = ""
    seeds: dict = field(default_factory=dict)
    performed_external_actions: bool = False

    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.config, sort_keys=True,
                                         default=str).encode()).hexdigest()[:16]


def replay_plan(trace: Trace, safe_mode: bool | None = None) -> dict:
    """Replay the whole plan. Safe mode is the DEFAULT after external actions."""
    if safe_mode is None:
        safe_mode = trace.performed_external_actions
    return {"trace_id": trace.trace_id, "safe_mode": safe_mode,
            "steps": len(trace.steps),
            "warning": ("Original run performed external actions — replay runs "
                        "in safe inspection mode and will not resend them."
                        if safe_mode else ""),
            "config_hash": trace.config_hash()}


def rerun_step(trace: Trace, step_id: str, model: str | None = None,
               skill_version: str | None = None) -> dict:
    step = next((s for s in trace.steps if s.step_id == step_id), None)
    if step is None:
        return {"ok": False, "reason": "No such step in the trace."}
    substitution = {}
    if model and model != step.model:
        substitution["model"] = {"from": step.model, "to": model}
    if skill_version and skill_version != step.skill_version:
        substitution["skill_version"] = {"from": step.skill_version,
                                         "to": skill_version}
    safe = step.kind in EXTERNAL_STEPS
    return {"ok": True, "step_id": step_id, "substitutions": substitution,
            "safe_mode": safe,
            "note": ("This step performed an external action in the original "
                     "run — it replays in safe mode only." if safe else "")}


def compare_traces(a: Trace, b: Trace) -> dict:
    divergence = None
    for i, (sa, sb) in enumerate(zip(a.steps, b.steps)):
        if sa.outputs != sb.outputs or sa.kind != sb.kind:
            divergence = {"step_index": i, "step_id": sa.step_id,
                          "first_difference": "outputs or step kind differ"}
            break
    if divergence is None and len(a.steps) != len(b.steps):
        divergence = {"step_index": min(len(a.steps), len(b.steps)),
                      "first_difference": "trace lengths differ"}
    artifacts_same = [s.outputs for s in a.steps] == [s.outputs for s in b.steps]
    return {"first_divergence": divergence, "artifacts_same": artifacts_same,
            "config_hashes": {"a": a.config_hash(), "b": b.config_hash()},
            "config_changed": a.config_hash() != b.config_hash()}


def non_reproducible(trace: Trace) -> list[str]:
    """External dependencies that make exact reproduction impossible."""
    reasons = []
    for step in trace.steps:
        if step.kind in EXTERNAL_STEPS:
            reasons.append(f"Step {step.step_id} ({step.kind}) touched the "
                           "outside world.")
    if trace.environment.get("live_web"):
        reasons.append("Original run read live web pages, which change over time.")
    return reasons


def export_bundle(trace: Trace) -> dict:
    return {"trace_id": trace.trace_id, "app_version": trace.app_version,
            "environment": trace.environment, "seeds": trace.seeds,
            "config_hash": trace.config_hash(),
            "steps": [{"step_id": s.step_id, "kind": s.kind,
                       "inputs": redact_io(s.inputs),
                       "outputs": redact_io(s.outputs),
                       "model": s.model, "skill_version": s.skill_version,
                       "ok": s.ok} for s in trace.steps],
            "non_reproducible": non_reproducible(trace)}
