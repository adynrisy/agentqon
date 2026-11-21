"""AgentQon — mission planner (Ch.8).

Converts a validated mission contract into a small, bounded DAG:
research (two independent agents in parallel) -> critique -> synthesis
and evaluation. The plan version is persisted before scheduling, cycles
are rejected, and 20% of the budget is reserved for final evaluation so
early agents cannot consume the whole budget (Ch.15).
"""
from __future__ import annotations

from .domain import Budget, TaskContract, new_id

PLAN_VERSION = "plan-v1"
EVALUATION_RESERVE_FRACTION = 0.20


def build_plan(mission_id: str, contract: dict) -> tuple[list[TaskContract], float]:
    max_cost = float(contract["constraints"]["max_cost_usd"])
    usable = max_cost * (1.0 - EVALUATION_RESERVE_FRACTION)
    reserve = round(max_cost * EVALUATION_RESERVE_FRACTION, 4)

    t_research = new_id("tsk")
    t_critique = new_id("tsk")
    t_synthesis = new_id("tsk")

    tasks = [
        TaskContract(
            task_id=t_research, mission_id=mission_id, kind="research",
            description="Independent first-pass research by two agents with distinct strategies",
            dependencies=[], required_capabilities=["research.web"],
            output_schema_version="candidate-v1",
            acceptance_tests=["has_claims", "claims_have_sources", "schema_valid"],
            permission_scope=["web.read", "artifact.write"],
            budget_limit=Budget(amount=round(usable * 0.55, 4)),
        ),
        TaskContract(
            task_id=t_critique, mission_id=mission_id, kind="critique",
            description="One targeted, structured critique round against the candidates' claims",
            dependencies=[t_research], required_capabilities=["critique.review"],
            output_schema_version="critique-v1",
            acceptance_tests=["critique_targets_claim", "critique_has_evidence_or_test"],
            permission_scope=["artifact.read"],
            budget_limit=Budget(amount=round(usable * 0.20, 4)),
        ),
        TaskContract(
            task_id=t_synthesis, mission_id=mission_id, kind="synthesis",
            description="Validate schemas and citations, evaluate candidates, assemble the comparison",
            dependencies=[t_critique], required_capabilities=["research.web"],
            output_schema_version="comparison-v1",
            acceptance_tests=["two_distinct_candidates", "evaluation_record_present"],
            permission_scope=["artifact.read", "artifact.write"],
            budget_limit=Budget(amount=round(usable * 0.25, 4)),
        ),
    ]
    assert_acyclic(tasks)
    return tasks, reserve


def assert_acyclic(tasks: list[TaskContract]) -> None:
    deps = {t.task_id: list(t.dependencies) for t in tasks}
    visiting, visited = set(), set()

    def visit(node: str, path: list[str]) -> None:
        if node in visiting:
            raise ValueError(f"Cycle detected in task graph: {' -> '.join(path + [node])}")
        if node in visited:
            return
        visiting.add(node)
        for d in deps.get(node, []):
            visit(d, path + [node])
        visiting.discard(node)
        visited.add(node)

    for t in tasks:
        visit(t.task_id, [])
