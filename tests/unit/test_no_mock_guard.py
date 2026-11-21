"""v20 regression guard: canned generation must never come back.

Walks the production package (app/) and fails if any module name or
import references a mock — and asserts the production Settings carry
no flag that could enable a fake engine. The suite's one deterministic
stub lives under tests/ only (tests/stub_llm.py) and is reachable
solely by pointing the ordinary env-endpoint configuration at it from
test code; production config has no switch for it.
"""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _offenders():
    bad = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "mock" in path.stem.lower():
            bad.append(f"{path.name}: module name references mock")
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            else:
                continue
            for name in names:
                if "mock" in name.lower():
                    bad.append(f"{path.name}: import references {name}")
    return bad


def test_no_mock_modules_or_imports_in_app_package():
    assert _offenders() == []


def test_no_mock_enabling_setting_exists():
    from app.config import Settings
    fields = set(Settings.__dataclass_fields__)
    assert "agent_mode" not in fields
    assert not any("mock" in f or "stub" in f or "fake" in f for f in fields)
