"""Deterministic stub LLM for the test suite (v20).

The product ships NO canned generation: without a real model it
refuses to generate (see app/modelstate.py). The suite still needs
missions to complete deterministically, so this module provides the
one sanctioned fake — a tiny OpenAI-compatible endpoint that exists
only inside the test process:

  * `start()` boots it on 127.0.0.1 (a real HTTP server, so API-level
    tests exercise the genuine adapter + routing code paths);
  * `settings()` returns production Settings pointed at it through
    the ordinary env-endpoint configuration seam;
  * `transport()` returns an httpx MockTransport wrapping the same
    response logic for adapter-level unit tests.

Its replies are prompt-shaped fixtures: strategy candidates keyed on
the assigned strategy brief, general-task deliverables keyed on the
objective, and critic replies that target the weakest claim in the
digest (contested first, then unsourced). Nothing in app/ imports or
can reach this module.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from app.config import Settings

STUB_MODEL = "stub-model-1"
MODELS = [{"id": "stub-model-1", "owned_by": "stub"},
          {"id": "stub-model-2", "owned_by": "stub"}]

# ------------------------------------------------------------------ fixtures
_NICHE_CLAIMS = [
    {"text": "Buyers in a single industry community are the most common adoption path: "
             "68% of surveyed SMBs adopted their last tool via a community recommendation.",
     "source_ids": ["src_seed_smb_survey_2026"], "confidence_category": "supported"},
    {"text": "Narrow segment-first launches cut early acquisition cost: median CAC was "
             "2.4x lower than broad self-serve launches in the first two quarters.",
     "source_ids": ["src_seed_cac_benchmarks_2026"], "confidence_category": "supported"},
    {"text": "Narrow launches retained users better: 90-day retention was 41% versus "
             "23% for broad launches across 27 observed products.",
     "source_ids": ["src_seed_retention_note_2026"], "confidence_category": "supported"},
]
_PARTNERSHIP_CLAIMS = [
    {"text": "Partnership-led launches reached distribution 31% faster on average "
             "than direct launches.",
     "source_ids": ["src_seed_partnership_report_2025"], "confidence_category": "supported"},
    {"text": "Channel partners typically take 15-25% of first-year revenue and "
             "negotiations take a median of 11 weeks.",
     "source_ids": ["src_seed_partnership_report_2025"], "confidence_category": "supported"},
    {"text": "Partnerships alone can replace a sales team entirely.",
     "source_ids": ["src_seed_founder_opinion_2026"], "confidence_category": "contested"},
]


def _library_ids(user: str) -> list[str]:
    return re.findall(r"- source_id: (\S+)", user)


def _objective(user: str) -> str:
    m = re.search(r"(?:Mission objective|Task): (.+)", user)
    return m.group(1).strip() if m else "the task"


def _strategy_payload(user: str) -> dict:
    obj = _objective(user)
    ids = _library_ids(user)
    partnership = "PARTNERSHIP-LED" in user
    seed_ids = {"src_seed_smb_survey_2026", "src_seed_cac_benchmarks_2026",
                "src_seed_retention_note_2026", "src_seed_partnership_report_2025",
                "src_seed_founder_opinion_2026"}
    if seed_ids & set(ids):
        claims = _PARTNERSHIP_CLAIMS if partnership else _NICHE_CLAIMS
        claims = [c for c in claims if set(c["source_ids"]) <= set(ids) | seed_ids]
    elif ids:
        claims = [{"text": f"Evidence item {i + 1} gathered for the objective, "
                           f"drawn from {sid}.",
                   "source_ids": [sid], "confidence_category": "supported"}
                  for i, sid in enumerate(ids[:3])]
    else:
        claims = [{"text": "No citable evidence was available for this claim.",
                   "source_ids": [], "confidence_category": "unsupported"}]
    if partnership:
        return {"title": "Candidate B — Partnership-led launch",
                "summary": f"Launch through established partners for: {obj}. Trade "
                           "first-year revenue share for immediate distribution into "
                           "existing customer bases.",
                "claims": claims,
                "assumptions": ["Partners with aligned customers will accept a "
                                "15-25% revenue share"],
                "risks": ["Partner dependency: roadmap and reputation sit partly "
                          "outside your control"],
                "estimated_cost_usd": 3400, "time_to_learn": "11-14 weeks"}
    return {"title": "Candidate A — Niche-first launch",
            "summary": f"Launch narrowly for: {obj}. Pick one industry community, "
                       "win it completely, then expand segment by segment with "
                       "retention proof.",
            "claims": claims,
            "assumptions": ["One reachable industry community exists with an "
                            "identifiable gathering place"],
            "risks": ["Segment may be too small to reach revenue targets"],
            "estimated_cost_usd": 1200, "time_to_learn": "4-6 weeks"}


def _topic(obj: str) -> str:
    m = re.search(r"(?:about|regarding|for|on)\s+(.+)$", obj, re.IGNORECASE)
    topic = (m.group(1) if m else obj).strip()
    return topic[:1].upper() + topic[1:] if topic else obj


def _deliverable(task_type: str, obj: str, user: str) -> str:
    topic = _topic(obj)
    low = obj.lower()
    if task_type == "writing":
        return (f"# {topic}\n\nDear Sir or Madam,\n\nI am writing about "
                f"{topic[0].lower() + topic[1:] if topic else 'the matter at hand'}. "
                "It needs attention, and I would like to agree on a clear next step "
                "with you.\n\nIn short: here is the situation, here is what I am "
                "asking for, and here is when I hope we can resolve it.\n\nThank you "
                "for your time. A short reply confirming you have received this "
                "would be much appreciated.\n\nKind regards")
    if task_type == "code":
        if "duplicate" in low:
            code = ('def remove_duplicate_words(sentence: str) -> str:\n'
                    '    """Return the sentence with duplicate words removed,\n'
                    '    keeping first occurrences and original order."""\n'
                    '    seen = set()\n'
                    '    kept = []\n'
                    '    for word in sentence.split():\n'
                    '        key = word.lower().strip(".,!?;:")\n'
                    '        if key not in seen:\n'
                    '            seen.add(key)\n'
                    '            kept.append(word)\n'
                    '    return " ".join(kept)')
            explain = "Case-insensitive de-duplication that preserves order."
        elif "fibonacci" in low:
            code = ('def fibonacci(n: int):\n'
                    '    """Yield the first n Fibonacci numbers."""\n'
                    '    a, b = 0, 1\n'
                    '    for _ in range(n):\n'
                    '        yield a\n'
                    '        a, b = b, a + b')
            explain = "A lazy generator — constant memory regardless of n."
        elif "count" in low and "word" in low:
            code = ('def word_frequencies(text: str) -> dict:\n'
                    '    """Count how often each word appears in the text."""\n'
                    '    counts = {}\n'
                    '    for word in text.lower().split():\n'
                    '        word = word.strip(".,!?;:")\n'
                    '        if word:\n'
                    '            counts[word] = counts.get(word, 0) + 1\n'
                    '    return counts')
            explain = "Normalises case and strips surrounding punctuation."
        else:
            code = ('def solve_task(data):\n'
                    '    """Implements the requested behaviour."""\n'
                    '    if data is None:\n'
                    '        raise ValueError("input is required")\n'
                    '    return data')
            explain = "A structured implementation with validation in place."
        return f"# {obj}\n\n```python\n{code}\n```\n\n**How it works:** {explain}"
    if task_type == "plan":
        m = re.search(r"(\d+)\s*-?\s*day", obj, re.IGNORECASE)
        days = int(m.group(1)) if m else 7
        per = max(1, days // 3)
        return (f"# Plan — {obj}\n\nA {days}-day plan in three phases.\n\n"
                f"## Phase 1 — Foundations (days 1–{per})\n"
                "- Gather materials and set up your workspace\n"
                "- Cover the core concepts end-to-end, lightly\n\n"
                f"## Phase 2 — Practice (days {per + 1}–{2 * per})\n"
                "- Work the weak-spot list, hardest first\n\n"
                f"## Phase 3 — Consolidation (days {2 * per + 1}–{days})\n"
                "- One full end-to-end run-through\n- Final light review")
    if task_type == "research":
        return (f"# Research report — {obj}\n\n## Executive summary\nFindings are "
                "drawn from the consulted sources and weighed by source quality.\n\n"
                "## Open questions\n- Which finding would change the decision if "
                "it were wrong?")
    if task_type == "qa":
        return (f"# {obj}\n\n**Short answer**\nBased on the material consulted, "
                "the direct answer is given here, with the evidence trail kept "
                "in the claims.\n\n## Bottom line\nStart from the short answer.")
    return (f"# {obj}\n\n## Understanding\nThe task as briefed: {obj}.\n\n"
            "## Result\nA complete working-through of the task, with assumptions "
            "stated rather than hidden.")


def _general_payload(system: str, user: str) -> dict:
    m = re.search(r"completing a (\w+) task", system)
    task_type = m.group(1) if m else "general"
    obj = _objective(user)
    ids = _library_ids(user)
    claims = [{"text": "The deliverable addresses the brief as written.",
               "source_ids": ids[:1], "confidence_category": "supported"}] if ids else \
             [{"text": "Produced from the brief alone; no external evidence consulted.",
               "source_ids": [], "confidence_category": "unsupported"}]
    return {"title": f"Draft — {task_type}",
            "summary": f"A completion of the task: {obj}.",
            "deliverable": _deliverable(task_type, obj, user),
            "claims": claims,
            "assumptions": ["The brief as written is the whole requirement"],
            "risks": ["Verify names, dates and figures before real-world use"],
            "estimated_cost_usd": 400, "time_to_learn": "Ready to review"}


def _critic_payload(user: str) -> dict:
    critiques = []
    blocks = re.split(r"\n(?=Candidate )", user)
    for block in blocks:
        head = re.match(r"Candidate (\S+) —", block)
        if not head:
            continue
        cid = head.group(1)
        claims = re.findall(r"- \[(.*?)\] (.*?) \(sources: (.*?)\)", block)
        if not claims:
            continue
        target = None
        for cat, text, srcs in claims:
            if cat == "contested":
                target = (cat, text, srcs, "high",
                          "Claim is contested and rests on a single source; it "
                          "cannot carry the strategy until the contest is resolved.")
                break
        if target is None:
            for cat, text, srcs in claims:
                if srcs.strip() in ("", "none"):
                    target = (cat, text, srcs, "high",
                              "Claim has no attached source at all.")
                    break
        if target is None:
            cat, text, srcs = claims[0]
            n = 0 if srcs.strip() in ("", "none") else len(srcs.split(","))
            target = (cat, text, srcs, "low" if n >= 2 else "medium",
                      "Claim is supported by a single source; independent "
                      "corroboration would raise confidence." if n == 1 else
                      "Claim is corroborated; residual risk is interpretation.")
        cat, text, srcs, severity, issue = target
        critiques.append({
            "target_candidate_id": cid, "target_claim": text, "issue": issue,
            "evidence_or_test": f"Source check: sources_attached="
                                f"{0 if srcs.strip() in ('', 'none') else len(srcs.split(','))}, "
                                f"confidence={cat}",
            "severity": severity,
            "suggested_correction": "Support the claim with primary data or "
                                    "downgrade it to an assumption.",
            "confidence": "high"})
    return {"critiques": critiques}


def respond(system: str, user: str) -> dict:
    """The stub model's brain: one deterministic payload per prompt shape."""
    if "adversarial critic" in system:
        return _critic_payload(user)
    if "deliverable (string" in user:
        return _general_payload(system, user)
    return _strategy_payload(user)


def _completion(payload: dict) -> dict:
    return {"id": "chatcmpl-stub", "model": STUB_MODEL,
            "choices": [{"message": {"role": "assistant",
                                     "content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


# ------------------------------------------------------------------ transports
def transport() -> httpx.MockTransport:
    """httpx MockTransport for adapter-level unit tests."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": MODELS})
        body = json.loads(request.content or b"{}")
        messages = body.get("messages", [])
        system = messages[0]["content"] if messages else ""
        user = messages[1]["content"] if len(messages) > 1 else ""
        return httpx.Response(200, json=_completion(respond(system, user)))
    return httpx.MockTransport(handler)


_server: ThreadingHTTPServer | None = None
_base_url: str | None = None


def start() -> str:
    """Boot the stub HTTP server (once per test process); returns base URL."""
    global _server, _base_url
    if _server is not None:
        return _base_url  # type: ignore[return-value]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _send(self, code: int, obj: dict):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.endswith("/models"):
                return self._send(200, {"data": MODELS})
            return self._send(404, {"error": "nope"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            messages = payload.get("messages", [])
            system = messages[0]["content"] if messages else ""
            user = messages[1]["content"] if len(messages) > 1 else ""
            return self._send(200, _completion(respond(system, user)))

    _server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    _base_url = f"http://127.0.0.1:{_server.server_address[1]}/v1"
    return _base_url


def base_url() -> str:
    return start()


def settings(**over) -> Settings:
    """Production Settings pointed at the stub via the ordinary
    env-endpoint seam (explicit local endpoint, $0 prices, seed
    research — the suite stays hermetic and deterministic)."""
    base = dict(llm_base_url=start(), llm_api_key="stub-key",
                llm_model=STUB_MODEL, llm_base_url_explicit=True,
                llm_api_key_explicit=True, llm_price_in_per_m=0.0,
                llm_price_out_per_m=0.0, research_mode="seed")
    base.update(over)
    return Settings(**base)


def orchestrator(store, vault=None):
    """An Orchestrator wired to the stub model."""
    from app.orchestrator import Orchestrator
    return Orchestrator(store, settings=settings(), vault=vault)
