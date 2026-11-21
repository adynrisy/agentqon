"""v39 UI contracts — W5 conversation fixes (2026-10-24): the two defects
found by the browser passes on gap-integration @ 5b64a805.

D1: chat code blocks carry their actions. mdRender must render every
fenced block inside an .aq-codeblock wrapper with buttons from the
codeBlockActions allowlist, and the delegated handler for those
buttons must exist. (open-file/run-test need a file path, which a
bare markdown fence does not carry, so they are not offered here.)

D2: the scroll guard tracks the WINDOW, not only #thread. #thread is
a page-flow element (no overflow), so its scroll event never fires
and tracking it alone left aqAtBottom true forever — every reply
dragged the window to the bottom while the reader was at the top.

Static checks on the shipped source, same pattern as v20/v30.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_mdRender_emits_code_block_actions():
    html = _html()
    # mdRender consults the allowlist instead of emitting a bare <pre>
    assert "AQConversation.codeBlockActions" in html
    assert "aq-codeblock" in html
    assert 'data-lang="' in html
    # the button template itself carries a name attribute (v20 sweeps
    # every <button in this file, including template literals)
    assert 'name="code-${a}"' in html
    assert 'data-aq-code-action="${a}"' in html
    assert "Apply patch" in html


def test_code_action_click_handler_exists():
    html = _html()
    assert "data-aq-code-action" in html
    assert "CODE_EXT" in html
    # copy, download and apply-patch all have a branch in the handler
    assert "action === 'copy'" in html
    assert "action === 'download'" in html
    assert "action === 'apply-patch'" in html
    # apply-patch has no direct engine: it goes through the agent's
    # approved tool flow, never a silent no-op
    assert "Apply this patch to the workspace" in html


def test_scroll_guard_tracks_window_scroll():
    html = _html()
    assert "updateAqAtBottom" in html
    assert "window.addEventListener('scroll', updateAqAtBottom)" in html
    assert "window.addEventListener('resize', updateAqAtBottom)" in html
    # the near-bottom test reads the window's scroll position
    assert "window.scrollY" in html
    assert "document.documentElement.scrollHeight" in html
    # the guard still gates only scrollIntoView calls inside #thread
    assert "this.closest('#thread') && !window.aqAtBottom" in html


def test_no_bare_pre_in_mdRender():
    html = _html()
    m = re.search(r"function mdRender\(md\)\{(.*?)\n\}\n", html, re.S)
    assert m, "mdRender not found"
    body = m.group(1)
    assert "aq-codeblock" in body
    # the old split matched closing fences too (empty language) and
    # shifted every later block; fences are scanned in order now
    assert "split(/```" not in body


def test_multiblock_parsing_via_node():
    """Two fenced blocks plus interstitial text must all survive:
    python block, the text between blocks, diff block — and the
    language word must not render as its own code block."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    html = _html()
    m = re.search(r"function mdRender\(md\)\{(.*?)\n\}\n", html, re.S)
    assert m, "mdRender not found"
    src = "function mdRender(md){" + m.group(1) + "\n}\n"
    sample = ("Intro text.\n\n```python\nprint(1)\n```\n\n"
              "And here is the change:\n\n```diff\n--- a/x\n+++ b/x\n```\n\n"
              "Closing words.")
    # bare node has no window: stub the helper's language branch so
    # the allowlist wiring (not just the fallback) is exercised
    stub = ("global.window = { AQConversation: { codeBlockActions: (b) => {"
            " const a = ['copy', 'download'];"
            " if ((b.language || '').toLowerCase() === 'diff' ||"
            " (b.language || '').toLowerCase() === 'patch') a.push('apply-patch');"
            " return a; } } };\n")
    script = (stub + src + "const out = mdRender(" + json.dumps(sample) + ");\n"
              "console.log(out);")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        res = subprocess.run([node, path], capture_output=True, text=True,
                             timeout=30)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert 'data-lang="python"' in out
    assert 'data-lang="diff"' in out
    assert 'data-aq-code-action="apply-patch"' in out
    assert "And here is the change:" in out
    assert "Closing words." in out
    assert "print(1)" in out
    # the language word is metadata, never a rendered code block
    assert "<pre>diff</pre>" not in out
