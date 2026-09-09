#!/usr/bin/env python3
"""Inline this project's CSS into the Selkies page at build time.

Selkies ships a built single-page app; there is no hook for a stylesheet and no
setting for where the picture sits in the window. The page is copied to a temp
directory at start-up, so patching the packaged copy is enough -- the copy
carries the change.

Idempotent, and loud when it cannot do the job: a silent no-op here would show
up much later as "the alignment setting does nothing".
"""
import sys
from pathlib import Path

MARKER = "chaos-ui-css"


def main(index_path: str, css_path: str) -> int:
    index = Path(index_path)
    html = index.read_text(encoding="utf-8")
    if MARKER in html:
        print(f"patch-selkies-ui: already patched, leaving {index} alone")
        return 0

    css = Path(css_path).read_text(encoding="utf-8")
    if "</head>" not in html:
        print(f"patch-selkies-ui: no </head> in {index}; Selkies' page layout "
              "has changed and this patch needs revisiting", file=sys.stderr)
        return 1

    block = f'<style id="{MARKER}">\n{css}</style></head>'
    index.write_text(html.replace("</head>", block, 1), encoding="utf-8")
    print(f"patch-selkies-ui: inlined {len(css)} bytes of CSS into {index}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: patch-selkies-ui.py <index.html> <style.css>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
