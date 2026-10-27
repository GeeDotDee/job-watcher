"""Local review page at http://127.0.0.1:8765: each tailored package with its score, gaps, resume and answers.
A person decides what happens next. Nothing on this page applies for you."""

import html
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import settings as S
from . import store
from .filters import is_pm, submit_floor
from .util import fmt_pay

log = logging.getLogger("jobwatch.review")
TOKEN = secrets.token_urlsafe(16)  # blocks other websites from POSTing to this local server
_tailor_busy = threading.Lock()

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1a;--mute:#6b6b66;--line:#e4e4df;--acc:#2f5bea;--good:#127a3a;--warn:#a15c00;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--mute:#a3a29b;--line:#33332f;--acc:#7c9cff;--good:#4ade80;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 20px}
.bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:20px}
.pill{font-size:13px;padding:2px 10px;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
.top{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap}
.title{font-weight:650;font-size:17px}.co{color:var(--mute)}
.facts{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13.5px;color:var(--mute);margin:8px 0}
.score{font-weight:700;font-size:22px;text-align:right}.score small{display:block;font-size:12px;font-weight:400;color:var(--mute)}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.kw{font-size:13px;margin:6px 0}.kw span{display:inline-block;margin:2px 4px 2px 0;padding:1px 8px;border-radius:6px;background:color-mix(in srgb,var(--bad) 12%,transparent)}
details{margin-top:8px}summary{cursor:pointer;color:var(--acc);font-size:14px}
pre{white-space:pre-wrap;font:13px/1.5 ui-monospace,Consolas,monospace;background:var(--bg);padding:10px;border-radius:8px;overflow-x:auto}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
button,.btn{display:inline-block;font:inherit;font-size:14px;line-height:1.4;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);cursor:pointer;text-decoration:none}
"""


def _post_form(action: str, label: str) -> str:
    return (f"<form method=post action='{action}' style='display:inline'>"
            f"<input type=hidden name=t value='{TOKEN}'><button>{label}</button></form>")


def page() -> str:
    with store.db() as con:
        rows = store.by_status(con, "needs_you", "ready", "error", limit=200)
        counts = dict(con.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    out = [f"<title>Job Watcher</title><meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<style>{CSS}</style><main>",
           "<h1>Job Watcher</h1><p class=sub>Tailored packages wait here. Every number on a resume is checked "
           "against the master; you decide what gets sent.</p>",
           "<div class=bar>"
           f"<span class=pill>{counts.get('candidate', 0)} matches waiting to be tailored</span>"
           f"<span class=pill>{counts.get('applied', 0)} applied</span>"
           f"<span class=pill>{counts.get('rejected', 0)} filtered out</span>"
           + (_post_form("/tailor", "Tailor next 5 now") if counts.get("candidate") else "") + "</div>"]
    if not rows:
        out.append("<div class=card>No packages yet. New matches show up here after each scan.</div>")
    out += [_card(r) for r in rows]
    out.append("</main>")
    return "".join(out)


def _card(row: dict) -> str:
    j, jid = row["job"], row["id"]
    e = html.escape
    if row["status"] == "error":
        return (f"<div class=card><div class=title>{e(j['title'])}</div><div class=co>{e(j['company'])}</div>"
                f"<p class=bad>Tailoring failed: {e(row['reason'])}</p><div class=actions>"
                f"<a class=btn href='{e(j['url'])}' target=_blank rel=noopener>Open posting</a>"
                + _post_form(f"/retry/{jid}", "Retry") + _post_form(f"/skip/{jid}", "Skip") + "</div></div>")
    pkg = Path(row["package"])
    meta = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    m = meta["match"]
    floor = submit_floor(j["title"])
    cls = "good" if m["match_pct"] >= m.get("target", S.TARGET_MATCH) else "warn" if m["match_pct"] >= floor else "bad"
    cannot = [k for k in m.get("cannot_add", []) if k]
    missing = [k for k in m["missing"] if k not in cannot]
    issues = meta.get("consistency", {}).get("issues", [])
    state = {"needs_you": f"<b class=bad>Needs you: {e(row.get('reason', ''))}</b>"}.get(row["status"], "")
    if row["status"] == "ready":
        state = ("<b class=good>Clean: match at the floor, package agrees with the master resume</b>" if row["clean"]
                 else f"<b class=warn>Not clean: {e('; '.join(issues) or f'match under {floor}%')}</b>")
    answers = "".join(f"<p><b>{e(k.replace('_', ' ').title())}</b><br>{e(v)}</p>"
                      for k, v in meta.get("answers", {}).items())
    return (
        f"<div class=card><div class=top><div><div class=title>{e(j['title'])}"
        + (" <span class=pill>PM</span>" if is_pm(j["title"]) else "") + "</div>"
        f"<div class=co>{e(j['company'])} · {e(j.get('location', ''))}</div></div>"
        f"<div class='score {cls}'>{m['match_pct']}%<small>keyword match · floor {floor}%</small></div></div>"
        f"<div class=facts><span>{fmt_pay(j.get('salary_min'), j.get('salary_max')) or 'pay not listed'}</span>"
        + (f"<span>posted {e(j['posted_at'][:10])}</span>" if j.get("posted_at") else "")
        + f"<span>{e(j['source'])}</span><span>base: {e(meta['base'])}</span></div>"
        f"<div>{e(meta.get('fit_summary', ''))}</div>"
        + (f"<div class=kw>Missing keywords: {''.join(f'<span>{e(k)}</span>' for k in missing[:15])}</div>"
           if missing else "")
        + (f"<div class=kw>Not added because they aren't true (add any that ARE to extra_skills.txt, then "
           f"re-tailor): {''.join(f'<span>{e(k)}</span>' for k in cannot[:15])}</div>" if cannot else "")
        + (f"<div class=kw>Gaps vs. posting: {e('; '.join(meta.get('gaps', [])[:5]))}</div>" if meta.get("gaps") else "")
        + f"<div>{state}</div>"
        f"<details><summary>Written answers</summary>{answers}</details>"
        f"<details><summary>Resume text</summary><pre>{e((pkg / 'resume.txt').read_text(encoding='utf-8'))}</pre></details>"
        "<div class=actions>"
        f"<a class=btn href='/pdf/{e(jid)}' target=_blank>Resume PDF</a>"
        f"<a class=btn href='{e(j['url'])}' target=_blank rel=noopener>Posting</a>"
        + _post_form(f"/retry/{jid}", "Re-tailor")
        + _post_form(f"/applied/{jid}", "I applied")
        + _post_form(f"/skip/{jid}", "Skip") + "</div></div>"
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, page())
        if path.startswith("/pdf/"):
            with store.db() as con:
                row = store.get(con, path[5:])
            if row and row["package"]:
                pdf = Path(row["package"]) / S.resume_filename()
                if pdf.exists():
                    return self._send(200, pdf.read_bytes(), "application/pdf")
        self._send(404, "not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(n).decode())
        if form.get("t", [""])[0] != TOKEN:
            return self._send(403, "bad token, reload the page")
        action, _, jid = urlparse(self.path).path.strip("/").partition("/")
        with store.db() as con:
            if action == "skip":
                store.update(con, jid, status="skipped")
            elif action == "applied":
                store.update(con, jid, status="applied")
            elif action == "retry":
                store.update(con, jid, status="candidate", reason="", priority=9999)
                threading.Thread(target=_tailor_now, daemon=True).start()
            elif action == "tailor":
                threading.Thread(target=_tailor_now, daemon=True).start()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def _tailor_now():
    if not _tailor_busy.acquire(blocking=False):
        return
    try:
        from . import pipeline
        pipeline.tailor_batch(5)
    finally:
        _tailor_busy.release()


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", S.REVIEW_PORT), Handler)
    log.info(f"Review page: http://127.0.0.1:{S.REVIEW_PORT}/")
    srv.serve_forever()
