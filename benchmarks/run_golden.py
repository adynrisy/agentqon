"""OmniMind AI — Phase 6 evaluation runner.

Usage:
    .venv/bin/python -m benchmarks.run_golden [--suite golden|router|critic|all]
                                              [--live] [--live-limit N]
                                              [--out benchmarks/golden_results.json]

The golden suite runs the frozen 25-mission set through the full HTTP
application in-process. Default mode is the deterministic seed corpus
(the CI gate); --live runs the same expectations against real web
research (advisory report — the public web is not a frozen fixture).
Release gates (EVALUATION_PLAN): golden cases all pass, router
invariants hold, critic Cohen's kappa >= 0.6.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_client(live: bool):
    os.environ.setdefault("OMNIMIND_DATA",
                          tempfile.mkdtemp(prefix="omnimind-golden-"))
    if not live:
        os.environ["OMNIMIND_RESEARCH_MODE"] = "seed"
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def run_golden_suite(live: bool, live_limit: int | None) -> dict:
    from benchmarks.harness import case_metrics, check_case, load_json

    data = load_json("golden_set.json")
    cases = data["cases"]
    if live and live_limit:
        keep = [c for c in cases if c["expect"]["research_live"]][:live_limit]
        keep += [c for c in cases if not c["expect"]["research_live"]]
        cases = keep
    client = _make_client(live)
    results = []
    for i, case in enumerate(cases):
        tenant = f"bench_{case['id']}"
        headers = {"X-Tenant-Id": tenant, "Idempotency-Key": f"golden-{case['id']}"}
        started = time.time()
        created = client.post("/v1/missions", headers=headers,
                              json={"title": case["title"], "contract": case["contract"]})
        mid = created.json()["mission_id"]
        ran = client.post(f"/v1/missions/{mid}/run", headers={"X-Tenant-Id": tenant})
        detail = ran.json()
        library = client.get("/v1/sources", headers={"X-Tenant-Id": tenant}).json()
        library_ids = {s["source_id"] for s in library["sources"]}
        m = case_metrics(detail, library_ids)
        fails = check_case(case, m, live_mode=live)
        results.append({"case": case["id"], "title": case["title"],
                        "seconds": round(time.time() - started, 2),
                        "metrics": m, "failures": fails, "passed": not fails})
        print(f"  [{i + 1}/{len(cases)}] {case['id']} "
              f"{'PASS' if not fails else 'FAIL: ' + '; '.join(fails)}")
    passed = sum(1 for r in results if r["passed"])
    return {"suite": "golden", "mode": "live" if live else "seed",
            "cases": results, "passed": passed, "total": len(results),
            "all_passed": passed == len(results)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all",
                    choices=["golden", "router", "critic", "all"])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--live-limit", type=int, default=3)
    ap.add_argument("--out", default="benchmarks/golden_results.json")
    args = ap.parse_args()

    from benchmarks.harness import router_invariants, run_router_matrix

    report: dict = {"harness": "phase6-golden-v1"}
    gates_ok = True

    if args.suite in ("critic", "all"):
        print("== critic calibration ==")
        print("  removed in v20: the deterministic critic no longer exists;")
        print("  critic quality is a property of the connected live model.")
        report["critic"] = {"status": "removed_in_v20"}

    if args.suite in ("router", "all"):
        print("== router quality matrix ==")
        matrix = run_router_matrix()
        inv = router_invariants(matrix)
        report["router"] = {**matrix, "invariant_failures": inv}
        for row in matrix["rows"]:
            print(f"  {row['scenario']:<14} {row['mode']:<24} -> "
                  f"{row['model'] or row['outcome']}")
        if inv:
            gates_ok = False
            print("  GATE FAIL:", inv)

    if args.suite in ("golden", "all"):
        print(f"== golden suite ({'live' if args.live else 'seed'}) ==")
        golden = run_golden_suite(args.live, args.live_limit if args.live else None)
        report["golden"] = golden
        print(f"  {golden['passed']}/{golden['total']} cases passed")
        if not args.live and not golden["all_passed"]:
            gates_ok = False
            print("  GATE FAIL: golden suite (seed mode is the release gate)")

    Path(args.out).write_text(json.dumps(report, indent=1))
    print(f"report written to {args.out}; gates: {'OK' if gates_ok else 'FAILED'}")
    return 0 if gates_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
