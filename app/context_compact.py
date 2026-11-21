"""AgentQon — context-compaction utilities (W1, audit §2).

Shared utilities, not independent features. Every function reports
what it removed and marks partial output as partial — compaction
never silently changes meaning.

GOVERNING METRIC: total cost per successfully completed task. These
utilities save tokens only when they do not cause rework: nothing here
drops a section that carries unique information without saying so, and
the regression tests prove a cut that causes failure reads as worse
(see cost_engine.cost_per_success).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from datetime import datetime

from .cost_engine import estimate_tokens

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = frozenset(
    "a an the and or of to in on for with is are was were be been it its "
    "this that these those by at from as".split())


# 21 — repeated-sentence detection
def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def detect_repeated_sentences(text: str) -> list[dict]:
    """Sentences appearing more than once, with counts."""
    counts: dict[str, int] = {}
    for s in split_sentences(text):
        counts[s] = counts.get(s, 0) + 1
    return [{"sentence": s, "count": c} for s, c in counts.items() if c > 1]


def remove_repeated_sentences(text: str) -> dict:
    """Keep the first occurrence of each sentence; report removals."""
    seen, kept, removed = set(), [], 0
    for s in split_sentences(text):
        if s in seen:
            removed += 1
            continue
        seen.add(s)
        kept.append(s)
    return {"text": " ".join(kept), "removed_sentences": removed}


# 22 — duplicate tool-result references
def dedup_tool_results(results: list[dict]) -> dict:
    """Tool results that repeat an earlier result (same tool + same
    content hash) collapse to a short reference to the first one."""
    seen, kept, removed = {}, [], 0
    for r in results:
        digest = hashlib.sha256(
            f"{r.get('tool', '')}|{r.get('content', '')}".encode()).hexdigest()
        if digest in seen:
            removed += 1
            kept.append({"tool": r.get("tool"), "reference_to": seen[digest],
                         "note": "Same result as an earlier tool call; see that result."})
        else:
            seen[digest] = r.get("id", f"result-{len(seen)}")
            kept.append(r)
    return {"results": kept, "duplicates_removed": removed}


# 23 — redundant metadata removal (values duplicated under another key,
# or empty metadata containers — never keys with unique information)
def strip_redundant_metadata(obj: dict) -> dict:
    seen_values: dict[str, str] = {}
    out, removed = {}, []
    for key in sorted(obj):
        value = obj[key]
        fingerprint = hashlib.sha256(repr(value).encode()).hexdigest()
        if isinstance(value, dict) and not value:
            removed.append(key)
            continue
        if fingerprint in seen_values and key not in ("id",):
            removed.append(key)
            continue
        seen_values[fingerprint] = key
        out[key] = value
    return {"data": out, "removed_keys": removed}


# 24 — irrelevant-history filtering by RELEVANCE, not recency
def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def relevance_score(query: str, text: str) -> float:
    """Keyword-overlap relevance in [0, 1]; 0 when nothing overlaps."""
    q, t = _keywords(query), _keywords(text)
    if not q:
        return 0.0
    return round(len(q & t) / len(q), 4)


def filter_history_by_relevance(turns: list[dict], query: str,
                                keep_top_n: int = 5) -> dict:
    """Keep the most relevant turns (plus the first turn, the anchor),
    drop the rest — regardless of how recent the dropped ones are."""
    if not turns:
        return {"turns": [], "dropped": 0}
    scored = [(relevance_score(query, t.get("content", "")), i, t)
              for i, t in enumerate(turns)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    keep_idx = {0} | {i for _, i, _ in scored[:keep_top_n]}
    kept = [t for i, t in enumerate(turns) if i in keep_idx]
    return {"turns": kept, "dropped": len(turns) - len(kept),
            "kept_scores": {i: s for s, i, _ in scored if i in keep_idx}}


# 25 — compact timestamp formatting (display only; the full value is
# never overwritten in the record, only in the compacted view)
def compact_timestamp(iso_value: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except (ValueError, AttributeError):
        return iso_value


# 26 — identifier aliasing with an unambiguous mapping
def alias_identifiers(text: str, identifiers: list[str]) -> dict:
    """Replace long or repeated identifiers with short aliases (A1,
    A2, ...). The mapping is returned alongside the text, aliases are
    checked not to already occur in the text, and the same identifier
    always maps to the same alias — never ambiguous."""
    mapping: dict[str, str] = {}
    out = text
    for ident in sorted(set(identifiers), key=len, reverse=True):
        if not ident or out.count(ident) == 0:
            continue
        if len(ident) <= 20 and out.count(ident) < 2:
            continue  # short, single-use identifiers are not worth aliasing
        alias = f"A{len(mapping) + 1}"
        while alias in out and alias not in mapping.values():
            alias = f"A{len(mapping) + 2}"
        # Never alias onto a token that already means something else.
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text):
            continue
        mapping[ident] = alias
        out = out.replace(ident, alias)
    return {"text": out, "mapping": mapping,
            "reverse_mapping": {v: k for k, v in mapping.items()}}


# 27 — structured-result compaction
def compact_structured(obj, *, max_list_items: int = 10,
                       max_string_chars: int = 500):
    """Truncate long lists/strings WITH a marker stating what was cut;
    drop empty fields. Partial output always says it is partial."""
    if isinstance(obj, dict):
        out, cut = {}, 0
        for k, v in obj.items():
            if v in (None, "", [], {}):
                cut += 1
                continue
            compacted, n = compact_structured(v, max_list_items=max_list_items,
                                              max_string_chars=max_string_chars)
            cut += n
            out[k] = compacted
        return out, cut
    if isinstance(obj, list):
        if len(obj) > max_list_items:
            head = [compact_structured(v, max_list_items=max_list_items,
                                       max_string_chars=max_string_chars)[0]
                    for v in obj[:max_list_items]]
            head.append(f"[{len(obj) - max_list_items} more items hidden: "
                        "output was shortened, not complete.]")
            return head, len(obj) - max_list_items
        return [compact_structured(v, max_list_items=max_list_items,
                                   max_string_chars=max_string_chars)[0]
                for v in obj], 0
    if isinstance(obj, str) and len(obj) > max_string_chars:
        return (obj[:max_string_chars]
                + f" [... shortened: {len(obj) - max_string_chars} more characters]"), 1
    return obj, 0


# 28 — repeated-error suppression
def suppress_repeated_errors(errors: list[str]) -> dict:
    """Identical errors collapse to one entry with a repeat count, so
    a looping failure does not flood the context. The count is kept —
    the repetition is summarized, not hidden."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for e in errors:
        if e not in counts:
            order.append(e)
        counts[e] = counts.get(e, 0) + 1
    lines = []
    for e in order:
        lines.append(e if counts[e] == 1 else
                     f"{e} [same problem repeated {counts[e]} times]")
    return {"errors": lines, "suppressed_repeats": sum(c - 1 for c in counts.values())}


# 29 — relevant-log extraction
def extract_relevant_logs(log_lines: list[str], keywords: list[str]) -> dict:
    wanted = [k.lower() for k in keywords]
    kept = [ln for ln in log_lines
            if any(k in ln.lower() for k in wanted)]
    return {"lines": kept, "kept": len(kept), "dropped": len(log_lines) - len(kept)}


# 30 — diff-based context
def diff_context(old_text: str, new_text: str) -> dict:
    """When context was already sent, send only what changed."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = [ln for ln in difflib.unified_diff(old_lines, new_lines, lineterm="")
            if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    unchanged = len(set(old_lines) & set(new_lines))
    return {"changed_lines": diff, "unchanged_lines_suppressed": unchanged,
            "note": "Only changed lines are included; unchanged lines were already sent."}


# 31 — file-range selection
def select_file_range(lines: list[str], start: int, end: int,
                      context_lines: int = 2) -> dict:
    """Return one range of a file (1-based, inclusive) plus a little
    context, with a header stating the range and the file's total so
    the reader knows this is a slice, not the whole file."""
    total = len(lines)
    lo = max(1, start - context_lines)
    hi = min(total, end + context_lines)
    return {"lines": lines[lo - 1:hi], "range": (lo, hi), "total_lines": total,
            "note": f"Showing lines {lo}-{hi} of {total}; this is a slice, not the whole file."}


# 32 — search-result dedup
def dedup_search_results(results: list[dict]) -> dict:
    seen, kept, removed = set(), [], 0
    for r in results:
        key = (r.get("url", "") or "").strip().lower().rstrip("/")
        if not key:
            key = (r.get("title", "") or "").strip().lower()
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(r)
    return {"results": kept, "duplicates_removed": removed}


# 35 — output-length budgeting per step
def budget_output_per_step(steps: list[str], total_budget_tokens: int) -> dict:
    """Split a total output budget across steps. Steps named as
    needing more (containing 'detail' or 'full') get a double share;
    every step gets at least 1 token of budget. Shares are explicit so
    a step that exceeds its share can be cut with a marker."""
    if not steps:
        return {}
    weights = [2 if ("detail" in s.lower() or "full" in s.lower()) else 1
               for s in steps]
    total_weight = sum(weights)
    budgets = {}
    remaining = total_budget_tokens
    for step, w in zip(steps, weights):
        share = max(1, total_budget_tokens * w // total_weight)
        budgets[step] = share
        remaining -= share
    if remaining and steps:
        budgets[steps[0]] += remaining
    return budgets


# 36 — unchanged-state suppression
def suppress_unchanged_state(previous, current) -> dict:
    """If state did not change, send nothing (suppressed=True) instead
    of re-sending the same state. A change is always sent in full."""
    if previous == current:
        return {"suppressed": True, "value": None,
                "note": "State unchanged since last time; nothing re-sent."}
    return {"suppressed": False, "value": current}


# 39 — repeated-prefix detection (prompt-caching opportunities)
def detect_repeated_prefix(prompts: list[str], min_chars: int = 100) -> dict:
    """The longest common prefix shared by at least two prompts, when
    it is long enough to be worth caching. This is the signal for the
    prompt-caching pass-through in cost_engine."""
    if len(prompts) < 2:
        return {"prefix": "", "prompts_affected": 0, "estimated_tokens": 0}
    # Longest prefix shared by at least TWO prompts (not necessarily all).
    best = ""
    for i in range(len(prompts)):
        for j in range(i + 1, len(prompts)):
            a, b = prompts[i], prompts[j]
            k = 0
            while k < min(len(a), len(b)) and a[k] == b[k]:
                k += 1
            if k > len(best):
                best = a[:k]
    prefix = best
    affected = sum(1 for p in prompts if p.startswith(prefix)) if len(prefix) >= min_chars else 0
    if affected < 2:
        return {"prefix": "", "prompts_affected": 0, "estimated_tokens": 0}
    return {"prefix": prefix, "prompts_affected": affected,
            "estimated_tokens": estimate_tokens(prefix),
            "note": "This shared opening can be cached once and reused."}


# 40 — context contribution measurement (top-15 #7)
def measure_context_contribution(sections: list[dict], query: str = "") -> dict:
    """Per-section contribution: estimated tokens, share of the total,
    and (when a query is given) relevance. The largest, least relevant
    section is the first candidate for trimming — measured, not guessed."""
    total = 0
    measured = []
    for s in sections:
        text = s.get("content", "")
        tokens = estimate_tokens(text)
        total += tokens
        measured.append({"name": s.get("name", ""), "estimated_tokens": tokens,
                         "relevance": (relevance_score(query, text) if query else None)})
    for m in measured:
        m["share_pct"] = round(m["estimated_tokens"] / total * 100, 2) if total else 0.0
    measured.sort(key=lambda m: (-m["estimated_tokens"]))
    largest = measured[0] if measured else None
    least_relevant = None
    if query and measured:
        least_relevant = min(measured, key=lambda m: (m["relevance"] if m["relevance"] is not None else 0))
    return {"sections": measured, "total_estimated_tokens": total,
            "largest_section": largest, "least_relevant_section": least_relevant}
