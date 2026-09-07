# Multiplayer findings

How Chaos Overlords multiplayer actually works, and the Wine bug that made it
look broken. Measured, per SPEC rules 13 and 16 — nothing here is guessed.

## Summary

| Question | Answer |
|---|---|
| Protocol | Plain WinSock TCP. **Not DirectPlay.** |
| Port | **TCP 4269** on the host; the client's source port is ephemeral |
| UDP | none observed |
| Broadcast discovery | none — the joiner types the host's IP |
| Docker bridge networking | **sufficient**; macvlan is not needed |
| Blocker found | Wine never displays the Host/Join dialogs (see below) |

## The game's networking stack

The executable's imports settle it:

```
$ strings -n 5 "Chaos Overlords.exe" | grep -i '\.dll$'
ADVAPI32.dll  comdlg32.dll  DDRAW.dll  GDI32.dll  KERNEL32.dll
smackw32.dll  TAPI32.dll  user32.dll  USER32.dll  WINMM.dll  WSOCK32.dll
```

`WSOCK32.dll` and no `dplayx.dll`: this is ordinary BSD-style sockets, which
Wine implements well. (`TAPI32.dll` is for the modem play the Comm menu also
offers.) That removes the biggest risk the spec anticipated — no DirectPlay, so
no winetricks `directplay` verb, and no DirectPlay port range.

Confirmed on the wire with two containers connected:

```
mp2:  ESTAB  172.18.0.6:42050 -> 172.18.0.5:4269
mp1:  ESTAB  172.18.0.5:4269  <- 172.18.0.6:42050
```

The host binds `0.0.0.0:4269` with a listen backlog of 1 — one joiner at a time
completes a handshake, which is consistent with players joining one by one.

## How to actually start a multiplayer game

This is not obvious, and getting it wrong looks like a broken game.

**1. Comm → WinSock.** The `Comm` menu is a *transport selector*, not a
host/join menu:

```
  Disconnect   (greyed)
  ─────────────
✓ None         <- the shipped default
  WinSock
  Modem
  Direct Connect
```

It ships set to **None** (`commType=0` in `chaosreg.reg`), and while it is None
the `Host Game` and `Join Game` entries in the File menu are greyed out.

**2. File → Host Game… (Ctrl+H)** on one instance. A dialog lists the local IP
addresses; pick one and press OK. The game then listens on TCP 4269 and shows
"Hosting at IP Address".

**3. File → Join Game… (Ctrl+J)** on the other. Type the host's address —
`172.18.0.5`, or the container name if you prefer — and press OK.

Because the joiner types an address, **Docker bridge networking is enough**.
There is no broadcast discovery to break, so the macvlan/ipvlan work sketched in
[networking.md](networking.md) is not needed. SPEC rule 12 says only introduce
it if testing proves it necessary; testing proves it is not.

## The blocker: Wine never showed the Host/Join dialogs

Both hosting and joining appeared to hang the game completely.

**What was actually happening.** A Wine backtrace of the frozen process:

```
=>0 win32u
  1 DIALOG_DoDialogBox+0x68(hwnd=0003009E, owner=00010084)  [user32/dialog.c:790]
  2 DialogBoxParamA+0x8e(hInst=00400000, ..., dlgProc=00465EC6)
  3 chaos overlords (+0x65d2a)
```

The game was inside `DialogBoxParamA`, pumping a modal message loop — correctly.
The dialog window existed, with all its controls:

```
DIALOG_ParseTemplate32 DIALOG 110, 100, 200, 70
DIALOG_ParseTemplate32  STYLE 0x800002c0
DIALOG_GetControl32  L"Button"  L"OK"
DIALOG_GetControl32  L"Button"  L"Cancel"
DIALOG_GetControl32  L"Static"  L"Choose an IP Adress to Host on"
DIALOG_GetControl32  L"ListBox" L""
```

But its X window was `Map State: IsUnMapped`. A modal loop over a window nobody
can see is indistinguishable from a hang: the game stops responding to the menu
bar because the dialog holds input, and there is nothing on screen to click.

**Why.** Wine 10.0, `dlls/user32/dialog.c:704`:

```c
if (template.style & WS_VISIBLE && !(GetWindowLongW( hwnd, GWL_STYLE ) & WS_VISIBLE))
{
   NtUserShowWindow( hwnd, SW_SHOWNORMAL );   /* SW_SHOW doesn't always work */
}
```

Wine shows a modal dialog **only if the template already carries `WS_VISIBLE`**.
This game's template style is `0x800002c0` — `WS_POPUP | DS_SETFOREGROUND |
DS_MODALFRAME | DS_SETFONT`, and no `WS_VISIBLE`. On Windows, `DialogBoxParam`
displays a modal dialog either way; that is the documented difference between
`DialogBox*` (displays) and `CreateDialog*` (does not).

Ruled out along the way, each by testing rather than reasoning:

- **Not DNS.** Every lookup inside the container returns in under a millisecond.
- **Not the network.** The winsock trace shows `gethostname` → `gethostbyname`
  → `inet_addr("172.18.0.4")` all succeeding before the stall.
- **Not DirectDraw fullscreen.** Setting `prefsFullScreen=0` makes the game run
  windowed; the dialog is still unmapped.
- **Not the Wine virtual desktop.** `WINE_VIRTUAL_DESKTOP=false` behaves the same.
- **Not the reported Windows version.** win95, win98 and winxp all identical.

## The fix

`PATCH_DIALOG_VISIBILITY=true` (the default) sets `WS_VISIBLE` on the affected
dialog templates in a **copy of the executable inside the Wine prefix**. The
mounted game files are read-only and are never touched; deleting the `/config`
volume discards the patched copy.

`scripts/patch-dialogs.py` parses the PE resource directory, walks the
`RT_DIALOG` resources, and sets the bit only on top-level templates that lack
it:

```
dialog at file offset 0x08c9c0: style 0x800002c0 -> 0x900002c0
dialog at file offset 0x08ca98: style 0x800002c0 -> 0x900002c0
dialog at file offset 0x08d068: style 0x800002c0 -> 0x900002c0
patch-dialogs: 3 of 27 dialog templates made visible
```

Three of twenty-seven, one bit each. `WS_CHILD` templates (embedded property
pages, which are shown by their parent) are skipped, and re-running on an
already-patched file changes nothing. If patching fails for any reason the
original executable is symlinked in unchanged, so the game still starts.

SPEC section 3 rules out "game patching beyond what is necessary for Wine
compatibility". This is exactly that necessary minimum: without it, the game's
entire multiplayer feature is unreachable under Wine.

Set `PATCH_DIALOG_VISIBILITY=false` to disable it; the container then restores
the unmodified executable on the next start.

## Not yet tested

- A **full multiplayer game played to completion**. What is proven is that the
  host listens, the joiner connects, and the TCP session is established in both
  directions. Turn synchronisation over a whole session is still unverified.
- **More than two players.** The listen backlog of 1 suggests joiners are
  accepted one at a time, but three and four players have not been tried.
- **Reconnection** after a player's browser session drops.
