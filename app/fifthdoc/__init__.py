"""AgentQon deep platform (gap-fix workstream W10, fifth document).

Engine for the five product subsystems, implemented on the eight reusable
systems from the fourth document. W9's eight systems are NOT merged in this
tree, so this module defines the seam as plain, typed records: every
feature below records exactly one subsystem and one implementation system
(see FEATURE_MAP). When W9 lands, these records map 1:1 onto its systems;
until then, end-to-end wiring through W9 is NOT_TESTED and is stated as
such in the W10 report — never claimed.

Subsystems: Workflow Engine, Trust and Policy Engine, Testing and
Reproducibility Lab, Operations Center, Extension and Integration Platform.
Systems: Action, Artifact, Event, Permission, Recovery, Verification,
Usage Accounting, Project Context.
"""
from __future__ import annotations

SUBSYSTEMS = (
    "Workflow Engine",
    "Trust and Policy Engine",
    "Testing and Reproducibility Lab",
    "Operations Center",
    "Extension and Integration Platform",
)

SYSTEMS = (
    "Action", "Artifact", "Event", "Permission", "Recovery", "Verification",
    "Usage Accounting", "Project Context",
)

# section -> (subsystem, implementation system, seam note)
FEATURE_MAP = {
    "s01_undo": ("Operations Center", "Recovery",
                 "rides W8 file-ops backups/rollback when merged; standalone snapshots here"),
    "s02_inbox": ("Operations Center", "Event",
                  "reads mission/approval events; dismiss never cancels a mission"),
    "s03_hitl": ("Trust and Policy Engine", "Permission",
                 "plan gate is distinct from mission approval; consumes W3 verification"),
    "s04_workflows": ("Workflow Engine", "Action",
                      "builds on planner.build_plan + assert_acyclic, schedules, approvals"),
    "s05_transform": ("Workflow Engine", "Artifact",
                      "built-in utilities, never agent scripts; bounded-chunk streaming"),
    "s06_browser": ("Extension and Integration Platform", "Permission",
                    "NEW permissioned surface; detection only for CAPTCHA/access denial"),
    "s07_api_lab": ("Testing and Reproducibility Lab", "Verification",
                    "permission required before any request that changes real data"),
    "s08_inspector": ("Operations Center", "Event",
                      "one unified event stream; every event links task+agent+artifact"),
    "s09_model_lab": ("Testing and Reproducibility Lab", "Verification",
                      "fixed test set; no auto best-model claim"),
    "s10_policy_tests": ("Trust and Policy Engine", "Verification",
                         "user-runnable agent tests; one success never proves always-safe"),
    "s11_env_inspector": ("Operations Center", "Project Context",
                          "builds on CodeGraph symbol index + workspace fingerprint"),
    "s12_output_quality": ("Testing and Reproducibility Lab", "Verification",
                           "per-format rules only, never one generic check"),
    "s13_watchers": ("Workflow Engine", "Event",
                     "loop prevention mandatory: own output never retriggers endlessly"),
    "s14_queue": ("Operations Center", "Usage Accounting",
                  "positions/times are estimates unless reliably known"),
    "s15_disagreement": ("Workflow Engine", "Verification",
                         "builds on critic/evaluator/verdict seed; majority never decides alone"),
    "s16_replay": ("Testing and Reproducibility Lab", "Recovery",
                   "defaults to safe inspection/test mode after external actions"),
    "s17_supply_chain": ("Trust and Policy Engine", "Verification",
                         "CVE scan BLOCKED: no scanner pinned/available via stdlib (see module)"),
    "s18_release": ("Operations Center", "Permission",
                    "deployment is explicit permissioned action, never silent"),
    "s19_personalization": ("Operations Center", "Project Context",
                            "precedence: system < workspace < project < user"),
    "s20_extensions": ("Extension and Integration Platform", "Permission",
                       "builds on W7 plugin framework; no unrestricted access by default"),
    "s21_tiny": ("Operations Center", "Action",
                 "small utilities; confirmation memory never overrides security policy"),
}
