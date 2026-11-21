"""Tenth audit document, Group 6 — local-model and hardware intelligence
(stdlib only).

AgentQon does not host models, so every signal here is caller-supplied
telemetry or measured locally. Nothing is invented: when telemetry is
absent, the schedulers say so instead of assuming peak performance.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field


# ---------------------------------------------- 31: Thermal-Aware Scheduling
def thermal_schedule(*, telemetry: dict | None, pending_jobs: list[dict]) -> dict:
    """telemetry: caller-supplied server readings, e.g. {"throttling": True,
    "sustained_seconds": 120}. None means no telemetry is available."""
    if telemetry is None:
        return {"action": "normal", "kept": [j["id"] for j in pending_jobs],
                "deferred": [],
                "note": "No thermal telemetry is available, so no thermal "
                        "assumption is made."}
    throttling = bool(telemetry.get("throttling"))
    sustained = float(telemetry.get("sustained_seconds", 0))
    if throttling and sustained >= 60:
        kept = [j["id"] for j in pending_jobs if j.get("urgent")]
        deferred = [j["id"] for j in pending_jobs if not j.get("urgent")]
        return {"action": "reduce_concurrency", "kept": kept, "deferred": deferred,
                "note": "Sustained throttling detected; nonurgent work deferred."}
    return {"action": "normal", "kept": [j["id"] for j in pending_jobs],
            "deferred": [],
            "note": "No sustained throttling; running normally."}


# -------------------------------------------- 32: Energy-Aware Mission Routing
def route_by_energy(modes: list[dict], *, on_battery: bool) -> dict:
    """Weigh energy alongside cost, latency and quality. On battery, energy
    counts double. Each mode: {name, energy, cost, latency, quality} where
    lower energy/cost/latency is better and higher quality is better."""
    if not modes:
        return {"choice": None, "note": "No execution modes to choose from."}
    energy_weight = 2.0 if on_battery else 1.0

    def score(mode: dict) -> float:
        return (energy_weight * mode["energy"] + mode["cost"]
                + mode["latency"] - mode["quality"])

    best = min(modes, key=score)
    return {"choice": best["name"], "scores": {m["name"]: score(m) for m in modes},
            "note": "Mode chosen weighing energy, cost, latency and quality."
            + (" Energy weighed double on battery." if on_battery else "")}


# ------------------------------------------------ 33: Model Warm-State Manager
class WarmStateManager:
    """Track unloaded / loading / ready / evicting states and use MEASURED
    load times to avoid unnecessary reloads."""

    def __init__(self):
        self._states: dict[str, str] = {}
        self._load_times: dict[str, list[float]] = {}

    def set_state(self, model_id: str, state: str) -> None:
        if state not in ("unloaded", "loading", "ready", "evicting"):
            raise ValueError(f"Unknown warm state: {state}")
        self._states[model_id] = state

    def record_load_time(self, model_id: str, seconds: float) -> None:
        self._load_times.setdefault(model_id, []).append(seconds)

    def average_load_time(self, model_id: str) -> float | None:
        times = self._load_times.get(model_id, [])
        return sum(times) / len(times) if times else None

    def plan(self, model_id: str) -> dict:
        state = self._states.get(model_id, "unloaded")
        if state == "ready":
            return {"action": "use_as_is", "reload": False,
                    "note": "Model is already ready; no reload needed."}
        if state == "loading":
            return {"action": "wait", "reload": False,
                    "note": "Model is already loading; do not start a second load."}
        avg = self.average_load_time(model_id)
        return {"action": "load", "reload": True,
                "expected_load_seconds": avg,
                "note": "Model needs loading."
                + (f" Measured load time averages {avg:.1f} seconds."
                   if avg is not None else " No load time measured yet.")}


# ------------------------------------------- 34: Hardware Contention Forecast
def contention_forecast(*, device_limits: dict, running: list[dict],
                        candidate: dict) -> dict:
    """Estimate the impact of co-running models/missions on shared RAM,
    memory bandwidth and actual device limits. All figures caller-supplied."""
    total_ram = sum(m.get("ram_mb", 0) for m in running) + candidate.get("ram_mb", 0)
    total_bw = sum(m.get("bandwidth_share", 0.0) for m in running) \
        + candidate.get("bandwidth_share", 0.0)
    problems = []
    if total_ram > device_limits.get("ram_mb", 0):
        problems.append(f"RAM needed ({total_ram} MB) exceeds device RAM "
                        f"({device_limits.get('ram_mb')} MB).")
    if total_bw > 1.0:
        problems.append("Combined memory-bandwidth demand exceeds what the device offers.")
    return {"fits": not problems, "total_ram_mb": total_ram,
            "total_bandwidth_share": total_bw, "problems": problems,
            "note": "The candidate fits alongside running work."
            if not problems
            else "Running this alongside current work would strain the device."}


# ----------------------------------- 35: Local Model Integrity Verification
def verify_model_file(path: str, *, expected_sha256: str | None = None,
                      manifest: dict | None = None) -> dict:
    """Verify a model file against a trusted checksum or signed manifest
    when available; detect unexpected changes before loading. Generalizes
    Shield product integrity to user models. Without a trusted checksum,
    the result honestly says it cannot verify."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        return {"ok": False, "sha256": None,
                "note": f"Model file could not be read: {exc}"}
    actual = digest.hexdigest()
    expected = expected_sha256
    if manifest is not None:
        expected = manifest.get(path) or manifest.get(path.split("/")[-1])
    if expected is None:
        return {"ok": False, "sha256": actual,
                "note": "No trusted checksum is available, so the file "
                        "cannot be verified before loading."}
    if actual == expected:
        return {"ok": True, "sha256": actual,
                "note": "Model file matches its trusted checksum."}
    return {"ok": False, "sha256": actual,
            "note": "Model file changed since its trusted checksum was recorded."}


# ------------------------------------------ 36: Capability Overclaim Detector
def detect_overclaim(*, advertised: dict, observed: dict) -> dict:
    """Compare advertised model features against observed test results under
    actual workload. Generalizes self-test probes: a feature that fails
    probes is flagged, not trusted."""
    flags = []
    for feature, claimed in advertised.items():
        if not claimed:
            continue
        result = observed.get(feature)
        if result is None:
            flags.append(f"{feature}: advertised but never tested")
        elif result.get("success_rate", 1.0) < 0.8:
            flags.append(
                f"{feature}: advertised but unreliable in tests "
                f"(success rate {result.get('success_rate')})")
    return {"overclaimed": flags,
            "note": "Advertised features match observed test results."
            if not flags
            else "Some advertised features do not hold up in tests."}
