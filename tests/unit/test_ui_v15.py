"""Chat UI wiring for v15 (static check — the repo has no browser
test harness; this pins the Proof Pack affordance so a UI refactor
cannot silently drop it)."""
from pathlib import Path

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def test_proof_pack_button_wired_in_chat():
    html = INDEX.read_text(encoding="utf-8")
    # v20.3 UI redesign: emoji removed from UI chrome by owner directive;
    # the pin is the wired "Proof Pack" affordance itself (label + handler).
    assert "Proof Pack" in html and "🧾" not in html
    assert "chatProofPack" in html
    assert "/proof-pack" in html
    # sits in the completed-mission actions, next to Simulate
    assert "chatSimulate" in html
