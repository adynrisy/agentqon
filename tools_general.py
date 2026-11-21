"""Fourth pass (v12): general-purpose tasks in the UI — task-type chip,
deliverable rendering with a mini-markdown renderer, mixed examples,
dashboard task-type picker. Plain anchors; extracted-line insert for
the dashboard candidate card."""
import pathlib

p = pathlib.Path("apps/web/index.html")
s = p.read_text()

def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, f"anchor count {c} != {n}: {old[:80]!r}"
    s = s.replace(old, new)

# ---- CSS ----
rep("</style>", """
.deliverable{background:#0d1119;border:1px solid #232c44;border-radius:10px;padding:12px 14px;margin:10px 0;font-size:14px;line-height:1.55}
.deliverable pre{background:#05070c;border:1px solid #232c44;border-radius:8px;padding:10px;overflow-x:auto;font-size:12.5px}
.deliverable .mdh{font-weight:700;margin:10px 0 4px;font-size:15px}
.deliverable .mdul{margin:4px 0 4px 18px;padding:0}
.deliverable p{margin:5px 0}
.deliverable code{background:#1a2133;padding:1px 5px;border-radius:5px;font-size:12.5px}
</style>""")

# ---- welcome copy + mixed examples ----
rep("Just tell me what you're trying to figure out. I'll research the live web, draft two competing strategies, have a critic check the evidence, and score them — you just pick the winner.",
    "Ask me anything — a question, a draft, a plan, code, or a big decision. Two agents take it on, a critic checks the work, and everything is scored before you choose.")
rep("""      <button class="exchip" data-ex="Compare growth strategies for a Facebook page posting short talking-cat comedy videos">🐱 Grow my PawPaw Cats page</button>
      <button class="exchip" data-ex="Compare go-to-market strategies for a subscription invoicing tool for freelancers">🧾 Launch a freelancer invoicing tool</button>
      <button class="exchip" data-ex="Compare pricing strategies for an online course platform for language teachers">🎓 Price an online course platform</button>""",
    """      <button class="exchip" data-ex="Compare growth strategies for a Facebook page posting short talking-cat comedy videos">🐱 Grow my PawPaw Cats page</button>
      <button class="exchip" data-ex="Write a professional email to my landlord about a leaking tap that has gone unfixed for two weeks">✍️ Write an email for me</button>
      <button class="exchip" data-ex="Write a Python function that removes duplicate words from a sentence">💻 Write code for me</button>
      <button class="exchip" data-ex="Plan a 7-day study schedule for a biology exam">🗓️ Plan my study week</button>
      <button class="exchip" data-ex="Explain how rainbows form, in simple terms">❓ Explain something</button>""")

# ---- dashboard: task-type picker + wiring ----
rep("""    <div class="grid">
      <div><label>Project (optional)</label>""",
    """    <div class="grid">
      <div><label>Task type</label><select id="taskType"><option value="">Auto — AgentQon figures it out</option><option value="strategy">🎯 Strategy decision</option><option value="research">🔍 Research report</option><option value="writing">✍️ Writing</option><option value="code">💻 Code</option><option value="plan">🗓️ Plan</option><option value="qa">❓ Question</option><option value="general">🧩 General task</option></select></div>
      <div><label>&nbsp;</label><div class="mut small">The type decides what the agents produce: a strategy comparison, a report, a finished draft, working code, a plan, or an answer. Auto is right almost always.</div></div>
    </div>
    <div class="grid">
      <div><label>Project (optional)</label>""")
rep("      data_classification: $('classification').value,",
    "      data_classification: $('classification').value,\n      task_type: $('taskType').value || null,")

# ---- chat JS: labels + markdown renderer (insert before chat section) ----
MD_JS = r"""
const TASK_LABELS = { strategy: '🎯 Strategy decision', research: '🔍 Research report', writing: '✍️ Writing', code: '💻 Code', plan: '🗓️ Plan', qa: '❓ Question', general: '🧩 General task' };
function mdRender(md){
  const escHtml = (x) => x.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const inline = (x) => x.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
  const parts = String(md || '').split(/```(\w*)\n?/);
  let html = '';
  for (let i = 0; i < parts.length; i++){
    if (i % 3 === 2){ html += '<pre>' + escHtml(parts[i].replace(/\n$/,'')) + '</pre>'; continue; }
    if (i % 3 === 1){ continue; }
    let inList = false;
    for (const ln of escHtml(parts[i]).split('\n')){
      const h = ln.match(/^(#{1,3})\s+(.*)/); const li = ln.match(/^[-•]\s+(.*)/);
      if (h){ if (inList){ html += '</ul>'; inList = false; } html += `<div class="mdh">${inline(h[2])}</div>`; }
      else if (li){ if (!inList){ html += '<ul class="mdul">'; inList = true; } html += `<li>${inline(li[1])}</li>`; }
      else if (!ln.trim()){ if (inList){ html += '</ul>'; inList = false; } }
      else { if (inList){ html += '</ul>'; inList = false; } html += `<p>${inline(ln)}</p>`; }
    }
    if (inList) html += '</ul>';
  }
  return html;
}
"""
rep("/* ================= chat-first view ================= */",
    MD_JS + "\n/* ================= chat-first view ================= */")

# ---- candCard: render the deliverable ----
rep("    <p class=\"small\">${esc(c.summary)}</p>\n    ${dims}",
    "    <p class=\"small\">${esc(c.summary)}</p>\n"
    "    ${c.deliverable ? `<div class=\"deliverable\">${mdRender(c.deliverable)}</div>` : ''}\n"
    "    ${dims}")

# ---- renderThread: type chip + adaptive intro + simulate only for strategy ----
rep("    h += `<div style=\"margin-top:8px\">Done — two strategies, researched and scored. Pick the one you trust, or simulate them in a world built from this research:</div>`;\n"
    "    h += `<div class=\"row\" style=\"margin-top:8px\"><button class=\"small\" onclick=\"chatSimulate('${m.id}')\">🌍 Simulate these strategies</button><span class=\"mut small\">Run cost $${Number(m.spent).toFixed(2)} · cap $${Number(m.budget_limit).toFixed(2)}</span></div>`;",
    "    const tt = (m.contract && m.contract.task_type) || 'strategy';\n"
    "    h += `<div style=\"margin-top:6px\"><span class=\"chip\">${TASK_LABELS[tt] || tt}</span></div>`;\n"
    "    if (tt === 'strategy') {\n"
    "      h += `<div style=\"margin-top:8px\">Done — two strategies, researched and scored. Pick the one you trust, or simulate them in a world built from this research:</div>`;\n"
    "      h += `<div class=\"row\" style=\"margin-top:8px\"><button class=\"small\" onclick=\"chatSimulate('${m.id}')\">🌍 Simulate these strategies</button><span class=\"mut small\">Run cost $${Number(m.spent).toFixed(2)} · cap $${Number(m.budget_limit).toFixed(2)}</span></div>`;\n"
    "    } else {\n"
    "      h += `<div style=\"margin-top:8px\">Done — two takes on your task, drafted by different agents and checked by the critic. Pick the one you trust:</div>`;\n"
    "      h += `<div class=\"mut small\" style=\"margin-top:4px\">Run cost $${Number(m.spent).toFixed(2)} · cap $${Number(m.budget_limit).toFixed(2)}</div>`;\n"
    "    }")

# ---- dashboard candidate card: deliverable too (extracted-line insert) ----
lines = s.split("\n")
idx = [i for i, ln in enumerate(lines) if "Est. cost:" in ln]
assert len(idx) == 1, idx
ins = "      ${c.deliverable ? `<div class=\"deliverable\">${mdRender(c.deliverable)}</div>` : ''}"
lines.insert(idx[0], ins)
s = "\n".join(lines)

p.write_text(s)
print("general-tasks UI pass applied, size:", len(s))
