"""One-shot UI surgery: wrap the dashboard, add the chat-first view.
Every replacement is anchor-checked; aborts if an anchor is missing."""
import pathlib, sys

p = pathlib.Path("apps/web/index.html")
s = p.read_text()

def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, f"anchor count {c} != {n}: {old[:70]!r}"
    s = s.replace(old, new)

# ---------- 1. header: plain subtitle + view toggle + tenant box id ----------
rep("Collaborative, evidence-driven agent platform · Phase 3: BYOK connections · OmniRoute gateway · cost-aware routing · Evidence beats confidence",
    "Your AI research team — ask a question, get evidence-checked strategies back.")
rep('<div class="row">\n    <label style="margin:0">Tenant</label>',
    '<div class="row">\n    <button class="viewbtn active" id="viewChatBtn">💬 Chat</button>\n'
    '    <button class="viewbtn secondary" id="viewDashBtn">⚙ Advanced</button>\n'
    '  </div>\n  <div class="row" id="tenantBox">\n    <label style="margin:0">Tenant</label>')

# ---------- 2. chat CSS ----------
CSS = """
/* ---- chat-first view ---- */
.viewbtn{font-weight:600}
body[data-view="chat"] #tenantBox{display:none!important}
#chatView{max-width:860px;margin:0 auto}
#welcome{text-align:center;padding:34px 10px 6px}
.wlogo{width:64px;height:64px;border-radius:18px;background:linear-gradient(135deg,#7c5cff,#00c2a8);display:flex;align-items:center;justify-content:center;font-size:34px;margin:0 auto 12px;color:#fff}
.wtitle{font-size:26px;margin:4px 0 6px}
.examples{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:14px}
.exchip{border:1px solid #2c3550;background:#171d2e;color:#dbe2f0;border-radius:999px;padding:8px 14px;cursor:pointer;font-size:13px}
.exchip:hover{border-color:#7c5cff}
.recent{display:flex;gap:8px;overflow-x:auto;padding:10px 2px;scrollbar-width:thin}
.rchip{white-space:nowrap;border:1px solid #2c3550;background:#141a29;color:#c6cede;border-radius:10px;padding:6px 10px;cursor:pointer;font-size:12px}
.rchip b{color:#fff}
.rchip .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#8a93a8;margin-right:6px}
.rchip .dot.done{background:#00c2a8}.rchip .dot.run{background:#ffb020}
#thread{display:flex;flex-direction:column;gap:14px;padding:8px 0 18px;min-height:120px}
.msg{max-width:92%;padding:12px 14px;border-radius:14px;line-height:1.45}
.msg.user{align-self:flex-end;background:#2b3a67;color:#eef2ff;border-bottom-right-radius:4px}
.msg.ai{align-self:flex-start;background:#141a29;border:1px solid #232c44;border-bottom-left-radius:4px;width:100%;max-width:100%}
.msg .who{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8a93a8;margin-bottom:6px}
.steps{list-style:none;margin:6px 0 0;padding:0;font-size:13px;color:#aeb8cf}
.steps li{padding:3px 0 3px 22px;position:relative}
.steps li:before{content:"✓";position:absolute;left:2px;color:#00c2a8}
.steps li.pending:before{content:"…";color:#ffb020}
.cand2{border:1px solid #2c3550;border-radius:12px;padding:14px;margin-top:10px;background:#10151f}
.cand2.sel{border-color:#00c2a8;box-shadow:0 0 0 1px #00c2a8 inset}
.cand2 h4{margin:0 0 4px;font-size:16px}
.scorebar{display:flex;align-items:center;gap:8px;font-size:12px;margin:3px 0}
.scorebar span{width:104px;color:#aeb8cf}
.scorebar .bar{flex:1}
.src{font-size:12px;color:#8fb7ff;word-break:break-all}
#composer{position:sticky;bottom:10px;background:#141a29;border:1px solid #2c3550;border-radius:14px;padding:10px;box-shadow:0 8px 30px rgba(0,0,0,.45)}
#composer textarea{width:100%;background:transparent;border:none;resize:vertical;color:#e8ecf6;font-size:15px;outline:none}
.crow{display:flex;align-items:center;gap:12px;margin-top:6px;flex-wrap:wrap}
.crow select{width:auto;display:inline-block}
details.how summary{cursor:pointer;color:#8a93a8;font-size:12px}
"""
rep("</style>", CSS + "</style>")

# ---------- 3. chat view markup + dashboard wrapper ----------
CHAT = """
<section id="chatView">
  <div id="welcome">
    <div class="wlogo">Ω</div>
    <h2 class="wtitle">What should we work on?</h2>
    <p class="sub">Ask anything that needs research and a decision. AgentQon searches the live web, two agents draft competing strategies, a critic attacks the evidence, and everything is scored before you choose. No forms, no jargon.</p>
    <div class="examples">
      <button class="exchip" data-ex="Compare growth strategies for a Facebook page posting short talking-cat comedy videos">🐱 Grow my PawPaw Cats page</button>
      <button class="exchip" data-ex="Compare go-to-market strategies for a subscription invoicing tool for freelancers">🧾 Launch a freelancer invoicing tool</button>
      <button class="exchip" data-ex="Compare pricing strategies for an online course platform for language teachers">🎓 Price an online course platform</button>
    </div>
  </div>
  <div id="recentRow" class="recent"></div>
  <div id="thread"></div>
  <div id="composer">
    <textarea id="chatInput" rows="2" placeholder="Describe what you want to figure out…"></textarea>
    <div class="crow">
      <label class="small mut">Budget <select id="chatBudget"><option value="5">$5</option><option value="10" selected>$10</option><option value="25">$25</option></select></label>
      <label class="small mut">Privacy <select id="chatClass"><option value="internal">Internal · web research on</option><option value="public">Public · web research on</option><option value="confidential">Confidential · no web search</option></select></label>
      <span style="flex:1"></span>
      <button id="chatSend">Ask AgentQon ➤</button>
    </div>
    <div class="mut small" style="margin-top:4px">Enter sends · Shift+Enter for a new line · Power features (connections, models, simulations, members) live under <b>⚙ Advanced</b>.</div>
  </div>
</section>
<div id="dashView" style="display:none">
"""
rep('  <div class="card">\n    <h2>New Mission</h2>', CHAT + '  <div class="card">\n    <h2>New Mission</h2>')
rep("</main>", "</div>\n</main>")

# ---------- 4. chat JS ----------
JS = r"""
/* ================= chat-first view ================= */
let chatBusy = false;
const STEP_LABELS = {
  MissionCreated: () => 'Mission set up',
  MissionPlanned: () => 'Plan ready — research, draft, critique, score',
  ResearchCompleted: p => 'Researched the live web — ' + (p.sources||0) + ' sources found',
  ResearchSkipped: () => 'Web research off for this privacy level — used the built-in knowledge base',
  ResearchFallback: () => 'Live research had a hiccup — fell back to the built-in knowledge base',
  CandidatesProduced: () => 'Two strategies drafted by different agents',
  CritiqueCompleted: () => 'A critic agent attacked every claim against its sources',
  EvaluationCompleted: () => 'Scored against the evidence rubric',
  AgentFallback: () => 'A model hiccuped — switched to a backup agent automatically',
  BranchPromoted: () => 'Simulation branch promoted to a draft mission',
  MissionFailed: () => 'The mission hit a problem',
};
function friendlySteps(events){
  const out = []; const seen = new Set();
  for (const e of events || []) {
    const f = STEP_LABELS[e.event_type]; if (!f) continue;
    const label = f(e.payload || {});
    if (seen.has(label)) continue; seen.add(label); out.push(label);
  }
  return out;
}
async function chatSources(){
  try{ const s = await api('/v1/sources'); const m = {}; (s.sources||[]).forEach(x => m[x.source_id] = x); return m; }
  catch(e){ return {}; }
}
function scoreRow(k, v){
  return `<div class="scorebar"><span>${esc(k.replace(/_/g,' '))}</span><div class="bar"><i style="width:${v.score/5*100}%"></i></div><b>${v.score}/5</b></div>`;
}
function candCard(c, d, srcById){
  const ev = (d.evaluations||[]).find(e => e.candidate_id === c.candidate_id);
  const crits = (d.critiques||[]).filter(x => x.candidate_id === c.candidate_id);
  const sel = d.mission.selected_candidate_id === c.candidate_id || c.status === 'selected';
  const dims = ev ? Object.entries(ev.dimensions).map(([k,v]) => scoreRow(k, v)).join('') : '';
  const claims = (c.claims||[]).slice(0, 3).map(cl => {
    const links = (cl.source_ids||[]).map(id => srcById[id]).filter(x => x && x.url)
      .map(x => `<div class="src">🔗 ${esc(x.url)}</div>`).join('');
    return `<div class="claim">${esc(cl.text)}${links}</div>`;
  }).join('');
  const critHtml = crits.length ? `<div class="small" style="margin-top:8px"><b>Critic's warnings:</b>${crits.map(x => `<div>⚠ <span class="chip ${x.severity==='high'?'bad':x.severity==='medium'?'warn':''}">${esc(x.severity)}</span> ${esc(x.issue)}</div>`).join('')}</div>` : '<div class="small mut" style="margin-top:8px">Critic found no material problems.</div>';
  return `<div class="cand2 ${sel?'sel':''}">
    <div class="row"><h4>${esc(c.title)}</h4>${sel?'<span class="chip ok">✓ YOUR PICK</span>':''}</div>
    <p class="small">${esc(c.summary)}</p>
    ${dims}
    <div class="small mut">Confidence: <b style="color:#dbe2f0">${ev?esc(ev.confidence):'—'}</b> · Est. strategy cost: $${Number(c.estimated_cost_usd||0).toLocaleString()} · Time to learn: ${esc(c.time_to_learn||'—')}</div>
    <h3 style="margin:10px 0 4px">Key claims &amp; their evidence</h3>${claims || '<div class="mut small">No claims.</div>'}
    ${critHtml}
    <div class="row" style="margin-top:10px">
      ${sel ? '<span class="chip ok">Selected — this is your strategy</span>' : `<button class="small" onclick="chatSelect('${c.candidate_id}')">✓ Use this strategy</button>`}
      <button class="small secondary" onclick="chatBrief('${d.mission.id}')">⬇ Download brief</button>
    </div></div>`;
}
function renderThread(d, srcById){
  const m = d.mission;
  const steps = friendlySteps(d.events);
  let h = `<div class="msg user"><div class="who">You</div>${esc(m.contract ? m.contract.objective : m.title)}</div>`;
  h += '<div class="msg ai"><div class="who">AgentQon</div>';
  if (steps.length) h += `<details class="how"><summary>How it worked — ${steps.length} steps</summary><ul class="steps">${steps.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>`;
  if (m.status === 'completed' || (d.candidates||[]).length) {
    h += `<div style="margin-top:8px">Done — two strategies, researched and scored. Spent <b>$${Number(m.spent).toFixed(2)}</b> of your $${Number(m.budget_limit).toFixed(2)} budget. Pick the one you trust:</div>`;
    h += (d.candidates||[]).map(c => candCard(c, d, srcById)).join('');
  } else if (m.status === 'draft') {
    h += `<div style="margin-top:8px">This one hasn't run yet.</div><div class="row" style="margin-top:8px"><button class="small" onclick="chatRunExisting('${m.id}')">▶ Run it now</button></div>`;
  } else if (m.status === 'failed') {
    h += '<div style="margin-top:8px">This mission failed — open it under ⚙ Advanced to see the event log.</div>';
  } else {
    h += `<div style="margin-top:8px">Status: <b>${esc(m.status)}</b> — still working.</div>`;
  }
  h += '</div>';
  $('thread').innerHTML = h;
  $('welcome').style.display = 'none';
}
async function openChatMission(id){
  try{ const d = await api('/v1/missions/' + id); const src = await chatSources(); renderThread(d, src); window.scrollTo({top:0, behavior:'smooth'}); }
  catch(e){ toast(e.message); }
}
async function chatSelect(cid){
  const mid = window._chatMission; if (!mid) return;
  try{ await api('/v1/missions/' + mid + '/select', { method:'POST', body: JSON.stringify({ candidate_id: cid }) });
    toast('Strategy selected'); openChatMission(mid); renderRecent();
  }catch(e){ toast(e.message); }
}
async function chatBrief(mid){
  try{ const r = await fetch('/v1/missions/' + mid + '/export', { headers: headers() });
    if (!r.ok) throw new Error('export failed');
    const blob = await r.blob(); const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'agentqon-brief-' + mid + '.md'; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }catch(e){ toast(e.message); }
}
async function renderRecent(){
  try{ const ms = await api('/v1/missions');
    $('recentRow').innerHTML = (ms.missions||[]).slice(0, 12).map(m =>
      `<button class="rchip" onclick="window._chatMission='${m.id}';openChatMission('${m.id}')"><span class="dot ${m.status==='completed'?'done':['queued','planned','running'].includes(m.status)?'run':''}"></span><b>${esc(m.title.slice(0, 34))}</b> · ${esc(m.status)}</button>`).join('');
  }catch(e){ $('recentRow').innerHTML = ''; }
}
async function chatRunExisting(mid){ window._chatMission = mid; await runChatMission(mid); }
async function runChatMission(mid){
  chatBusy = true; $('chatSend').disabled = true;
  $('welcome').style.display = 'none';
  $('thread').innerHTML += `<div class="msg ai" id="liveBox"><div class="who">AgentQon · working</div><ul class="steps" id="liveSteps"><li class="pending">Starting…</li></ul></div>`;
  $('liveBox').scrollIntoView({behavior:'smooth'});
  const seen = new Set(); let done = false;
  const poller = setInterval(async () => {
    try{ const r = await api('/v1/missions/' + mid + '/events'); const labels = friendlySteps(r.events);
      const ul = $('liveSteps'); if (!ul) { clearInterval(poller); return; }
      const fresh = labels.filter(x => !seen.has(x)); fresh.forEach(x => seen.add(x));
      if (fresh.length) ul.innerHTML = [...seen].map(x => `<li>${esc(x)}</li>`).join('') + (done ? '' : '<li class="pending">Working…</li>');
    }catch(e){}
  }, 1500);
  try{ await api('/v1/missions/' + mid + '/run', { method:'POST', body:'{}' }); }
  catch(e){ toast(e.message); }
  done = true; clearInterval(poller);
  chatBusy = false; $('chatSend').disabled = false;
  const lb = $('liveBox'); if (lb) lb.remove();
  await openChatMission(mid); renderRecent();
}
async function sendChat(){
  const text = $('chatInput').value.trim(); if (!text || chatBusy) return;
  $('chatInput').value = '';
  try{
    const body = { title: text.slice(0, 60),
      contract: { objective: text, deliverables: ['Decision brief', 'Assumptions ledger', 'Risk register'],
        acceptance: ['Material claims cite sources'],
        constraints: { max_cost_usd: Number($('chatBudget').value) },
        tools: { allow: ['web.read', 'artifact.write'], deny: [] },
        approval_policy: { external_side_effects: 'required' },
        data_classification: $('chatClass').value } };
    const m = await api('/v1/missions', { method:'POST', body: JSON.stringify(body), headers: { 'Idempotency-Key': 'chat-' + Date.now().toString(36) } });
    window._chatMission = m.mission_id;
    $('welcome').style.display = 'none';
    $('thread').innerHTML = `<div class="msg user"><div class="who">You</div>${esc(text)}</div>`;
    renderRecent();
    await runChatMission(m.mission_id);
  }catch(e){ toast(e.message); chatBusy = false; $('chatSend').disabled = false; }
}
function setView(v){
  document.body.dataset.view = v;
  $('chatView').style.display = v === 'chat' ? 'block' : 'none';
  $('dashView').style.display = v === 'dash' ? 'block' : 'none';
  $('viewChatBtn').className = 'viewbtn' + (v === 'chat' ? ' active' : ' secondary');
  $('viewDashBtn').className = 'viewbtn' + (v === 'dash' ? ' active' : ' secondary');
  if (v === 'dash') refreshLists();
}
$('viewChatBtn').onclick = () => setView('chat');
$('viewDashBtn').onclick = () => setView('dash');
$('chatSend').onclick = sendChat;
$('chatInput').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } });
document.querySelectorAll('.exchip').forEach(b => b.onclick = () => { $('chatInput').value = b.dataset.ex; sendChat(); });
// when the dashboard opens a mission, remember it for chat actions too
const _openMission = openMission;
openMission = async function(id){ window._chatMission = id; return _openMission(id); };
setView('chat'); renderRecent();
"""
rep("refreshLists();\n</script>", "refreshLists();\n" + JS + "\n</script>")

p.write_text(s)
print("redesign applied, new size:", len(s))
