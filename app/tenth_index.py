"""Index for the tenth audit document's 42 capabilities.

Subsystem key: WE=Workflow Engine, TPE=Trust and Policy Engine,
TRL=Testing and Reproducibility Lab, OC=Operations Center,
EIP=Extension and Integration Platform.
System key: ACT=Action, ART=Artifact, EVT=Event, PERM=Permission,
REC=Recovery, VER=Verification, ACCT=Usage Accounting, CTX=Project Context.
No new workstream was created; every item files into the existing grouping.
"""
from __future__ import annotations

ITEMS = [
    # (number, name, subsystem, system, module)
    (1, "Verifier Reliability Lab", "TRL", "VER", "tenth_meta"),
    (2, "Verification Oracle Generator", "TRL", "VER", "tenth_meta"),
    (3, "Validator Drift Monitor", "TRL", "VER", "tenth_meta"),
    (4, "Independent Verification Diversity Score", "TRL", "VER", "tenth_meta"),
    (5, "False-Confidence Detector", "TRL", "VER", "tenth_meta"),
    (6, "Just-in-Time Credential Broker", "TPE", "PERM", "tenth_identity"),
    (7, "Delegated Authorization Chain", "TPE", "PERM", "tenth_identity"),
    (8, "Authorization Context Binding", "TPE", "PERM", "tenth_identity"),
    (9, "Privilege Escalation Tripwire", "TPE", "PERM", "tenth_identity"),
    (10, "Credential Revocation Propagation", "TPE", "PERM", "tenth_identity"),
    (11, "Separation-of-Duties Rules", "TPE", "PERM", "tenth_identity"),
    (12, "Retry-Storm Prevention", "OC", "REC", "tenth_distributed"),
    (13, "Dependency Bulkheads", "OC", "ACT", "tenth_distributed"),
    (14, "Graceful Load Shedding", "OC", "ACT", "tenth_distributed"),
    (15, "Clock-Skew and Event-Ordering Monitor", "OC", "EVT", "tenth_distributed"),
    (16, "Streaming Backpressure Controller", "OC", "EVT", "tenth_distributed"),
    (17, "Distributed Work-Lease Recovery", "OC", "REC", "tenth_distributed"),
    (18, "Provider Failover Compatibility Gate", "OC", "ACT", "tenth_distributed"),
    (19, "Agent Message Contract Registry", "WE", "EVT", "tenth_comms"),
    (20, "Shared Vocabulary and Entity Resolution", "WE", "CTX", "tenth_comms"),
    (21, "Decision Dependency Tracker", "WE", "CTX", "tenth_comms"),
    (22, "Commitment Registry", "WE", "VER", "tenth_comms"),
    (23, "Communication Loss Detector", "WE", "EVT", "tenth_comms"),
    (24, "Agent Handoff Compression Test", "WE", "VER", "tenth_comms"),
    (25, "Content Rights and Attribution Ledger", "TPE", "ART", "tenth_governance"),
    (26, "Data Residency Enforcement", "TPE", "PERM", "tenth_governance"),
    (27, "Consent and Authorization Expiry", "TPE", "PERM", "tenth_governance"),
    (28, "Sensitive Output Release Gate", "TPE", "VER", "tenth_governance"),
    (29, "Cross-Boundary Data Transformation Audit", "TPE", "EVT", "tenth_governance"),
    (30, "Privacy-Preserving Aggregation", "OC", "ACCT", "tenth_governance"),
    (31, "Thermal-Aware Scheduling", "OC", "ACT", "tenth_local"),
    (32, "Energy-Aware Mission Routing", "OC", "ACCT", "tenth_local"),
    (33, "Model Warm-State Manager", "OC", "ACT", "tenth_local"),
    (34, "Hardware Contention Forecast", "OC", "ACCT", "tenth_local"),
    (35, "Local Model Integrity Verification", "TRL", "VER", "tenth_local"),
    (36, "Capability Overclaim Detector", "TRL", "VER", "tenth_local"),
    (37, "Prompt and Policy Semantic Diff", "TRL", "VER", "tenth_strategic"),
    (38, "Output Consumer Compatibility Test", "TRL", "VER", "tenth_strategic"),
    (39, "Unattended Mission Readiness Gate", "WE", "VER", "tenth_strategic"),
    (40, "Feature Interaction Tester", "TRL", "VER", "tenth_strategic"),
    (41, "Operational Debt Register", "OC", "CTX", "tenth_strategic"),
    (42, "Capability Sunset Manager", "EIP", "CTX", "tenth_strategic"),
]
