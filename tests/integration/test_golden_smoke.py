"""Phase 6 smoke: three golden cases end-to-end through the harness
metrics (the full 25-case run is benchmarks/run_golden.py)."""
from __future__ import annotations

from benchmarks.harness import case_metrics, check_case, load_json


def test_golden_smoke_three_cases(client):
    cases = load_json("golden_set.json")["cases"][:3]
    for i, case in enumerate(cases):
        tenant = f"org_goldsmoke{i}"
        headers = {"X-Tenant-Id": tenant, "Idempotency-Key": f"smoke-{case['id']}"}
        created = client.post("/v1/missions", headers=headers,
                              json={"title": case["title"], "contract": case["contract"]})
        mid = created.json()["mission_id"]
        detail = client.post(f"/v1/missions/{mid}/run",
                             headers={"X-Tenant-Id": tenant}).json()
        library = client.get("/v1/sources", headers={"X-Tenant-Id": tenant}).json()
        m = case_metrics(detail, {s["source_id"] for s in library["sources"]})
        assert check_case(case, m, live_mode=False) == [], (case["id"], m)
