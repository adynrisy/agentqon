"""General-adapter deliverable recovery (audit B, 2026-10-12).

Reproduced live against qwen2.5:1.5b: a code mission's first panelist
(the AI Data Remediation Engineer persona) answered in its own schema
— {"transformation": "lambda x: x[::-1]", "confidence_score": 0.99} —
with no 'deliverable' key. _invoke_general raised "no deliverable" and
one panelist's schema deviation killed the whole mission, on every
attempt, while other task types completed. The adapter now recovers
the work product from the model's own substantive string field and
records a warning; it still raises when there is no content at all.
"""
import asyncio

import pytest

from app.adapters import AgentRequest
from app.config import Settings
from app.llm_adapter import LLMResearchAdapter


class _FakeGateway:
    base_url = "http://127.0.0.1:9/v1"

    def __init__(self, data):
        self._data = data

    async def chat_json(self, model, system, user):
        return self._data, {"input_tokens": 3, "output_tokens": 2,
                            "model": model}


def _adapter(data):
    return LLMResearchAdapter(Settings(), client=_FakeGateway(data))


def _request():
    return AgentRequest(mission_id="m1", task_id="t1",
                        objective="Write a Python function that reverses a string",
                        deliverables=["deliverable"], output_schema={},
                        task_type="code")


_MANIFEST = {"agent_id": "agency.test", "version": "1.0.0", "strategy": "direct"}


def test_persona_schema_answer_is_recovered_as_deliverable():
    adapter = _adapter({"transformation": "lambda x: x[::-1]",
                        "confidence_score": 0.99})
    resp = asyncio.run(adapter.invoke(_request(), dict(_MANIFEST)))
    assert resp.output["deliverable"] == "lambda x: x[::-1]"
    assert any("recovered" in w and "transformation" in w
               for w in resp.warnings)


def test_normal_deliverable_unchanged_and_no_warning():
    adapter = _adapter({"title": "T", "summary": "S",
                        "deliverable": "def rev(s): return s[::-1]",
                        "claims": [], "assumptions": [], "risks": []})
    resp = asyncio.run(adapter.invoke(_request(), dict(_MANIFEST)))
    assert resp.output["deliverable"] == "def rev(s): return s[::-1]"
    assert not any("recovered" in w for w in resp.warnings)


def test_empty_output_still_raises():
    adapter = _adapter({"confidence_score": 0.5})
    with pytest.raises(RuntimeError, match="no deliverable"):
        asyncio.run(adapter.invoke(_request(), dict(_MANIFEST)))
