#!/usr/bin/env python3
"""Add WS_VISIBLE to modal dialog templates in a PE binary.

Wine's user32 shows a DialogBoxParam dialog only when the template already
carries WS_VISIBLE (dlls/user32/dialog.c: "if (template.style & WS_VISIBLE ...)
NtUserShowWindow"). Windows shows a modal dialog either way. A template without
the bit therefore leaves the application in a modal message loop over a window
that was never mapped -- indistinguishable from a hang.

This rewrites RT_DIALOG resources in place so Wine shows them.
"""
import struct, sys

WS_VISIBLE, WS_POPUP, WS_CHILD = 0x10000000, 0x80000000, 0x40000000
RT_DIALOG = 5

def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]

def sections(data):
    pe = u32(data, 0x3C)
    assert data[pe:pe+4] == b"PE\0\0", "not a PE file"
    nsec = u16(data, pe + 6)
    opt = u16(data, pe + 20)
    base = pe + 24 + opt
    out = []
    for i in range(nsec):
        o = base + i * 40
        out.append((data[o:o+8].rstrip(b"\0").decode("latin1"),
                    u32(data, o + 12),        # VirtualAddress
                    u32(data, o + 16),        # SizeOfRawData
                    u32(data, o + 20)))       # PointerToRawData
    return out

def rva_to_off(secs, rva):
    for _, va, size, ptr in secs:
        if va <= rva < va + size:
            return ptr + (rva - va)
    return None

def walk(data, secs, root_off, off, level, want_type, found):
    """Depth-first walk of the resource directory; collect RT_DIALOG data entries."""
    nnamed, nid = u16(data, off + 12), u16(data, off + 14)
    for i in range(nnamed + nid):
        e = off + 16 + i * 8
        name, entry = u32(data, e), u32(data, e + 4)
        if level == 0:
            if name & 0x80000000 or name != want_type:
                continue
        if entry & 0x80000000:
            walk(data, secs, root_off, root_off + (entry & 0x7FFFFFFF),
                 level + 1, want_type, found)
        else:
            data_rva, size = u32(data, root_off + entry), u32(data, root_off + entry + 4)
            o = rva_to_off(secs, data_rva)
            if o is not None:
                found.append((o, size))

def patch(path, out_path):
    data = bytearray(open(path, "rb").read())
    secs = sections(data)
    rsrc = next((s for s in secs if s[0] == ".rsrc"), None)
    assert rsrc, "no .rsrc section"
    root = rsrc[3]
    found = []
    walk(data, secs, root, root, 0, RT_DIALOG, found)

    changed = []
    for off, size in found:
        style = u32(data, off)
        # DLGTEMPLATEEX begins with dlgVer=1, signature=0xFFFF; its style is at +12.
        is_ex = u16(data, off) == 1 and u16(data, off + 2) == 0xFFFF
        soff = off + 12 if is_ex else off
        style = u32(data, soff)
        if style & WS_CHILD:            # embedded page, not a top-level dialog
            continue
        if style & WS_VISIBLE:
            continue
        struct.pack_into("<I", data, soff, style | WS_VISIBLE)
        changed.append((off, style, style | WS_VISIBLE))

    open(out_path, "wb").write(data)
    return found, changed

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: patch-dialogs.py <in.exe> <out.exe>", file=sys.stderr)
        sys.exit(2)
    try:
        found, changed = patch(sys.argv[1], sys.argv[2])
    except Exception as exc:                      # noqa: BLE001 - report and let the caller fall back
        print(f"patch-dialogs: {exc}", file=sys.stderr)
        sys.exit(1)
    for off, old, new in changed:
        print(f"  dialog at file offset 0x{off:06x}: style 0x{old:08x} -> 0x{new:08x}")
    print(f"patch-dialogs: {len(changed)} of {len(found)} dialog templates made visible")
