"""AgentQon — Phase 1 seed source library.

These sources are a curated *demonstration* library inside the product:
clearly labelled seed records with excerpts, publication dates and kinds,
so the evidence engine (claim ledger + citation checks) can be exercised
end to end. When live research is on (the default), retrieval through the
Tool Gateway's web.read tool supplies the corpus instead — the
claim/citation model does not change.
"""
from __future__ import annotations

from .domain import SourceRecord, now_iso

SEED_SOURCES: list[SourceRecord] = [
    SourceRecord(
        source_id="src_seed_smb_survey_2026", kind="primary",
        title="SMB Software Adoption Survey 2026 (seed library)",
        published_at="2026-03-14",
        retrieved_at=now_iso(),
        excerpt="In a survey of 412 small businesses, 68% said they adopted their last "
                "software tool after a recommendation inside a single industry community, "
                "not through general advertising.",
    ),
    SourceRecord(
        source_id="src_seed_cac_benchmarks_2026", kind="secondary",
        title="B2B Acquisition Cost Benchmarks 2026 (seed library)",
        published_at="2026-05-02",
        retrieved_at=now_iso(),
        excerpt="Median customer acquisition cost for broad self-serve B2B launches was "
                "2.4x higher than for narrow segment-first launches in the first two quarters.",
    ),
    SourceRecord(
        source_id="src_seed_partnership_report_2025", kind="secondary",
        title="Partnership-Led Growth Report 2025 (seed library)",
        published_at="2025-11-20",
        retrieved_at=now_iso(),
        excerpt="Partnership-led launches reached distribution 31% faster on average, "
                "but shared 15-25% of first-year revenue with channel partners and took "
                "a median of 11 weeks to negotiate.",
    ),
    SourceRecord(
        source_id="src_seed_retention_note_2026", kind="primary",
        title="Cohort Retention Field Notes 2026 (seed library)",
        published_at="2026-06-30",
        retrieved_at=now_iso(),
        excerpt="Products launched to a narrow, well-understood segment showed 90-day "
                "retention of 41%, versus 23% for broad launches, across 27 observed products.",
    ),
    SourceRecord(
        source_id="src_seed_founder_opinion_2026", kind="opinion",
        title="Founder Opinion: Partnerships Are All You Need (seed library)",
        published_at="2026-08-19",
        retrieved_at=now_iso(),
        excerpt="Opinion: in my experience partnerships alone can replace a sales team "
                "entirely, though no cohort data is presented in this piece.",
    ),
]
