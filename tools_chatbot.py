"""Second pass: make the chat view a true chatbot layout —
left conversation rail, slim header-less chat, pill composer, options tucked away."""
import pathlib

p = pathlib.Path("apps/web/index.html")
s = p.read_text()

def rep(old, new, n=1):
    global s
    c = s.count(old)
    assert c == n, f"anchor count {c} != {n}: {old[:70]!r}"
    s = s.replace(old, new)

# ---------- CSS ----------
CSS2 = """
/* ---- chatbot layout pass ---- */
body[data-view="chat"] header{display:none}
#chatView{display:flex;gap:18px;align-items:flex-start;max-width:1180px}
#rail{width:252px;flex:none;position:sticky;top:12px;background:#11151f;border:1px solid #232c44;border-radius:14px;padding:14px}
.railbrand{display:flex;align-items:center;gap:9px;font-size:17px;margin-bottom:12px}
#newChatBtn{width:100%;border-radius:10px}
#recentRow{display:block;overflow:visible;padding:4px 0}
#recentRow .rchip{display:block;width:100%;text-align:left;margin:0 0 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#chatMain{flex:1;min-width:0;max-width:800px;margin:0 auto;width:100%}
.msg.ai{background:transparent;border:none;padding:6px 2px}
.msg.ai .who{display:flex;align-items:center;gap:7px}
.msg.ai .who:before{content:"Ω";display:inline-flex;width:20px;height:20px;border-radius:50%;background:linear-gradient(135deg,#7c5cff,#00c2a8);color:#fff;align-items:center;justify-content:center;font-size:12px}
#composer{border-radius:20px;display:block}
.composerRow{display:flex;gap:8px;align-items:flex-end}
#chatInput{flex:1}
#chatSend{border-radius:50%;width:44px;height:44px;flex:none;font-size:17px;padding:0}
#optPanel{display:none;margin-top:8px;border-top:1px solid #232c44;padding-top:8px}
#optBtn{background:none;border:none;color:#8a93a8;cursor:pointer;font-size:12px;padding:2px 0}
@media (max-width:760px){
  #chatView{flex-direction:column}
  #rail{width:100%;position:static}
  #recentRow{display:flex;gap:8px;overflow-x:auto}
  #recentRow .rchip{width:auto;flex:none;margin:0}
}
"""
rep("</style>", CSS2 + "</style>")

# ---------- markup: rail + chatMain wrappers, pill composer ----------
OLD_OPEN = '<section id="chatView">\n  <div id="welcome">'
NEW_OPEN = '''<section id="chatView">
 <div id="rail">
   <div class="railbrand"><div class="logo" style="width:30px;height:30px;font-size:16px">Ω</div><b>AgentQon</b></div>
   <button id="newChatBtn">＋ New chat</button>
   <div class="mut small" style="margin:12px 2px 4px">Recent conversations</div>
   <div id="recentRow" class="recent"></div>
   <button class="secondary small" id="railAdvBtn" style="width:100%;margin-top:10px">⚙ Advanced dashboard</button>
   <div class="mut small" style="margin-top:6px">Connections, models, simulations &amp; members live there.</div>
 </div>
 <div id="chatMain">
  <div id="welcome">'''
rep(OLD_OPEN, NEW_OPEN)

rep('  <div id="recentRow" class="recent"></div>\n  <div id="thread"></div>', '  <div id="thread"></div>')

OLD_COMPOSER = '''  <div id="composer">
    <textarea id="chatInput" rows="2" placeholder="Describe what you want to figure out…"></textarea>
    <div class="crow">
      <label class="small mut">Budget <select id="chatBudget"><option value="5">$5</option><option value="10" selected>$10</option><option value="25">$25</option></select></label>
      <label class="small mut">Privacy <select id="chatClass"><option value="internal">Internal · web research on</option><option value="public">Public · web research on</option><option value="confidential">Confidential · no web search</option></select></label>
      <span style="flex:1"></span>
      <button id="chatSend">Ask AgentQon ➤</button>
    </div>
    <div class="mut small" style="margin-top:4px">Enter sends · Shift+Enter for a new line · Power features (connections, models, simulations, members) live under <b>⚙ Advanced</b>.</div>
  </div>
</section>'''
NEW_COMPOSER = '''  <div id="composer">
    <div class="composerRow">
      <textarea id="chatInput" rows="2" placeholder="Message AgentQon…"></textarea>
      <button id="chatSend" title="Send">➤</button>
    </div>
    <button id="optBtn">＋ Options (budget, privacy)</button>
    <div id="optPanel">
      <div class="crow">
        <label class="small mut">Budget <select id="chatBudget"><option value="5">$5</option><option value="10" selected>$10</option><option value="25">$25</option></select></label>
        <label class="small mut">Privacy <select id="chatClass"><option value="internal">Internal · web research on</option><option value="public">Public · web research on</option><option value="confidential">Confidential · no web search</option></select></label>
      </div>
      <div class="mut small">Enter sends · Shift+Enter for a new line.</div>
    </div>
  </div>
 </div>
</section>'''
rep(OLD_COMPOSER, NEW_COMPOSER)

# welcome copy: shorter, chattier
rep("Ask anything that needs research and a decision. AgentQon searches the live web, two agents draft competing strategies, a critic attacks the evidence, and everything is scored before you choose. No forms, no jargon.",
    "Just tell me what you're trying to figure out. I'll research the live web, draft two competing strategies, have a critic check the evidence, and score them — you just pick the winner.")

# ---------- JS: new chat + rail buttons + options toggle ----------
rep("$('viewChatBtn').onclick = () => setView('chat');",
    """$('viewChatBtn').onclick = () => setView('chat');
$('railAdvBtn').onclick = () => setView('dash');
$('optBtn').onclick = () => { const p = $('optPanel'); p.style.display = p.style.display === 'block' ? 'none' : 'block'; };
$('newChatBtn').onclick = () => { window._chatMission = null; $('thread').innerHTML = ''; $('welcome').style.display = 'block'; $('chatInput').value = ''; $('chatInput').focus(); window.scrollTo({top:0, behavior:'smooth'}); };""")

p.write_text(s)
print("chatbot pass applied, size:", len(s))
