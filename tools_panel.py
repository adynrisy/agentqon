"""Fifth pass (v13): suitability in the UI — registry record column,
suitability board, updated import copy, chat panel narration."""
import pathlib

p = pathlib.Path("apps/web/index.html")
s = p.read_text()
B = chr(92)  # backslash

def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, f"anchor count {c} != {n}: {old[:80]!r}"
    s = s.replace(old, new)

# ---- registry card: copy + Record column + suitability board ----
rep('Imported agency agents (msitarzewski/agency-agents, MIT) start in <b>testing</b> per the onboarding rule — activate them to make them eligible. Personas run through the LLM adapter in LLM mode; their quality scores are declared priors, not measurements.',
    'Imported agency agents (msitarzewski/agency-agents, MIT) arrive <b>active</b> and join the pool immediately. Every mission, AgentQon picks a panel of the best-suited agents for that kind of task — ranked by capability fit blended with their measured record (scores, critiques, your picks). Personas run through the LLM adapter in LLM mode.')
rep('<th>Agent</th><th>Capabilities</th><th>Quality</th><th>Lifecycle</th><th></th>',
    '<th>Agent</th><th>Capabilities</th><th>Quality</th><th>Record</th><th>Lifecycle</th><th></th>')
rep('''      <table><thead><tr><th>Agent</th><th>Capabilities</th><th>Quality</th><th>Record</th><th>Lifecycle</th><th></th></tr></thead><tbody id="agents"></tbody></table>
    </div>''',
    '''      <table><thead><tr><th>Agent</th><th>Capabilities</th><th>Quality</th><th>Record</th><th>Lifecycle</th><th></th></tr></thead><tbody id="agents"></tbody></table>
      <h3 style="margin-top:14px">Who suits which task?</h3>
      <div class="sub">The live suitability ranking AgentQon uses to pick each mission's panel. Fit = capability/division match; the score blends fit and declared quality with the measured record as runs accumulate.</div>
      <div class="row"><select id="suitType" onchange="loadSuitability()"><option value="strategy">🎯 Strategy decisions</option><option value="writing">✍️ Writing</option><option value="code">💻 Code</option><option value="plan">🗓️ Plans</option><option value="research">🔍 Research</option><option value="qa">❓ Questions</option><option value="general">🧩 General</option></select></div>
      <div id="suitBoard" style="margin-top:8px"></div>
    </div>''')

# ---- refreshLists: fetch stats + aggregate records ----
rep("    const [ms, ag, us, src] = await Promise.all([api('/v1/missions'), api('/v1/agents'), api('/v1/usage'), api('/v1/sources')]); window._missions = ms.missions;",
    "    const [ms, ag, us, src] = await Promise.all([api('/v1/missions'), api('/v1/agents'), api('/v1/usage'), api('/v1/sources')]); window._missions = ms.missions;\n"
    "    const statsResp = await api('/v1/agents/stats').catch(() => ({ stats: [] }));\n"
    "    const REC = {}; (statsResp.stats || []).forEach(x => { const r = REC[x.agent_version_id] || (REC[x.agent_version_id] = { runs: 0, wsum: 0, sel: 0 }); r.runs += x.runs; r.wsum += (x.mean_eval || 0) * x.runs; r.sel += x.selections; });")

# ---- agents row: Record cell between Quality and Lifecycle ----
old_q = '<td>${a.quality}/5</td><td><span class="chip '
K = "REC[a.agent_version_id]"
rec_expr = ("${" + K + " && " + K + ".runs ? " + K + ".runs + ' runs · avg ' + ("
            + K + ".wsum / " + K + ".runs).toFixed(1) + ' · picked ' + " + K
            + ".sel + '×' : 'no runs yet'}")
new_q = ('<td>${a.quality}/5</td><td class="small">'
         + rec_expr + '</td><td><span class="chip ')
rep(old_q, new_q)

# ---- suitability board loader + trigger ----
rep("async function importAgency(){",
    """async function loadSuitability(){
  const el = $('suitBoard'); if (!el) return;
  try{
    const d = await api('/v1/agents/suitability?task_type=' + $('suitType').value);
    el.innerHTML = d.ranking.map((e, i) => '<div class="row" style="justify-content:space-between;border-bottom:1px solid #1b2338;padding:5px 0"><span>' + (i + 1) + '. <b>' + esc(e.agent_id) + '</b> ' + (e.runs ? '<span class="mut small">' + e.runs + ' runs · avg ' + e.measured_eval + ' · picked ' + e.selections + '×</span>' : '<span class="mut small">unproven — prior only</span>') + '</span><span class="chip">' + e.score.toFixed(2) + '</span></div>').join('') || '<span class="mut">No eligible agents.</span>';
  }catch(err){ el.innerHTML = '<span class="mut">Could not load the ranking.</span>'; }
}
async function importAgency(){""")
rep("    $('agents').innerHTML = _agents.map(a=>",
    "    loadSuitability();\n    $('agents').innerHTML = _agents.map(a=>")
rep("toast('Imported ' + r.total + ' agency agents (testing)');",
    "toast('Imported ' + r.total + ' agency agents — all active, in the suitability pool.');")

# ---- chat narration: the panel pick ----
rep("const STEP_LABELS = {",
    "const STEP_LABELS = {\n  PanelSelected: (p) => 'Picked the ' + ((p.panel || []).length) + ' best-suited agents for this ' + (p.task_type || 'task') + ' from the full roster',")

p.write_text(s)
print("panel UI pass applied, size:", len(s))
