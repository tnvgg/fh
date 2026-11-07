"""Local read-only dashboard for the regression vault."""
import html
import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from flakehunt import db

app = FastAPI(title="FlakeHunt Dashboard")

CSS = """
:root{color-scheme:dark}body{margin:0;background:#0f1117;color:#e6e8ee;font:15px/1.5 system-ui,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px}h1{margin:0;font-size:30px;
background:linear-gradient(90deg,#60a5fa,#34d399);-webkit-background-clip:text;color:transparent}
.sub{color:#8b93a7;margin:4px 0 28px}.card{background:#171a23;border:1px solid #272b38;border-radius:12px;
padding:20px;margin-bottom:20px}.row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.node{font:600 16px ui-monospace,monospace;word-break:break-all}.meta{color:#8b93a7;font-size:12px}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;
border:1px solid #3b4256;background:#1f2430;margin-right:6px}.ok{color:#34d399;border-color:#1d6b50}
.bad{color:#f87171;border-color:#7a2c2c}h3{font-size:12px;letter-spacing:.08em;text-transform:uppercase;
color:#9aa3b8;margin:18px 0 6px}pre{background:#0b0d12;border:1px solid #232735;border-radius:8px;padding:12px;
overflow-x:auto;font-size:12px;margin:0;white-space:pre-wrap}.add{color:#34d399}.del{color:#f87171}
code{font-family:ui-monospace,monospace}
"""


def _diff_html(diff):
    out = []
    for line in diff.splitlines():
        cls = "add" if line.startswith("+") and not line.startswith("+++") else \
              "del" if line.startswith("-") and not line.startswith("---") else ""
        out.append('<span class="%s">%s</span>' % (cls, html.escape(line)))
    return "\n".join(out)


def render(failures):
    e = html.escape
    parts = ['<!doctype html><html><head><meta charset="utf-8"><title>FlakeHunt Vault</title>'
             "<style>%s</style></head><body><div class=\"wrap\"><h1>FlakeHunt Vault</h1>"
             '<p class="sub">Flaky tests found, shrunk to a minimal reproducer and replayable with one command.</p>' % CSS]
    if not failures:
        parts.append('<div class="card">No flaky failures saved yet. Run <code>flakehunt scan</code>.</div>')
    for f in failures:
        order = json.loads(f["min_order"] or "[]")
        fixed = ""
        if f.get("fix_diff"):
            fixed = ('<h3>Verified fix</h3><span class="badge ok">verified: reproducer and shuffled runs pass</span>'
                     "<pre>%s</pre>" % _diff_html(f["fix_diff"]))
        ai_text = f.get("ai_explanation") or "No model analysis saved (set BHARATCODE_API_KEY and re-scan)."
        parts.append(
            '<div class="card"><div class="row"><div><span class="badge bad">%s</span>'
            '<span class="badge">failed in %s runs</span><span class="badge">reproduces %s</span>'
            '<div class="node">%s</div></div><div class="meta">#%s &middot; %s &middot; %s profile</div></div>'
            "<h3>Minimal reproducer</h3><pre>%s</pre><h3>Replay</h3><pre>flakehunt replay %s</pre>"
            "<h3>Traceback</h3><pre>%s</pre><h3>Model analysis</h3><pre>%s</pre>%s</div>" % (
                e(f.get("kind") or "FLAKY"), e(str(f.get("fail_runs") or "?")), e(f.get("repro_rate") or "?"),
                e(f["nodeid"]), f["id"], e(str(f["timestamp"])), e((f.get("profile") or "").upper()),
                e("\n".join("%d. %s" % (i + 1, n) for i, n in enumerate(order))), f["id"],
                e(f.get("traceback") or ""), e(ai_text), fixed))
    parts.append("</div></body></html>")
    return "".join(parts)


@app.get("/", response_class=HTMLResponse)
def index():
    db.init_db()
    return render(db.list_failures())


@app.get("/api/failures")
def api_failures():
    db.init_db()
    return JSONResponse(db.list_failures())
