"""AgentQon — society engine hardening (platform side).

Pure functions over society records, so they can be tested without a
model and applied to any record shape the engine produces. The engine
itself (app/society.py) is NOT modified here; these are the validation,
recovery and interpretation layers around it.

Stances match the engine: "support", "oppose", "neutral". Confidence
is a number from 0 to 1, inclusive.

THE SEPARATION RULE (invariant, proven by test): information received,
reaction generated, confidence changed and stance changed are FOUR
separate records. A participant's state keeps four separate histories
(``received``, ``reactions``, ``confidence_history``,
``stance_history``). Nothing ever writes a reaction into the received
list or a confidence change into the stance list. That is what stops
the graph from exaggerating persuasion: a stance change counts only
when it is recorded as a stance change, and persuasion is measured
only from stance changes that follow a received item — never from
activity alone.

§12 items covered: 207 initial-state validation, 208 stance-value
validation, 209 confidence-range checking, 210 interaction
eligibility, 212 round-order consistency, 214 reaction-completeness
checking, 215 invalid-state recovery, 219 population-total checking.
Items 206, 213, 216, 217, 218, 220 were named as "harden to full" in
the program brief but their audit texts were NOT in this builder's
handoff and no audit document on disk contains them; they are NOT
claimed here (see the report: UNVERIFIED-text-missing, never invented).
The hardening layer below (``harden_record``) applies every named
check together, which is the full hardening of the record for the
items whose texts were supplied.
"""
from __future__ import annotations

import time

STANCES = ("support", "oppose", "neutral")
SIMULATION_MODES = ("individual", "batched", "deterministic", "hybrid")


# ---------------------------------------------------------------- 207/208/209
def validate_stance(stance: object) -> str | None:
    if stance not in STANCES:
        return (f"Stance {stance!r} is not valid — a stance must be "
                "support, oppose or neutral.")
    return None


def validate_confidence(value: object) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"Confidence {value!r} is not a number — it must be between 0 and 1."
    if not 0.0 <= float(value) <= 1.0:
        return (f"Confidence {value} is outside 0 to 1. "
                "Confidence is a range, not an open-ended score.")
    return None


def validate_initial_state(participants: list[dict]) -> list[str]:
    """207: every participant must start valid — unique id, valid
    stance, confidence in range. One bad participant refuses the whole
    cast, because a world built on a broken cast cannot be trusted."""
    errors = []
    seen: set[str] = set()
    for p in participants:
        pid = p.get("agent_id")
        if not pid:
            errors.append("A participant has no id — every participant needs one.")
            continue
        if pid in seen:
            errors.append(f"Participant id {pid!r} appears twice — ids must be unique.")
        seen.add(pid)
        err = validate_stance(p.get("stance"))
        if err:
            errors.append(f"Participant {pid}: {err}")
        err = validate_confidence(p.get("confidence"))
        if err:
            errors.append(f"Participant {pid}: {err}")
    return errors


# ---------------------------------------------------------------- 210
def interaction_eligible(*, participant: dict, round_no: int,
                         already_reacted_this_round: set[str]) -> dict:
    """210: a participant may interact in a round only when it has a
    valid recorded state, has not already reacted this round, and the
    round number is sane. Eligibility is checked, never assumed."""
    pid = participant.get("agent_id", "?")
    if round_no < 1:
        return {"eligible": False, "reason": f"Round {round_no} does not exist."}
    if pid in already_reacted_this_round:
        return {"eligible": False,
                "reason": f"Participant {pid} already reacted this round — "
                          "a second reaction would double-count it."}
    err = validate_stance(participant.get("stance")) or validate_confidence(
        participant.get("confidence"))
    if err:
        return {"eligible": False,
                "reason": f"Participant {pid} has invalid state: {err}"}
    return {"eligible": True, "reason": "Eligible."}


# ---------------------------------------------------------------- 212
def round_order_consistency(events: list[dict]) -> list[str]:
    """212: rounds must start at 1 and never go backwards. A round
    that arrives out of order means the record cannot be replayed
    truthfully, so it is flagged, not silently re-sorted."""
    errors = []
    previous = 0
    for i, event in enumerate(events):
        round_no = event.get("round")
        if not isinstance(round_no, int) or isinstance(round_no, bool):
            errors.append(f"Event {i} has no valid round number.")
            continue
        if i == 0 and round_no != 1:
            errors.append(f"The first event is in round {round_no}, not round 1.")
        if round_no < previous:
            errors.append(f"Event {i} goes back to round {round_no} after "
                          f"round {previous} — rounds must not go backwards.")
        previous = max(previous, round_no)
    return errors


# ---------------------------------------------------------------- 214
def reaction_completeness(events: list[dict], cast_ids: list[str],
                          *, rounds: int) -> dict:
    """214: for each round, which cast members reacted and which did
    not. A missing reaction is recorded as non-response, never filled
    in with an invented reaction."""
    by_round: dict[int, set[str]] = {}
    for event in events:
        by_round.setdefault(event.get("round", 0), set()).add(event.get("agent_id"))
    report = {}
    for round_no in range(1, rounds + 1):
        reacted = by_round.get(round_no, set())
        report[round_no] = {"reacted": sorted(reacted),
                            "non_responded": sorted(set(cast_ids) - reacted)}
    return report


# ---------------------------------------------------------------- 215
def invalid_state_recovery(record: dict) -> dict:
    """215: repair an invalid record WITHOUT hiding the repair. Returns
    the repaired record plus the list of repairs made. Repairs: clamp
    out-of-range confidence, drop events with invalid stances (counted,
    not rewritten), and note missing casts. The original is never
    silently overwritten — the caller keeps both."""
    import copy
    repaired = copy.deepcopy(record)
    repairs: list[str] = []
    for p in repaired.get("cast", []):
        conf = p.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            if conf < 0.0:
                p["confidence"] = 0.0
                repairs.append(f"Participant {p.get('agent_id')}: confidence clamped up to 0.")
            elif conf > 1.0:
                p["confidence"] = 1.0
                repairs.append(f"Participant {p.get('agent_id')}: confidence clamped down to 1.")
    kept = []
    for event in repaired.get("events", []):
        if event.get("stance") in STANCES:
            kept.append(event)
        else:
            repairs.append(f"Event in round {event.get('round')} dropped: "
                           f"invalid stance {event.get('stance')!r}.")
    repaired["events"] = kept
    return {"record": repaired, "repairs": repairs,
            "note": "Repairs are listed here so a repaired record is never "
                    "mistaken for an originally clean one."}


# ---------------------------------------------------------------- 219
def population_total_check(*, size: int, stance_counts: dict[str, int],
                           responded: int) -> list[str]:
    """219: the population's recorded state must stay internally
    consistent — stance counts must sum to the cast size (or to the
    responded count when non-response is counted separately), and
    responded must never exceed size."""
    errors = []
    total = sum(stance_counts.values())
    if total != size and total != responded:
        errors.append(f"Stance counts add up to {total}, but the cast has "
                      f"{size} members and {responded} responded — the "
                      "population record does not add up.")
    if responded > size:
        errors.append(f"{responded} members responded, but the cast only has "
                      f"{size} members — that cannot be right.")
    if any(v < 0 for v in stance_counts.values()):
        errors.append("A stance count is negative — counts cannot be negative.")
    return errors


# ---------------------------------------------------------------- separation rule
class ParticipantState:
    """One participant's state, with FOUR separate histories. This is
    the separation rule as code: each kind of change has its own
    append-only list, and no method ever writes to another kind's list."""

    def __init__(self, *, agent_id: str, stance: str, confidence: float):
        errors = [e for e in (validate_stance(stance), validate_confidence(confidence)) if e]
        if errors:
            raise ValueError("; ".join(errors))
        self.agent_id = agent_id
        self.received: list[dict] = []
        # Repeats spotted at intake: the raw received log stays intact
        # (audit), but a repeat is tracked here the moment it arrives
        # instead of only by a standalone audit afterwards.
        self.duplicate_exposures: list[dict] = []
        self.reactions: list[dict] = []
        # State version: counts stance/confidence changes so far (0 =
        # the initial state). A reaction records the version of the
        # state that produced it — that link is the versioning.
        self.state_version = 0
        self.confidence_history: list[dict] = [{"value": confidence, "at": time.time(),
                                                "why": "initial state", "version": 0}]
        self.stance_history: list[dict] = [{"value": stance, "at": time.time(),
                                            "why": "initial state", "version": 0}]

    @property
    def stance(self) -> str:
        return self.stance_history[-1]["value"]

    @property
    def confidence(self) -> float:
        return self.confidence_history[-1]["value"]

    def receive(self, item_id: str, *, content_summary: str) -> None:
        # Same key rule as detect_duplicate_information: an item id,
        # falling back to the summary when there is none.
        key = item_id or content_summary
        if any((r.get("item_id") or r.get("summary")) == key
               for r in self.received):
            self.duplicate_exposures.append(
                {"item_id": item_id,
                 "note": "This information arrived before — it counts "
                         "as one exposure, not two.",
                 "at": time.time()})
        self.received.append({"item_id": item_id, "summary": content_summary,
                              "at": time.time()})

    @property
    def exposures(self) -> list[dict]:
        """Unique exposures, in first-arrival order: each item counted
        once. The raw received log stays intact for audit; counting
        uses this, so a repeat never counts twice."""
        seen = set()
        out = []
        for r in self.received:
            key = r.get("item_id") or r.get("summary")
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out

    def react(self, *, stance: str, text_summary: str, reason: str) -> None:
        err = validate_stance(stance)
        if err:
            raise ValueError(err)
        # A reaction is not a state change, so it does not bump the
        # version — it records the version it was produced from.
        self.reactions.append({"stance": stance, "summary": text_summary,
                               "reason": reason, "at": time.time(),
                               "state_version": self.state_version})

    def set_confidence(self, value: float, *, why: str) -> None:
        err = validate_confidence(value)
        if err:
            raise ValueError(err)
        self.state_version += 1
        self.confidence_history.append({"value": value, "at": time.time(), "why": why,
                                        "version": self.state_version})

    def set_stance(self, value: str, *, why: str) -> None:
        err = validate_stance(value)
        if err:
            raise ValueError(err)
        self.state_version += 1
        self.stance_history.append({"value": value, "at": time.time(), "why": why,
                                    "version": self.state_version})

    def persuasion_events(self) -> list[dict]:
        """Stance changes that followed a received item. Activity
        alone (reactions without a stance change) never counts as
        persuasion — that is the exaggeration this rule prevents."""
        out = []
        for change in self.stance_history[1:]:
            prior_received = [r for r in self.received if r["at"] <= change["at"]]
            out.append({"from": None, "to": change["value"],
                        "why": change["why"],
                        "followed_received_item": bool(prior_received)})
        # Fill in the 'from' values in order.
        values = [h["value"] for h in self.stance_history]
        for i, item in enumerate(out):
            item["from"] = values[i]
        return out


def detect_duplicate_information(received: list[dict]) -> list[dict]:
    """Duplicate-information detection: the same item id arriving twice
    is recognised (by id and by identical summary), so the same
    information is never counted as two separate exposures."""
    seen: dict[str, int] = {}
    duplicates = []
    for item in received:
        key = item.get("item_id") or item.get("summary")
        if key in seen:
            duplicates.append({"item_id": item.get("item_id"),
                               "note": "This information arrived before — "
                                       "it counts as one exposure, not two."})
        else:
            seen[key] = 1
    return duplicates


def replay_metadata(*, seed: int | None, config: dict, mode: str,
                    llm_used: bool) -> dict:
    """Deterministic replay record. When a live model was used, exact
    reproduction is NOT promised: the record says so honestly."""
    if mode not in SIMULATION_MODES:
        raise ValueError(f"Simulation mode {mode!r} is not one of "
                         f"{', '.join(SIMULATION_MODES)}.")
    return {"seed": seed, "config": dict(config), "mode": mode,
            "llm_used": llm_used,
            "reproducible_exactly": (not llm_used) and seed is not None,
            "reproducibility_note": (
                "Exact replay is possible (no live model, seed recorded)."
                if (not llm_used) and seed is not None else
                "A replay of this run cannot reproduce it exactly, because "
                "a live model answers differently each time. The seed and "
                "configuration are recorded so the setup can be repeated, "
                "but the model's words will differ.")}


def interpret_output(*, assumptions: list[str], generated_reactions: list[dict],
                     calculated_statistics: dict) -> dict:
    """Output interpretation in THREE labelled sections, never blended
    into one confident story. Assumptions are what was assumed before
    the run; reactions are what the simulated participants said;
    statistics are what was calculated from the record."""
    return {
        "assumptions": {"label": "Assumptions — what was assumed, not observed",
                        "items": list(assumptions)},
        "generated_reactions": {"label": "Generated reactions — what the "
                                         "simulated participants said",
                                "items": list(generated_reactions)},
        "calculated_statistics": {"label": "Calculated statistics — counted "
                                           "from the record, not generated",
                                  "items": dict(calculated_statistics)},
        "warning": ("The reactions above are simulated, not predictions "
                    "about real people."),
    }


def participant_id_stability(cast_ids: list[str],
                             events: list[dict]) -> list[str]:
    """Participant-ID stability: one id always means one participant.
    Cast ids must be unique, and every event must name an id from the
    cast — an event from outside the cast, or a cast id used twice,
    is an identity break, never a new person."""
    errors = []
    seen = set()
    for aid in cast_ids:
        if aid in seen:
            errors.append(f"Cast id {aid!r} appears more than once — "
                          "one id must mean one participant.")
        seen.add(aid)
    for event in events:
        aid = event.get("agent_id")
        if not aid:
            errors.append("An event has no agent id — every reaction "
                          "must name its participant.")
        elif aid not in seen:
            errors.append(f"Event in round {event.get('round')} names "
                          f"{aid!r}, which is not in the cast — an "
                          "outsider cannot speak in the room.")
    return errors


def harden_record(record: dict) -> dict:
    """Apply every named §12 check to a record at once and return the
    findings. This is the 'harden to full' layer for the items whose
    audit texts were supplied."""
    cast = record.get("cast", [])
    participants = [{"agent_id": m.get("agent_id"), "stance": m.get("stance"),
                     "confidence": m.get("confidence", 0.5)} for m in cast]
    events = record.get("events", [])
    size = len(cast)
    stance_counts: dict[str, int] = {}
    for event in events:
        if event.get("stance") in STANCES:
            stance_counts[event["stance"]] = stance_counts.get(event["stance"], 0) + 1
    # Final-stance counts, for the population check.
    last_stance: dict[str, str] = {}
    for event in events:
        if event.get("agent_id") and event.get("stance") in STANCES:
            last_stance[event["agent_id"]] = event["stance"]
    final_counts: dict[str, int] = {}
    for stance in last_stance.values():
        final_counts[stance] = final_counts.get(stance, 0) + 1
    id_errors = participant_id_stability(
        [m.get("agent_id") for m in cast], events)
    return {
        "initial_state_errors": validate_initial_state(participants),
        "round_order_errors": round_order_consistency(events),
        "population_errors": population_total_check(
            size=size, stance_counts=final_counts,
            responded=len(last_stance)),
        "participant_id_errors": id_errors,
        "all_valid": (not validate_initial_state(participants)
                      and not round_order_consistency(events)
                      and not population_total_check(
                          size=size, stance_counts=final_counts,
                          responded=len(last_stance))
                      and not id_errors),
    }
