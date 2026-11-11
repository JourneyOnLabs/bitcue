"""A picture of the finished part, drawn from its job file: the part seen from above in bronze, lit from the
top left, so it is easy to tell one job from another on the job list.

Each cutting move is followed through a grid of small squares, remembering how deep the bits went in each one: a
flat end mill across its whole width, a V-bit wider the deeper it cuts (from the job file's ;@MKR|TOOL lines). What
lies outside a cut-through outline is left out, so the picture shows the part, not the plate around it. The
holding tabs a cut-out keeps (cut part of the way through) count as part of the outline, so they don't let the
outside in, and small pieces cut loose (the middle of a hole cut as a ring) are left out, as they fall out.

  png = render(data)           PNG bytes (transparent around the part), or None if the file cuts nothing

Needs Pillow; without it (a Pi set up for a monitor only) there is no picture, and nothing else changes.
"""

from __future__ import annotations

import io
import math
import re

try:
    from PIL import Image
except ImportError:          # pragma: no cover
    Image = None

SIZE = 240                   # the stored picture, in pixels (the screens scale it down)
TAB_MM = 6.0                 # gaps in a cut-out path up to this wide are taken for holding tabs
BRONZE = (150, 98, 52)
LIGHT = (-0.55, 0.55, 0.63)  # from the top left, a little above
_WORD = re.compile(rb"([A-Za-z])\s*([-+]?\d*\.?\d+)")
_FIELD = re.compile(r"([a-zA-Z]+)=([^|]*)")


def available() -> bool:
    return Image is not None


def _tool_lines(line: bytes, tools: dict) -> None:
    kv = dict(_FIELD.findall(line.decode("utf-8", "replace").strip()))
    try:
        tools[int(kv["number"])] = {"d": float(kv.get("diameter") or 3.175), "tip": float(kv.get("tipdiameter") or 0),
                                    "angle": float(kv.get("angle") or 0)}
    except (KeyError, ValueError):
        pass


class _Cuts:
    """How deep the bits went in each square of the grid."""

    def __init__(self, cell: float) -> None:
        self.cell = cell
        self.depth = {}
        self._disks = {}

    def _disk(self, n: int) -> list:
        d = self._disks.get(n)
        if d is None:
            d = [(i, j) for i in range(-n, n + 1) for j in range(-n, n + 1) if i * i + j * j <= n * n + n]
            self._disks[n] = d
        return d

    def radius(self, spec, z: float) -> float:
        if spec is None:
            return 0.0
        if spec["angle"] > 0:
            return min(spec["d"] / 2, spec["tip"] / 2 + (-z) * math.tan(math.radians(spec["angle"] / 2)))
        return spec["d"] / 2

    def move(self, a, b, spec) -> None:
        (x0, y0, z0), (x1, y1, z1) = a, b
        if z0 >= 0 and z1 >= 0:
            return
        rmax = self.radius(spec, min(z0, z1))
        step = max(self.cell * 0.8, min(rmax * 0.5, 1.0))
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        depth, cell = self.depth, self.cell
        for s in range(n + 1):
            f = s / n
            z = z0 + (z1 - z0) * f
            if z >= 0:
                continue
            ci, cj = int(round((x0 + (x1 - x0) * f) / cell)), int(round((y0 + (y1 - y0) * f) / cell))
            for di, dj in self._disk(int(self.radius(spec, z) / cell)):
                k = (ci + di, cj + dj)
                if z < depth.get(k, 0.0):
                    depth[k] = z


def _cuts(data: bytes):
    """Follows the job's moves. Returns (cuts, stock thickness or None)."""
    tools, stock_t, bounds = {}, None, None
    for raw in data.split(b"\n", 400)[:400]:          # the ;@MKR header comes first
        if raw.startswith(b";@MKR|TOOL|"):
            _tool_lines(raw, tools)
        elif raw.startswith(b";@MKR|STOCK|"):
            m = re.search(rb"height=([\d.]+)", raw)
            stock_t = float(m.group(1)) if m else None
            m1, m2 = re.search(rb"length=([\d.]+)", raw), re.search(rb"width=([\d.]+)", raw)
            if m1 and m2:
                bounds = max(float(m1.group(1)), float(m2.group(1)))
    # fine squares for small parts, coarser for big plates (a few hundred across is plenty for a thumbnail)
    cuts = _Cuts(min(1.0, max(0.2, (bounds or 120.0) / 750.0)))
    x = y = z = 0.0
    mode, tool, absolute, unit = 0, None, True, 1.0
    for raw in data.split(b"\n"):
        if not raw or raw[0] == 59:                        # ";"
            continue
        line = raw.split(b";", 1)[0]
        if b"(" in line:
            line = re.sub(rb"\([^)]*\)", b"", line)
        words = _WORD.findall(line)
        if not words:
            continue
        w, g53 = {}, False
        for letter, val in words:
            L = letter.upper()
            v = float(val)
            if L == b"G":
                g = int(v)
                if g in (0, 1, 2, 3):
                    mode = g
                elif g == 53:
                    g53 = True
                elif g == 90:
                    absolute = True
                elif g == 91:
                    absolute = False
                elif g == 20:
                    unit = 25.4
                elif g == 21:
                    unit = 1.0
            elif L == b"T":
                tool = int(v)
            else:
                w[L] = v * unit
        if g53 or not (b"X" in w or b"Y" in w or b"Z" in w):
            continue
        if absolute:
            nx, ny, nz = w.get(b"X", x), w.get(b"Y", y), w.get(b"Z", z)
        else:
            nx, ny, nz = x + w.get(b"X", 0.0), y + w.get(b"Y", 0.0), z + w.get(b"Z", 0.0)
        spec = tools.get(tool)
        if mode == 1:
            cuts.move((x, y, z), (nx, ny, nz), spec)
        elif mode in (2, 3) and (b"I" in w or b"J" in w):
            cx, cy = x + w.get(b"I", 0.0), y + w.get(b"J", 0.0)
            r = math.hypot(x - cx, y - cy)
            a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
            sweep = a1 - a0
            if mode == 2 and sweep >= 0:
                sweep -= 2 * math.pi
            if mode == 3 and sweep <= 0:
                sweep += 2 * math.pi
            n = max(2, int(abs(sweep) * r / max(cuts.cell, 0.3)))
            px, py, pz = x, y, z
            for s in range(1, n + 1):
                f = s / n
                qx, qy = cx + r * math.cos(a0 + sweep * f), cy + r * math.sin(a0 + sweep * f)
                qz = z + (nz - z) * f
                cuts.move((px, py, pz), (qx, qy, qz), spec)
                px, py, pz = qx, qy, qz
        elif mode in (2, 3):
            cuts.move((x, y, z), (nx, ny, nz), spec)
        x, y, z = nx, ny, nz
    return cuts, stock_t


def render(data: bytes, size: int = SIZE):
    """The part as a PNG (bytes), or None when nothing is cut or Pillow is missing."""
    if Image is None:
        return None
    cuts, stock_t = _cuts(data)
    depth, cell = cuts.depth, cuts.cell
    if not depth:
        return None
    deep = min(depth.values())
    thick = stock_t or -deep
    pad = int(TAB_MM / cell) + 4
    keys = depth.keys()
    i0, i1 = min(k[0] for k in keys) - pad, max(k[0] for k in keys) + pad
    j0, j1 = min(k[1] for k in keys) - pad, max(k[1] for k in keys) + pad
    W, H = i1 - i0 + 1, j1 - j0 + 1
    h = depth.get
    thr = {k for k, v in depth.items() if v <= -thick + 0.02}
    # What the outside of the part can't cross: cut-through squares, and anything cut at least a third of the way
    # through, which is how a cut-out's holding tabs are cut (Makera Studio lifts the bit to half depth over them).
    # Small gaps up to TAB_MM are bridged too; then the outside grows back over that band (never across a cut).
    wall_z = -max(0.35 * thick, 0.25)
    wall = {k for k, v in depth.items() if v <= wall_z} if thr else set()
    rc = max(1, int(TAB_MM / 2 / cell))
    closed = set(wall)
    front = list(wall)
    for _ in range(rc):                                # grown a square at a time: cheap even for long outlines
        nxt = []
        for i, j in front:
            for q in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if q not in closed:
                    closed.add(q)
                    nxt.append(q)
        front = nxt
    outside = set()
    stack = [(i, j) for i in range(i0, i1 + 1) for j in (j0, j1)] + [(i, j) for j in range(j0, j1 + 1) for i in (i0, i1)]
    while stack:
        k = stack.pop()
        if k in outside or k in closed or not (i0 <= k[0] <= i1 and j0 <= k[1] <= j1):
            continue
        outside.add(k)
        i, j = k
        stack.extend(((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)))
    front = list(outside)
    for _ in range(2 * rc + 2):
        nxt = []
        for i, j in front:
            for q in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if q not in outside and q in closed and q not in wall and i0 <= q[0] <= i1 and j0 <= q[1] <= j1:
                    outside.add(q)
                    nxt.append(q)
        front = nxt
    has_outline = bool(thr) and W * H - len(outside) - len(thr) > 0.05 * W * H
    # Pieces cut loose (the middle of a hole cut as a ring) fall out: keep the pieces at least a quarter the size
    # of the biggest (several parts on one sheet all stay), leave out the small ones.
    dropped = set()
    if thr:
        seen, pieces = set(), []
        for jj in range(H):
            for ii in range(W):
                k0 = (i0 + ii, j1 - jj)
                if k0 in seen or k0 in thr or (has_outline and k0 in outside):
                    continue
                piece, stack = [], [k0]
                seen.add(k0)
                while stack:
                    i, j = stack.pop()
                    piece.append((i, j))
                    for q in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                        if (q not in seen and q not in thr and not (has_outline and q in outside)
                                and i0 <= q[0] <= i1 and j0 <= q[1] <= j1):
                            seen.add(q)
                            stack.append(q)
                pieces.append(piece)
        biggest = max((len(pc) for pc in pieces), default=0)
        for pc in pieces:
            if len(pc) < biggest / 4:
                dropped.update(pc)

    L = math.sqrt(sum(c * c for c in LIGHT))
    half = (LIGHT[0], LIGHT[1], LIGHT[2] + L)
    HL = math.sqrt(sum(c * c for c in half))
    zs = 1.6                                           # relief made a little steeper, so 0.1 mm detail shows
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = img.load()
    for jj in range(H):
        j = j1 - jj
        for ii in range(W):
            i = i0 + ii
            k = (i, j)
            if k in thr or (has_outline and k in outside) or k in dropped:
                continue
            z0 = h(k, 0.0)
            gx = (h((i + 1, j), 0.0) - h((i - 1, j), 0.0)) / (2 * cell) * zs
            gy = (h((i, j + 1), 0.0) - h((i, j - 1), 0.0)) / (2 * cell) * zs
            nl = math.sqrt(gx * gx + gy * gy + 1.0)
            diff = max(0.0, (-gx * LIGHT[0] - gy * LIGHT[1] + LIGHT[2]) / (nl * L))
            spec = max(0.0, (-gx * half[0] - gy * half[1] + half[2]) / (nl * HL)) ** 24
            shade = (0.30 + 0.85 * diff) * (1.0 + 0.35 * z0 / max(0.5, thick))
            px[ii, jj] = (min(255, int(BRONZE[0] * shade + 120 * spec)), min(255, int(BRONZE[1] * shade + 100 * spec)),
                          min(255, int(BRONZE[2] * shade + 70 * spec)), 255)
    box = img.getbbox()
    if not box:
        return None
    img = img.crop(box)
    k = (size - 8) / max(img.width, img.height)
    img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def main(argv=None) -> int:
    """python3 -m z1screen.preview JOB.nc OUT.png: exit 0 with the picture written, 3 when nothing is cut."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__)
        return 2
    with open(args[0], "rb") as fh:
        png = render(fh.read())
    if not png:
        return 3
    with open(args[1], "wb") as fh:
        fh.write(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
