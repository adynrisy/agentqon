"""§4 Workflow builder — visual-editor model over the real mission DAG.

Node kinds: start, action, condition, loop, finish, approval, retry,
parallel (branch), join, subworkflow. Every step declares inputs, outputs,
failure behaviour, permissions and idempotency — a step missing any of
these is rejected at validation time, not at run time.

Validation reuses the DAG discipline of app/planner.py (acyclic graphs):
cycles are rejected, unreachable nodes are flagged, and iteration /
duration limits bound loops. Execution here is a deterministic dry-run /
trace engine over declared steps; live step execution rides the mission
runner (NOT_TESTED end-to-end until that wiring lands).
"""
from __future__ import annotations

from dataclasses import dataclass, field

NODE_KINDS = ("start", "action", "condition", "loop", "finish", "approval",
              "retry", "parallel", "join", "subworkflow")

REQUIRED_STEP_FIELDS = ("inputs", "outputs", "on_failure", "permissions",
                        "idempotent")


@dataclass
class Node:
    node_id: str
    kind: str
    label: str = ""
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    on_failure: str = "stop"  # stop | retry | skip
    permissions: list[str] = field(default_factory=list)
    idempotent: bool = False
    config: dict = field(default_factory=dict)
    next_nodes: list[str] = field(default_factory=list)


@dataclass
class Workflow:
    workflow_id: str
    nodes: dict[str, Node] = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    version: int = 1
    history: list[dict] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        if node.kind not in NODE_KINDS:
            raise ValueError(f"Unknown node kind: {node.kind}")
        self.nodes[node.node_id] = node

    def validate(self) -> dict:
        errors, warnings = [], []
        starts = [n for n in self.nodes.values() if n.kind == "start"]
        finishes = [n for n in self.nodes.values() if n.kind == "finish"]
        if len(starts) != 1:
            errors.append(f"Workflow needs exactly one start node, found {len(starts)}.")
        if not finishes:
            errors.append("Workflow needs at least one finish node.")
        for node in self.nodes.values():
            for fname in REQUIRED_STEP_FIELDS:
                value = getattr(node, fname)
                if fname == "on_failure" and value not in ("stop", "retry", "skip"):
                    errors.append(f"Node {node.node_id}: unknown failure behaviour.")
                elif fname in ("inputs", "outputs") and not isinstance(value, dict):
                    errors.append(f"Node {node.node_id}: {fname} must be a mapping.")
                elif fname == "permissions" and not isinstance(value, list):
                    errors.append(f"Node {node.node_id}: permissions must be a list.")
            for nxt in node.next_nodes:
                if nxt not in self.nodes:
                    errors.append(f"Node {node.node_id} points to missing node {nxt}.")
        # Cycle detection (same discipline as planner.assert_acyclic),
        # iterative: a deep graph used to recurse in visit() until the
        # stack ran out (RecursionError -> bare 500 on dry-run). Edges
        # to missing nodes are skipped here — they are already reported
        # as "points to missing node" errors above, and following them
        # used to raise KeyError (global 404 handler) instead.
        color: dict[str, str] = {}
        for root in self.nodes:
            if color.get(root) == "black":
                continue
            color[root] = "gray"
            stack: list[tuple[str, object, list[str]]] = [
                (root, iter(self.nodes[root].next_nodes), [root])]
            while stack:
                nid, it, path = stack[-1]
                pushed = False
                for nxt in it:  # type: ignore[union-attr]
                    if nxt not in self.nodes:
                        continue
                    if color.get(nxt) == "gray":
                        errors.append(
                            f"Cycle detected: {' -> '.join(path + [nxt])}")
                    elif color.get(nxt) != "black":
                        color[nxt] = "gray"
                        stack.append(
                            (nxt, iter(self.nodes[nxt].next_nodes),
                             path + [nxt]))
                        pushed = True
                        break
                if not pushed:
                    color[nid] = "black"
                    stack.pop()
        # Reachability from start.
        if starts:
            reachable = set()
            stack = [starts[0].node_id]
            while stack:
                cur = stack.pop()
                if cur in reachable or cur not in self.nodes:
                    continue
                reachable.add(cur)
                stack.extend(self.nodes[cur].next_nodes)
            for nid in self.nodes:
                if nid not in reachable:
                    warnings.append(f"Node {nid} is unreachable from start.")
        # Loop limits.
        for node in self.nodes.values():
            if node.kind == "loop":
                limit = node.config.get("max_iterations")
                if limit is None or limit <= 0:
                    errors.append(f"Loop node {node.node_id} needs a positive "
                                  "max_iterations limit.")
                if node.config.get("max_duration_seconds", 0) <= 0:
                    warnings.append(f"Loop node {node.node_id} has no duration limit.")
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def dry_run(self, inputs: dict | None = None) -> dict:
        """Walk the graph without executing side effects; record the trace."""
        validation = self.validate()
        if not validation["ok"]:
            return {"ok": False, "errors": validation["errors"], "trace": []}
        starts = [n for n in self.nodes.values() if n.kind == "start"]
        # RETEST-3 fix: defense in depth — dry_run must never IndexError
        # on starts[0], even if it is ever called on a workflow whose
        # validation was bypassed. A graph with no start node is a
        # plain error result, not a 500.
        if not starts:
            return {"ok": False,
                    "errors": ["Workflow needs exactly one start node, "
                               "found 0."],
                    "trace": []}
        trace, seen = [], {}
        current = starts[0].node_id
        steps = 0
        while current and steps < 1000:
            node = self.nodes[current]
            trace.append({"node_id": current, "kind": node.kind,
                          "inputs": dict(node.inputs), "outputs": dict(node.outputs),
                          "would_execute": node.kind not in ("start", "finish")})
            steps += 1
            if node.kind == "finish":
                break
            if node.kind == "loop":
                seen[current] = seen.get(current, 0) + 1
                if seen[current] >= node.config.get("max_iterations", 1):
                    # Loop exhausted: follow the exit edge if declared.
                    nxt = [n for n in node.next_nodes
                           if n != current]
                    current = nxt[0] if nxt else None
                    continue
            current = node.next_nodes[0] if node.next_nodes else None
        return {"ok": True, "trace": trace, "steps": steps,
                "inputs_used": inputs or {}}

    def export(self) -> dict:
        return {"workflow_id": self.workflow_id, "version": self.version,
                "variables": self.variables,
                "nodes": [{**{k: getattr(n, k) for k in
                              ("node_id", "kind", "label", "inputs", "outputs",
                               "on_failure", "permissions", "idempotent",
                               "config", "next_nodes")}}
                          for n in self.nodes.values()]}

    @staticmethod
    def import_(data: dict) -> "Workflow":
        wf = Workflow(workflow_id=data["workflow_id"],
                      variables=data.get("variables", {}),
                      version=data.get("version", 1))
        for raw in data["nodes"]:
            wf.add_node(Node(**{k: raw[k] for k in
                                ("node_id", "kind", "label", "inputs", "outputs",
                                 "on_failure", "permissions", "idempotent",
                                 "config", "next_nodes")}))
        return wf

    def save_version(self) -> int:
        self.history.append({"version": self.version, "snapshot": self.export()})
        self.version += 1
        return self.version - 1
