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
/* Owner names are chosen by players, so they are shown as typed rather than
   upper-cased like the status badges. */
.owner   { color:#8fb8ff; border-color:#33507d; text-transform:none;
           letter-spacing:0; }
.owner.unowned { color:#7d848f; border-color:#333a44; font-style:italic; }
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


def session_url(s, cfg) -> str:
    """Where a player points their browser.

    With a wildcard domain each session has a hostname of its own, which is an
    absolute URL and cannot be a relative link -- it is a different origin.
    Without one, sessions share the manager's hostname under /s/<id>/.
    """
    domain = cfg.get("session_domain", "")
    base = cfg.get("public_url", "")
    if domain:
        scheme = "http" if base.startswith("http://") else "https"
        return f"{scheme}://{s.id}.{domain}/"
    return f"{base}{s.path}/" if base else f"{s.path}/"


def _session_card(s, cfg, is_new: bool, show_owner: bool = False) -> str:
    e = html.escape
    idle = int(time.time() - s.last_seen)
    url = session_url(s, cfg)

    # Who a session belongs to, for the admin only -- a guest sees nothing but
    # their own sessions, so the answer would always be "you". A session made
    # from the admin page has no owner: nobody claimed it, and only the admin
    # can open it. That is a different thing from a claimed name, so it reads
    # differently rather than being dressed up as an owner called "admin".
    owner_badge = ""
    if show_owner:
        if s.owner:
            owner_badge = (f'<span class="badge owner" title="This session belongs to '
                           f'{e(s.owner)}; they open it with their own login.">'
                           f'{e(s.owner)}</span>')
        else:
            owner_badge = ('<span class="badge owner unowned" title="Created from this '
                           'admin page, so no player owns it. Anyone opening it needs '
                           'the share password below.">no owner</span>')

    if s.active_conns:
        meta = f"{s.active_conns} viewer{'s' if s.active_conns != 1 else ''} connected"
    elif s.status == "stopped":
        meta = f"stopped &middot; save kept &middot; idle {_ago(idle)}"
    else:
        meta = f"idle {_ago(idle)}"

    actions = [f'<a class="btn" href="{e(url)}" target="_blank">Open</a>']
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
            <span class="badge {s.status}">{s.status}</span>{owner_badge}</div>
          <div class="meta">{meta}</div>
        </div>
        <div>{' '.join(actions)}</div>
      </div>
      <div class="row" style="margin-top:.8rem">
        <div class="grow"><div class="meta">Link</div><code>{e(url)}</code></div>
        <div><div class="meta">Username</div>
             <code class="pw">{e(cfg.get("web_user", "player"))}</code></div>
        <div><div class="meta">Password <span title="Only needed by someone signing in without an account of their own; you do not need it.">(to share)</span></div>
             <code class="pw">{e(s.password)}</code></div>
      </div>
    </div>"""


def render_page(sessions, cfg, new_id: str = "", role: str = "admin",
                who: str = "", accounts: list | None = None) -> str:
    items = sorted(sessions, key=lambda s: s.created, reverse=True)
    is_admin = role == "admin"
    cards = "".join(_session_card(s, cfg, s.id == new_id, show_owner=is_admin)
                    for s in items) or \
        '<div class="empty">No sessions yet. Start one below.</div>'
    hours = cfg.get("retention_hours", 0)
    retention_note = ("Saves are kept indefinitely &mdash; nothing is deleted "
                      "unless you delete it." if not hours else
                      f"Saves are kept for {hours} hours after that.")
    accounts = accounts or []

    # A player who has just claimed a name can do exactly one thing.
    if role == "claiming":
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Choose a password</title><style>{STYLE}</style></head>
<body><div class="wrap">
  <h1><span>Chaos</span> Overlords</h1>
  <p class="sub">The name <strong>{html.escape(who)}</strong> is yours. Choose a
     password for it. From now on this is how you sign in &mdash; the shared
     invite password will not open your games again.</p>
  <div class="card">
    <form method="post" action="/api/account/password">
      <div class="row">
        <input type="password" name="password" placeholder="New password"
               minlength="6" required>
        <input type="password" name="confirm" placeholder="Repeat it"
               minlength="6" required>
        <button class="primary" type="submit">Claim {html.escape(who)}</button>
      </div>
    </form>
  </div>
  <p class="note">At least six characters. Your browser will ask you to sign in
     again straight afterwards, with the password you just chose.</p>
</div></body></html>"""
    intro = ("One container per player, opened by whoever owns it."
             if is_admin else "Start a game, and it is yours alone.")
    whoami = ("" if is_admin else
              f'<br>Signed in as <strong>{html.escape(who)}</strong>.')
    label_field = ('<input type="text" name="label" placeholder="Player name (optional)" '
                   'maxlength="40">' if is_admin else "")
    footer = (
        "<strong>Open</strong> takes you straight in &mdash; your login here is "
        "enough for any session you own. The username and password above are for "
        "<em>someone else</em>: send all three and they can take that seat without "
        "an account of their own.<br>Closing the tab leaves the game running for a "
        "few more minutes, then it stops and the save is kept &mdash; opening the "
        "link again brings it straight back."
        if is_admin else
        "<strong>Open</strong> takes you straight to your game &mdash; you are "
        "already signed in, so it asks for nothing. The username and password above "
        "are only for handing this seat to someone else; send them all three and "
        "they need no account.<br>Closing the tab leaves it running for a few more "
        "minutes, then it stops and your save is kept &mdash; come back to the same "
        "link any time.")
    if is_admin:
        rows = "".join(
            f'<div class="row" style="margin-top:.4rem"><div class="grow">'
            f'<code>{html.escape(n)}</code></div>'
            f'<form class="inline" method="post" action="/api/accounts/{html.escape(n)}/release" '
            f'onsubmit="return confirm(\'Release {html.escape(n)}? They can claim it again '
            f'with the invite password, and keep their games.\')">'
            f'<button>Release</button></form></div>' for n in accounts)
        extra = (f'<div class="card" style="margin-top:1.5rem">'
                 f'<div class="name">Player accounts</div>'
                 f'<div class="meta">A name is claimed on first sign-in and then needs its '
                 f'own password. Release one if a player forgets theirs &mdash; their games '
                 f'are kept.</div>{rows or "<div class=meta>None claimed yet.</div>"}</div>'
                 if accounts or True else "")
    else:
        extra = (
            '<div class="card" style="margin-top:1.5rem">'
            '<div class="name">Change your password</div>'
            '<form method="post" action="/api/account/password">'
            '<div class="row" style="margin-top:.6rem">'
            '<input type="password" name="current" placeholder="Current password" required>'
            '<input type="password" name="password" placeholder="New password" '
            'minlength="6" required>'
            '<input type="password" name="confirm" placeholder="Repeat it" '
            'minlength="6" required>'
            '<button type="submit">Change</button></div></form></div>')
    starting = any(s.status == "starting" for s in items)
    refresh = '<meta http-equiv="refresh" content="5">' if starting else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chaos Overlords sessions</title>{refresh}
<style>{STYLE}</style></head>
<body><div class="wrap">
  <h1><span>Chaos</span> Overlords &mdash; sessions</h1>
  <p class="sub">{intro} Each session is shut down after
     {cfg['idle_minutes']} minutes with nobody watching. {retention_note}
     {whoami}</p>

  {cards}

  <form method="post" action="/api/sessions" style="margin-top:1.5rem">
    <div class="row">
      {label_field}
      <button class="primary" type="submit">New session</button>
    </div>
  </form>

  {extra}

  <p class="note">{footer}</p>
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
