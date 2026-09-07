"""The landing page: start a session, read off its password, hand it to a friend."""
from __future__ import annotations

import html
import time

STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:#0d0f12; color:#d7dae0;
       font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
.wrap { max-width:60rem; margin:0 auto; }
h1 { font-size:1.4rem; margin:0 0 .25rem; letter-spacing:.01em; }
h1 span { color:#c1121f; }
.sub { color:#7d848f; margin:0 0 2rem; font-size:.9rem; }
.card { background:#14171c; border:1px solid #232830; border-radius:10px;
        padding:1rem 1.1rem; margin-bottom:.85rem; }
.new { background:#132018; border-color:#2c6b45; }
.row { display:flex; align-items:center; gap:1rem; flex-wrap:wrap; }
.grow { flex:1 1 14rem; min-width:0; }
.name { font-weight:600; }
.meta { color:#7d848f; font-size:.82rem; margin-top:.15rem; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; background:#0a0c0f;
       border:1px solid #232830; border-radius:5px; padding:.15rem .4rem; font-size:.9em; }
.pw { font-size:1.05rem; letter-spacing:.04em; }
.badge { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em;
         padding:.2rem .5rem; border-radius:99px; border:1px solid; }
.ready   { color:#5dd39e; border-color:#2c6b45; }
.starting{ color:#e8c468; border-color:#6b5a2c; }
.stopped { color:#7d848f; border-color:#333a44; }
a.btn, button { font:inherit; font-size:.88rem; padding:.42rem .85rem; border-radius:7px;
        border:1px solid #2e3540; background:#1b2028; color:#d7dae0; cursor:pointer;
        text-decoration:none; display:inline-block; }
a.btn:hover, button:hover { background:#232933; }
.primary { background:#c1121f; border-color:#c1121f; color:#fff; font-weight:600; }
.primary:hover { background:#a50f1a; }
.danger:hover { border-color:#a33; color:#f19; }
form.inline { display:inline; }
input[type=text] { font:inherit; padding:.45rem .6rem; border-radius:7px;
        border:1px solid #2e3540; background:#0a0c0f; color:#d7dae0; }
.empty { color:#7d848f; padding:2rem 0; text-align:center; }
.note { color:#7d848f; font-size:.82rem; margin-top:2rem; border-top:1px solid #232830;
        padding-top:1rem; }
"""


def _ago(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _session_card(s, cfg, is_new: bool) -> str:
    e = html.escape
    idle = int(time.time() - s.last_seen)
    base = cfg["public_url"]
    url = f"{base}{s.path}/" if base else f"{s.path}/"

    if s.active_conns:
        meta = f"{s.active_conns} viewer{'s' if s.active_conns != 1 else ''} connected"
    elif s.status == "stopped":
        meta = f"stopped &middot; save kept &middot; idle {_ago(idle)}"
    else:
        meta = f"idle {_ago(idle)}"

    actions = [f'<a class="btn" href="{e(s.path)}/" target="_blank">Open</a>']
    if s.status == "stopped":
        actions.append(f'<form class="inline" method="post" action="/api/sessions/{e(s.id)}/resume">'
                       f'<button>Resume</button></form>')
    else:
        actions.append(f'<form class="inline" method="post" action="/api/sessions/{e(s.id)}/stop">'
                       f'<button>Stop</button></form>')
    actions.append(
        f'<form class="inline" method="post" action="/api/sessions/{e(s.id)}/delete" '
        f'onsubmit="return confirm(\'Delete this session and its saves permanently?\')">'
        f'<button class="danger">Delete</button></form>')

    return f"""
    <div class="card{' new' if is_new else ''}">
      <div class="row">
        <div class="grow">
          <div class="name">{e(s.label or 'Session ' + s.id)}
            <span class="badge {s.status}">{s.status}</span></div>
          <div class="meta">{meta}</div>
        </div>
        <div>{' '.join(actions)}</div>
      </div>
      <div class="row" style="margin-top:.8rem">
        <div class="grow"><div class="meta">Link</div><code>{e(url)}</code></div>
        <div><div class="meta">Password</div>
             <code class="pw">{e(s.password)}</code></div>
      </div>
    </div>"""


def render_page(sessions, cfg, new_id: str = "") -> str:
    items = sorted(sessions, key=lambda s: s.created, reverse=True)
    cards = "".join(_session_card(s, cfg, s.id == new_id) for s in items) or \
        '<div class="empty">No sessions yet. Start one below.</div>'
    hours = cfg.get("retention_hours", 0)
    retention_note = ("Saves are kept indefinitely &mdash; nothing is deleted "
                      "unless you delete it." if not hours else
                      f"Saves are kept for {hours} hours after that.")
    starting = any(s.status == "starting" for s in items)
    refresh = '<meta http-equiv="refresh" content="5">' if starting else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chaos Overlords sessions</title>{refresh}
<style>{STYLE}</style></head>
<body><div class="wrap">
  <h1><span>Chaos</span> Overlords &mdash; sessions</h1>
  <p class="sub">One container per player. Each gets its own password, and is
     shut down after {cfg['idle_minutes']} minutes with nobody watching.
     {retention_note}</p>

  {cards}

  <form method="post" action="/api/sessions" style="margin-top:1.5rem">
    <div class="row">
      <input type="text" name="label" placeholder="Player name (optional)" maxlength="40">
      <button class="primary" type="submit">New session</button>
    </div>
  </form>

  <p class="note">Send a player their link and password. Closing the tab leaves
     the game running for a few more minutes, then it stops and the save is kept
     &mdash; opening the link again brings it straight back.</p>
</div></body></html>"""


def waiting_page(label: str, seconds: int = 4) -> str:
    """Shown while a session's container is still coming up.

    A player following their link to an idle session would otherwise get a bare
    502 from the proxy: the container is starting, and Wine takes a moment.
    """
    import html as _h
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{seconds}">
<title>Starting {_h.escape(label)}</title>
<style>{STYLE}
.center {{ min-height:70vh; display:flex; flex-direction:column; align-items:center;
          justify-content:center; text-align:center; gap:.6rem; }}
.dot {{ width:9px; height:9px; border-radius:50%; background:#c1121f;
       animation:p 1.1s ease-in-out infinite; }}
@keyframes p {{ 0%,100%{{opacity:.25}} 50%{{opacity:1}} }}
</style></head>
<body><div class="wrap"><div class="center">
  <div class="dot"></div>
  <h1>Starting your game</h1>
  <p class="sub">{_h.escape(label)} is waking up. This takes a few seconds &mdash;
     your saves are still there. This page will load it for you.</p>
</div></div></body></html>"""
