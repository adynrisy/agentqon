"""Third pass (v2, backslash-safe): data-grounded simulation in chat."""
import pathlib

B = chr(92)  # literal backslash, avoids escape ambiguity
p = pathlib.Path("apps/web/index.html")
s = p.read_text()

def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, f"anchor count {c} != {n}: {old[:80]!r}"
    s = s.replace(old, new)

# ---- 1. chat result intro: outcomes first, cost demoted ----
rep("h += `<div style=\"margin-top:8px\">Done — two strategies, researched and scored. Spent <b>$${Number(m.spent).toFixed(2)}</b> of your $${Number(m.budget_limit).toFixed(2)} budget. Pick the one you trust:</div>`;",
    "h += `<div style=\"margin-top:8px\">Done — two strategies, researched and scored. Pick the one you trust, or simulate them in a world built from this research:</div>`;\n"
    "    h += `<div class=\"row\" style=\"margin-top:8px\"><button class=\"small\" onclick=\"chatSimulate('${m.id}')\">🌍 Simulate these strategies</button><span class=\"mut small\">Run cost $${Number(m.spent).toFixed(2)} · cap $${Number(m.budget_limit).toFixed(2)}</span></div>`;")

# ---- 2. background runs: per-mission live bubble ----
rep("async function runChatMission(mid){\n  chatBusy = true; $('chatSend').disabled = true;",
    "async function runChatMission(mid){\n  chatBusy = true;")
rep("</ul></div>`;\n  $('liveBox').scrollIntoView({behavior:'smooth'});",
    "</ul></div>`;\n  const lb0 = document.getElementById('live-' + mid); if (lb0) lb0.scrollIntoView({behavior:'smooth'});")
rep('id=' + B + '"liveBox' + B + '"', 'id=' + B + '"live-${mid}' + B + '"')
rep("const ul = $('liveSteps'); if (!ul) { clearInterval(poller); return; }",
    "const ul = document.getElementById('steps-' + mid); if (!ul) { clearInterval(poller); return; }")
rep('<ul class=' + B + '"steps' + B + '" id=' + B + '"liveSteps">',
    '<ul class=' + B + '"steps' + B + '" id=' + B + '"steps-${mid}">')
rep("  done = true; clearInterval(poller);\n  chatBusy = false; $('chatSend').disabled = false;\n  const lb = $('liveBox'); if (lb) lb.remove();",
    "  done = true; clearInterval(poller);\n  chatBusy = false;\n  const lb = document.getElementById('live-' + mid); if (lb) lb.remove();")
rep("}catch(e){ toast(e.message); chatBusy = false; $('chatSend').disabled = false; }",
    "}catch(e){ toast(e.message); chatBusy = false; }")

# ---- 3. simulation chat flow ----
SIM_JS = """
async function chatSimulate(mid){
  const boxId = 'sim-' + Date.now().toString(36);
  $('thread').innerHTML += `<div class="msg ai" id="${boxId}"><div class="who">AgentQon · simulation</div><ul class="steps"><li class="pending">Building a world from this mission's research…</li></ul></div>`;
  const box = document.getElementById(boxId); box.scrollIntoView({behavior:'smooth'});
  const setSteps = (lines, pending) => { box.innerHTML = `<div class="who">AgentQon · simulation</div><ul class="steps">${lines.map(x => `<li>${esc(x)}</li>`).join('')}${pending ? `<li class="pending">${esc(pending)}</li>` : ''}</ul>`; };
  try{
    const d = await api('/v1/missions/' + mid);
    const strategies = [...new Set((d.candidates||[]).map(c => c.strategy))];
    const world = await api('/v1/worlds', { method:'POST', body: JSON.stringify({ name: 'World — ' + d.mission.title.slice(0, 50), mission_id: mid }) });
    const g = (world.provenance && world.provenance.grounding) || {};
    const gLines = Object.entries(g).map(([k, v]) => {
      const label = k === 'market_size' ? 'Market size' : k === 'price_usd_month' ? 'Price' : 'Competitor strength';
      const val = k === 'market_size' ? Number(v.value).toLocaleString() : k === 'price_usd_month' ? '$' + v.value + '/mo' : v.value;
      return v.origin === 'data' ? `${label}: ${val} — from your research ("${v.evidence}")` : `${label}: ${val} — assumption (no figure found in the research)`;
    });
    setSteps(['World built from the mission research', ...gLines], 'Agents are running ' + strategies.length + ' strategies through 26 weeks of market…');
    for (const c of d.candidates) {
      await api('/v1/worlds/' + world.world_id + '/branches', { method:'POST', body: JSON.stringify({ strategy: c.strategy, label: c.title, origin_candidate_id: c.candidate_id }) });
    }
    await api('/v1/worlds/' + world.world_id + '/run', { method:'POST', body:'{}' });
    const comp = await api('/v1/worlds/' + world.world_id + '/compare');
    const rows = [...comp.rows].sort((a, b) => b.adopters - a.adopters);
    const fmt = (r) => `<div class="cand2"><h4>${esc(r.label)}</h4><div class="small">👥 <b>${Number(r.adopters).toLocaleString()}</b> adopters · 📊 ${r.market_share_pct}% of the market · 💰 $${Number(r.total_revenue_usd).toLocaleString()} revenue · 🔁 ${r.retention_90d_pct}% still there at 90 days</div><div class="mut small">after 26 weeks in the simulated world</div></div>`;
    const winner = rows[0];
    box.innerHTML = `<div class="who">AgentQon · simulation</div>
      <div>🌍 <b>Simulated world — built from this mission's research</b> <span class="chip warn">SIMULATED ESTIMATE</span></div>
      <ul class="steps">${gLines.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      ${rows.map(fmt).join('')}
      <div style="margin-top:8px">In this world, <b>${esc(winner.label)}</b> ends strongest — ${Number(winner.adopters).toLocaleString()} adopters and ${winner.market_share_pct}% market share. An estimate from your data, not a prediction — the full world (sensitivity, weekly curves) is under Advanced → Simulation Worlds.</div>`;
    box.scrollIntoView({behavior:'smooth'});
  }catch(e){ box.innerHTML = `<div class="who">AgentQon · simulation</div><div>Simulation could not run: ${esc(e.message)}</div>`; }
}
"""
rep("function setView(v){", SIM_JS + "\nfunction setView(v){")

# ---- 4. Advanced world detail: grounding provenance ----
old_line = ('<div class=' + B + '"mut small">Baseline ${esc(w.baseline_hash)} · seed ${w.seed} '
            '· engine ${esc(w.engine_version)} · market ${w.baseline.market_size} '
            '· $${w.baseline.price_usd_month}/mo · budget $${w.baseline.budget_usd}</div>')
ground_line = ('      ${(w.provenance && w.provenance.grounding) ? `<div class=' + B + '"small" '
               'style=' + B + '"margin-top:4px">🌍 <b>Grounded in the mission research:</b> '
               '${Object.entries(w.provenance.grounding).map(([k,v]) => `${esc(k)} = <b>${esc(String(v.value))}</b> '
               '(${v.origin === \'data\' ? \'data — \' + esc(v.evidence) : \'assumption — \' + esc(v.evidence)})`).join(\' · \')}</div>` : \'\'}')
rep(old_line, old_line + "\n" + ground_line)

p.write_text(s)
print("simulation chat pass applied, size:", len(s))
