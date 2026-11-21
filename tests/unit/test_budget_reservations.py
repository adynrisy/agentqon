"""V3: budget reservations are race-safe (Master Build §114/§120).
Concurrent workers must never reserve past the mission cap."""
import tempfile
import threading

from app.store import Store


def _mission(store: Store, tenant="org_r", cap=1.0) -> str:
    m = store.create_mission(tenant, "u", "race test",
                             {"objective": "x", "deliverables": ["d"], "acceptance": ["a"],
                              "constraints": {"max_cost_usd": cap}}, cap)
    return m["id"]


def test_reserve_settle_release_cycle():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s)
        rid = s.reserve_budget("org_r", mid, 0.40, 1.0)
        assert rid is not None
        assert s.reserve_budget("org_r", mid, 0.70, 1.0) is None  # would exceed
        s.settle_reservation(rid, "released")
        assert s.reserve_budget("org_r", mid, 0.70, 1.0) is not None  # room again
        mission = s.get_mission("org_r", mid)
        assert abs(mission["reserved_usd"] - 0.70) < 1e-9


def test_concurrent_reservations_never_exceed_cap():
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(f"{tmp}/t.db", f"{tmp}/a")
        mid = _mission(s, cap=1.0)
        results: list[str | None] = []

        def worker():
            results.append(s.reserve_budget("org_r", mid, 0.30, 1.0))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        won = [r for r in results if r is not None]
        assert len(won) == 3  # 3 x 0.30 <= 1.0; a 4th would breach the cap
        mission = s.get_mission("org_r", mid)
        assert mission["reserved_usd"] <= 1.0 + 1e-9
