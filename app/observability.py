"""AgentQon — observability (gap-security).

Metrics + traces for model calls, tool calls, queue depth and errors,
surfaced through the EXISTING minutes/usage surfaces — there is no
separate observability dashboard, by design. Per call the registry
records latency, success/failure and an error class (never a raw
traceback, never a secret). Traces are sampled (see reliability
TraceSampler) and searchable by correlation id.

This is an in-memory process registry: it describes this process's
recent work. Durable history stays where it already lives (usage
records, job rows, the security log).
"""
from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict

from .reliability import TraceSampler, new_tool_execution_id


class Metrics:
    def __init__(self, trace_rate: float = 0.1):
        self._lock = threading.Lock()
        self._model_calls: list[dict] = []
        self._tool_calls: list[dict] = []
        self._errors: Counter = Counter()
        self._queue_depth: int = 0
        self._queue_depth_samples: list[int] = []
        self._traces: list[dict] = []
        self._sampler = TraceSampler(rate=trace_rate)

    # ------------------------------------------------------------ record
    def record_model_call(self, provider: str, latency_ms: float, ok: bool,
                          error_class: str | None = None,
                          provider_request_id: str | None = None,
                          correlation_id: str | None = None) -> dict:
        entry = {"provider": provider, "latency_ms": round(float(latency_ms), 1),
                 "ok": bool(ok), "error_class": error_class,
                 "provider_request_id": provider_request_id,
                 "correlation_id": correlation_id, "at": time.time()}
        with self._lock:
            self._model_calls.append(entry)
            if not ok and error_class:
                self._errors[error_class] += 1
        return entry

    def record_tool_call(self, tool: str, latency_ms: float, ok: bool,
                         error_class: str | None = None,
                         tool_execution_id: str | None = None,
                         correlation_id: str | None = None) -> dict:
        entry = {"tool": tool, "latency_ms": round(float(latency_ms), 1),
                 "ok": bool(ok), "error_class": error_class,
                 "tool_execution_id": tool_execution_id or new_tool_execution_id(),
                 "correlation_id": correlation_id, "at": time.time()}
        with self._lock:
            self._tool_calls.append(entry)
            if not ok and error_class:
                self._errors[error_class] += 1
        return entry

    def record_queue_depth(self, depth: int) -> None:
        with self._lock:
            self._queue_depth = int(depth)
            self._queue_depth_samples.append(int(depth))

    def record_trace(self, correlation_id: str, kind: str, detail: dict) -> bool:
        """Sampled: returns True when the trace was kept."""
        if not self._sampler.keep(correlation_id + ":" + kind):
            return False
        with self._lock:
            self._traces.append({"correlation_id": correlation_id, "kind": kind,
                                 "detail": detail, "at": time.time()})
        return True

    # ------------------------------------------------------------ read
    def search_traces(self, correlation_id: str) -> list[dict]:
        with self._lock:
            return [t for t in self._traces if t["correlation_id"] == correlation_id]

    def _summarize(self, calls: list[dict], key: str) -> dict:
        grouped: dict[str, dict] = {}
        for call in calls:
            bucket = grouped.setdefault(call[key], {"calls": 0, "failures": 0,
                                                    "latency_ms": []})
            bucket["calls"] += 1
            if not call["ok"]:
                bucket["failures"] += 1
            bucket["latency_ms"].append(call["latency_ms"])
        out = {}
        for name, bucket in grouped.items():
            latencies = sorted(bucket["latency_ms"])
            out[name] = {"calls": bucket["calls"], "failures": bucket["failures"],
                         "avg_latency_ms": round(sum(latencies) / len(latencies), 1)
                         if latencies else 0,
                         "max_latency_ms": latencies[-1] if latencies else 0}
        return out

    def surface(self) -> dict:
        """The observability block for the existing usage surface:
        plain words first, numbers second, no new page."""
        with self._lock:
            model_calls = list(self._model_calls)
            tool_calls = list(self._tool_calls)
            errors = dict(self._errors)
            depth = self._queue_depth
            samples = list(self._queue_depth_samples)
        model = self._summarize(model_calls, "provider")
        tools = self._summarize(tool_calls, "tool")
        total_failures = sum(errors.values())
        lines = []
        if model_calls:
            lines.append(f"{len(model_calls)} model calls recorded, "
                         f"{total_failures} failures overall")
        if samples:
            lines.append(f"queue depth now {depth}, highest seen {max(samples)}")
        if errors:
            worst = max(errors, key=errors.get)
            lines.append(f"most common problem: {worst} ({errors[worst]} times)")
        return {"model_calls": model, "tool_calls": tools,
                "errors_by_class": errors, "queue_depth": depth,
                "plain_lines": lines or ["no calls recorded yet"]}


# Process-wide registry used by the usage surface.
METRICS = Metrics()
