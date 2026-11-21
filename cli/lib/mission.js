'use strict';
/* Mission lifecycle for the CLI: create (same contract the web chat
 * sends), run, and watch the event stream by polling — mapping platform
 * events onto the harness phase line: Planning → Panel → Researching →
 * Drafting → Critic → Evaluating.
 */
const s = require('./style');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PHASES = [
  { key: 'plan', label: 'Planning', events: ['MissionPlanned', 'MissionResumed'] },
  { key: 'panel', label: 'Choosing the agent panel', events: ['PanelSelected'] },
  { key: 'research', label: 'Researching', events: ['ResearchCompleted', 'ResearchFallback', 'ResearchSkipped'] },
  { key: 'draft', label: 'Drafting candidates', events: ['CandidateProposed'] },
  { key: 'critic', label: 'Critic review', events: ['CritiqueRoundCompleted', 'CritiqueSkippedBudget'] },
  { key: 'eval', label: 'Evaluating', events: ['EvaluationCompleted'] },
];
const EVENT_PHASE = {};
PHASES.forEach((p, i) => p.events.forEach((e) => { EVENT_PHASE[e] = i; }));

const TERMINAL = ['completed', 'failed', 'cancelled', 'rejected'];

function missionBody(text, opts = {}) {
  const contract = {
    objective: text,
    deliverables: ['Decision brief', 'Assumptions ledger', 'Risk register'],
    acceptance: ['Material claims cite sources'],
    /* No user-facing budget (v21.3.0 simplification law): the CLI sends
     * no max_cost_usd at all, so the server applies its single hidden
     * runaway-protection ceiling — the same one every other surface
     * gets. A former `opts.budget || 10` default silently capped every
     * CLI run at $10 while the UI/API used the ceiling. */
    constraints: {},
    tools: { allow: ['web.read', 'artifact.write'], deny: [], live_web: opts.live ? true : null },
    approval_policy: { external_side_effects: 'required' },
    data_classification: 'internal',
  };
  /* Model pinning (parity with the web chatbox picker, v21.4.2):
   * the contract's pinned_model routing — without it a CLI user
   * had no way to steer a run off a broken or depleted model that
   * auto-routing happened to pick. */
  if (opts.model) {
    contract.routing_mode = 'pinned_model';
    contract.pinned_model_id = opts.model;
  }
  return { title: text.slice(0, 60), contract };
}

async function createMission(api, text, opts = {}) {
  const key = 'cli-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  const created = await api.post('/v1/missions', missionBody(text, opts), { idempotencyKey: key });
  return created.mission_id;
}

/* Run a mission and narrate phases. Works in both server modes: inline
 * (the run POST blocks and returns the detail) and external-worker mode
 * (202 + poll the mission until terminal). */
async function runMission(api, missionId, spinner) {
  let detail = null;
  let runError = null;
  const runPromise = api.post(`/v1/missions/${missionId}/run`, {}, { timeout: 900_000 })
    .then((d) => { if (d && d.mission) detail = d; })
    .catch((e) => { runError = e; });

  const seen = new Set();
  let phaseIdx = -1;
  const advance = (idx) => {
    while (phaseIdx < idx) {
      phaseIdx++;
      /* Intermediate completions use succeedPhase: the terminal
       * succeed() would kill the spinner's timer and every later
       * phase would sit frozen on screen. */
      if (phaseIdx > 0) spinner.succeedPhase(PHASES[phaseIdx - 1].label);
      if (phaseIdx < PHASES.length) spinner.setLabel(PHASES[phaseIdx].label + '…');
    }
  };

  spinner.start('Planning…');
  /* Note: in the server's inline worker mode a running mission holds
   * the API while it works, so event polls can block until the run
   * finishes — phases then advance in a burst from the recorded event
   * log. Against an external-worker deployment they stream live. The
   * short poll timeouts keep this loop responsive either way, and the
   * TTY spinner shows elapsed time so the wait never looks frozen. */
  const startedAt = Date.now();
  const deadline = startedAt + 900_000;
  while (Date.now() < deadline) {
    try {
      const ev = await api.get(`/v1/missions/${missionId}/events`, { timeout: 6_000 });
      for (const e of ev.events || []) {
        if (seen.has(e.event_id)) continue;
        seen.add(e.event_id);
        const idx = EVENT_PHASE[e.event_type];
        if (idx !== undefined) advance(idx);
      }
    } catch { /* transient — keep polling the mission itself */ }
    if (detail) break;
    try {
      const d = await api.get(`/v1/missions/${missionId}`, { timeout: 6_000 });
      if (d && d.mission && TERMINAL.includes(d.mission.status)) { detail = d; break; }
    } catch { /* retry loop */ }
    if (runError) break;
    if (spinner.ttySpin) {
      const label = phaseIdx >= 0 ? PHASES[phaseIdx].label : 'Planning';
      spinner.setLabel(`${label}… ${Math.round((Date.now() - startedAt) / 1000)}s`);
    }
    await sleep(700);
  }
  await Promise.race([runPromise, sleep(1500)]);
  if (!detail && !runError) {
    try { detail = await api.get(`/v1/missions/${missionId}`); } catch { /* fall through */ }
  }
  if (runError && !detail) { spinner.fail('Mission run failed'); throw runError; }
  if (!detail) { spinner.fail('Timed out waiting for the mission'); throw new Error('mission did not finish within 15 minutes — it may still complete on the server; check `agentqon missions` or the web UI'); }
  if (detail.mission.status === 'completed') {
    advance(PHASES.length - 1);
    spinner.succeed(PHASES[PHASES.length - 1].label);
  } else {
    spinner.fail(`Mission ${detail.mission.status}`);
  }
  return detail;
}

async function runMissionFlow(api, text, opts = {}) {
  const spinner = new s.Spinner();
  const missionId = await createMission(api, text, opts);
  const detail = await runMission(api, missionId, spinner);
  return detail;
}

module.exports = { missionBody, createMission, runMission, runMissionFlow, TERMINAL };
