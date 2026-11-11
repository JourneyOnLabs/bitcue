"""Where a plate is cut, as grids of 0.5 mm cells.

Used by the screen service (gcode.py makes each job's footprint, jobs_api.py
keeps the record of the plate on the machine and checks a placement before a
start) and by the small screen (touch.py checks the placement as the part is
moved, tft.py draws it). Pure Python, no extra packages.

A job's footprint is every cell its bits can touch: the path of each bit's
center while it cuts (feed moves at or below the top of the plate, Z <= 0),
widened by that bit's radius. All grids sit on one 0.5 mm lattice: cell
(c, r) covers X c*0.5 to (c+1)*0.5 and Y r*0.5 to (r+1)*0.5 mm. So a part
moved by whole cells is the same grid with its corner moved. Each row is a
Python int (bit i is column c0 + i).

Plate coordinates: mm from the plate's corner in the Anchor 1 corner, X to
the right, Y toward the back, the same as the work coordinates of a job whose
work origin is Anchor 1 with offsets 0.
"""

from __future__ import annotations

import math
import re

RES = 0.5            # mm per cell
STEP = RES / 2.0     # mm between points marked along a path
_ONES = re.compile("1+")


def cells(mm: float) -> int:
    """Whole cells that cover a distance (rounded up)."""
    return int(math.ceil(float(mm) / RES - 1e-9))


def _spread(v: int, k: int) -> int:
    """v OR v shifted left and right by 1..k."""
    out, done, step = v, 0, 1
    while done < k:
        s = min(step, k - done)
        out = out | (out << s) | (out >> s)
        done += s
        step *= 2
    return out


class Grid:
    __slots__ = ("c0", "r0", "rows")

    def __init__(self, c0: int = 0, r0: int = 0, rows=None) -> None:
        self.c0 = int(c0)
        self.r0 = int(r0)
        self.rows = list(rows or [])

    # ---------------------------------------------------------------- basics
    def __bool__(self) -> bool:
        return any(self.rows)

    def trimmed(self) -> "Grid":
        rows = self.rows
        i, j = 0, len(rows)
        while i < j and not rows[i]:
            i += 1
        while j > i and not rows[j - 1]:
            j -= 1
        if i == j:
            return Grid()
        rows = rows[i:j]
        low = min((v & -v).bit_length() - 1 for v in rows if v)
        if low:
            rows = [v >> low for v in rows]
        return Grid(self.c0 + low, self.r0 + i, rows)

    def bbox(self):
        """(first column, first row, last column, last row) of the cut cells, or None."""
        g = self.trimmed()
        if not g.rows:
            return None
        return (g.c0, g.r0, g.c0 + max(v.bit_length() for v in g.rows) - 1, g.r0 + len(g.rows) - 1)

    def bbox_mm(self):
        b = self.bbox()
        if b is None:
            return None
        return [b[0] * RES, b[1] * RES, (b[2] + 1) * RES, (b[3] + 1) * RES]

    def moved(self, dc: int, dr: int) -> "Grid":
        return Grid(self.c0 + int(dc), self.r0 + int(dr), self.rows)

    def count(self) -> int:
        return sum(bin(v).count("1") for v in self.rows)

    def row(self, r: int, base: int) -> int:
        """Row r (lattice row) as bits counted from column `base` (base must not be right of c0)."""
        k = r - self.r0
        if k < 0 or k >= len(self.rows):
            return 0
        v = self.rows[k]
        d = self.c0 - base
        return v << d if d >= 0 else v >> (-d)

    # ---------------------------------------------------------------- combining
    def overlaps(self, other: "Grid") -> bool:
        lo = max(self.r0, other.r0)
        hi = min(self.r0 + len(self.rows), other.r0 + len(other.rows))
        if lo >= hi:
            return False
        base = min(self.c0, other.c0)
        for r in range(lo, hi):
            if self.row(r, base) & other.row(r, base):
                return True
        return False

    def both(self, other: "Grid") -> "Grid":
        """Cells in both grids."""
        lo = max(self.r0, other.r0)
        hi = min(self.r0 + len(self.rows), other.r0 + len(other.rows))
        if lo >= hi:
            return Grid()
        base = min(self.c0, other.c0)
        return Grid(base, lo, [self.row(r, base) & other.row(r, base) for r in range(lo, hi)]).trimmed()

    def union(self, other: "Grid") -> "Grid":
        if not other.rows:
            return Grid(self.c0, self.r0, self.rows)
        if not self.rows:
            return Grid(other.c0, other.r0, other.rows)
        lo = min(self.r0, other.r0)
        hi = max(self.r0 + len(self.rows), other.r0 + len(other.rows))
        base = min(self.c0, other.c0)
        return Grid(base, lo, [self.row(r, base) | other.row(r, base) for r in range(lo, hi)])

    def dilate(self, k: int) -> "Grid":
        """Every cell within k cells (a round brush) of a cut cell."""
        k = int(k)
        if k <= 0 or not self.rows:
            return Grid(self.c0, self.r0, self.rows)
        base = [v << k for v in self.rows]
        hw = [int(math.floor(math.sqrt(max(0.0, (k + 0.5) ** 2 - dy * dy)))) for dy in range(k + 1)]
        spread = {w: [_spread(v, w) for v in base] for w in set(hw)}
        n = len(base)
        out = [0] * (n + 2 * k)
        for dy in range(-k, k + 1):
            src = spread[hw[abs(dy)]]
            off = k + dy
            for i in range(n):
                if src[i]:
                    out[i + off] |= src[i]
        return Grid(self.c0 - k, self.r0 - k, out)

    @staticmethod
    def rect(x0: float, y0: float, x1: float, y1: float) -> "Grid":
        """The cells covering a rectangle in mm."""
        c0, c1 = int(math.floor(x0 / RES)), int(math.ceil(x1 / RES)) - 1
        r0, r1 = int(math.floor(y0 / RES)), int(math.ceil(y1 / RES)) - 1
        if c1 < c0 or r1 < r0:
            return Grid()
        v = (1 << (c1 - c0 + 1)) - 1
        return Grid(c0, r0, [v] * (r1 - r0 + 1))

    # ---------------------------------------------------------------- saving
    def to_json(self) -> dict:
        g = self.trimmed()
        return {"res": RES, "c0": g.c0, "r0": g.r0, "rows": [format(v, "x") for v in g.rows]}

    @staticmethod
    def from_json(d) -> "Grid":
        if not isinstance(d, dict) or abs(float(d.get("res", RES)) - RES) > 1e-9:
            return Grid()
        return Grid(int(d.get("c0", 0)), int(d.get("r0", 0)), [int(h, 16) if h else 0 for h in d.get("rows") or []])


def stamp(g: Grid, sx: float, sy: float) -> Grid:
    """A grid moved by sx, sy mm. A move that isn't a whole number of cells covers both neighbors."""
    fx, fy = sx / RES, sy / RES
    xs = sorted({int(math.floor(fx + 1e-9)), int(math.ceil(fx - 1e-9))})
    ys = sorted({int(math.floor(fy + 1e-9)), int(math.ceil(fy - 1e-9))})
    out = Grid()
    for dc in xs:
        for dr in ys:
            out = out.union(g.moved(dc, dr))
    return out


# -------------------------------------------------------------------- making a footprint
class Raster:
    """The cells a point path passes through (the path of one bit's center), and the path's exact box."""

    def __init__(self) -> None:
        self.cells = {}          # row -> set of columns
        self.box = None          # [x0, y0, x1, y1] of the path's points

    def _grow(self, x: float, y: float) -> None:
        b = self.box
        if b is None:
            self.box = [x, y, x, y]
        else:
            if x < b[0]:
                b[0] = x
            elif x > b[2]:
                b[2] = x
            if y < b[1]:
                b[1] = y
            elif y > b[3]:
                b[3] = y

    def add(self, x: float, y: float) -> None:
        self._grow(x, y)
        c, r = int(math.floor(x / RES)), int(math.floor(y / RES))
        s = self.cells.get(r)
        if s is None:
            self.cells[r] = {c}
        else:
            s.add(c)

    def line(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self._grow(x0, y0)
        self._grow(x1, y1)
        dx, dy = x1 - x0, y1 - y0
        n = int(math.hypot(dx, dy) / STEP) + 1
        last = None
        for i in range(n + 1):
            t = i / n
            x, y = x0 + dx * t, y0 + dy * t
            c, r = int(math.floor(x / RES)), int(math.floor(y / RES))
            if (c, r) != last:
                last = (c, r)
                s = self.cells.get(r)
                if s is None:
                    self.cells[r] = {c}
                else:
                    s.add(c)

    def arc(self, cx: float, cy: float, rad: float, a0: float, sweep: float) -> None:
        n = max(1, int(abs(sweep) * rad / STEP) + 1)
        for i in range(n + 1):
            a = a0 + sweep * i / n
            self.add(cx + rad * math.cos(a), cy + rad * math.sin(a))

    def grid(self) -> Grid:
        if not self.cells:
            return Grid()
        rmin, rmax = min(self.cells), max(self.cells)
        cmin = min(min(s) for s in self.cells.values())
        rows = []
        for r in range(rmin, rmax + 1):
            v = 0
            for c in self.cells.get(r, ()):
                v |= 1 << (c - cmin)
            rows.append(v)
        return Grid(cmin, rmin, rows)


def arc_center(x0, y0, x1, y1, i, j, r, cw):
    """Center, radius, start angle and signed sweep of a G2/G3 move (I/J offsets or R)."""
    if i is None and j is None and r is not None:
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        if chord < 1e-9 or abs(r) < chord / 2.0 - 1e-6:
            return None
        h = math.sqrt(max(0.0, r * r - chord * chord / 4.0))
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        # the center is to the right of the chord for G2 with a positive R (shorter arc)
        sgn = -1.0 if (cw == (r > 0)) else 1.0
        cx, cy = mx - sgn * h * dy / chord, my + sgn * h * dx / chord
        rad = abs(r)
    else:
        cx, cy = x0 + (i or 0.0), y0 + (j or 0.0)
        rad = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    sweep = a1 - a0
    if cw:
        while sweep >= 0:
            sweep -= 2 * math.pi
    else:
        while sweep <= 0:
            sweep += 2 * math.pi
    if abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
        sweep = -2 * math.pi if cw else 2 * math.pi
    return cx, cy, rad, a0, sweep


def footprint(rasters: dict, radii: dict, default_radius: float) -> Grid:
    """The union of each bit's path widened by that bit's radius."""
    out = Grid()
    for tool, ras in rasters.items():
        g = ras.grid()
        if g:
            out = out.union(g.dilate(cells(radii.get(tool, default_radius))))
    return out.trimmed()


# -------------------------------------------------------------------- checking a placement
GAP = 1.0            # mm of bare plate kept between a new job's cuts and anything already cut
PROBE_EDGE = 3.0     # mm the Z probe spot keeps from the plate's edges
PROBE_R = 1.5        # mm around the Z probe spot that must be bare
LEVEL_EDGE = 1.0     # mm the leveling box keeps from the plate's edges
NEAR_STEPS = 12      # the nearest cut is measured up to 12 cells (6 mm) away


class Plate:
    """A plate and what is cut on it, ready for many placement checks.

    The rules: the path of the bit's center must stay on the plate (a cut-out
    may run past the edge by up to the bit's radius, as the corner parts of the
    5-part sheet do); the job's footprint must keep `gap` mm from every cut area;
    the Z probe spot must be on bare plate, 3 mm or more from the edges; with
    leveling on, the whole leveling box must be bare and on the plate."""

    def __init__(self, w: float, h: float, occ: Grid, gap: float = GAP, probe_edge: float = PROBE_EDGE) -> None:
        self.w, self.h = float(w), float(h)
        self.occ = occ.trimmed()
        self.gap, self.probe_edge = float(gap), float(probe_edge)
        self.kgap = cells(gap)
        self.near = [self.occ] + [self.occ.dilate(k) for k in range(1, NEAR_STEPS + 1)]
        self.occ_gap = self.near[self.kgap] if self.kgap <= NEAR_STEPS else self.occ.dilate(self.kgap)
        self.occ_probe = self.occ.dilate(cells(PROBE_R))

    def inside(self, box, margin: float) -> bool:
        x0, y0, x1, y1 = box
        e = margin - 1e-6
        return x0 >= e and y0 >= e and x1 <= self.w - e and y1 <= self.h - e

    def check(self, foot: Grid, dc: int, dr: int, probe=None, level=None, path_box=None,
              measure: bool = True) -> dict:
        """Can the job start with its footprint moved by dc, dr cells?

        foot is the footprint at the file's own place on the plate; probe the Z
        probe spot, level the leveling box and path_box the box of the bit's
        center path, all in plate mm at the file's own place (or None). Returns
        {"ok", "code", "text", "near", "box"}: near is about how far in mm the
        nearest cut is (None if more than 6 mm, or not measured), box the
        footprint's corners in plate mm."""
        f = foot.moved(dc, dr)
        dx, dy = dc * RES, dr * RES
        box = f.bbox_mm()
        near = None
        if measure:
            for k, g in enumerate(self.near):
                if g and f.overlaps(g):
                    near = max(0, k - 1) * RES      # k - 1 bare cells between them
                    break
        res = {"ok": False, "code": "", "text": "", "near": near, "box": box}
        centers = [path_box[0] + dx, path_box[1] + dy, path_box[2] + dx, path_box[3] + dy] if path_box else None
        if box is None:
            res.update(code="empty", text="The Pi found no cutting moves in this file.")
        elif not self.inside(centers or box, 0.0):
            res.update(code="off", text="Part of the job is off the plate.")
        elif self.occ and f.overlaps(self.occ):
            res.update(code="overlap", text="It overlaps an area already cut.")
        elif self.occ_gap and f.overlaps(self.occ_gap):
            res.update(code="close", text="It is closer than %s mm to an area already cut." % _n(self.gap))
        elif probe is not None and not self._probe_ok(probe[0] + dx, probe[1] + dy):
            res.update(code="probe", text="The Z probe spot, X%s Y%s, isn't on bare plate." % (
                _n(probe[0] + dx), _n(probe[1] + dy)))
        elif level is not None and not self._level_ok([level[0] + dx, level[1] + dy, level[2] + dx, level[3] + dy]):
            res.update(code="level", text="The leveling area reaches a cut area or the plate's edge.")
        else:
            res.update(ok=True, code="ok", text="Clear of the cut areas.")
        return res

    def _probe_ok(self, x: float, y: float) -> bool:
        if not self.inside([x, y, x, y], self.probe_edge):
            return False
        c, r = int(math.floor(x / RES)), int(math.floor(y / RES))
        return not (self.occ_probe and self.occ_probe.row(r, c) & 1)

    def _level_ok(self, box) -> bool:
        if not self.inside(box, LEVEL_EDGE):
            return False
        g = Grid.rect(*box)
        return not (self.occ_gap and g.overlaps(self.occ_gap))

    def auto_place(self, foot: Grid, probe=None, level=None, path_box=None, step: int = 4):
        """(dc, dr) for the file's own place if it passes, else the first place that does,
        looking from the Anchor 1 corner along the front first; None if there is no room."""
        if self.check(foot, 0, 0, probe, level, path_box, measure=False)["ok"]:
            return 0, 0
        b = path_box or foot.bbox_mm()
        if b is None:
            return None
        # moves (in whole cells) that keep the box on the plate
        c_lo, c_hi = int(math.ceil(-b[0] / RES - 1e-9)), int(math.floor((self.w - b[2]) / RES + 1e-9))
        r_lo, r_hi = int(math.ceil(-b[1] / RES - 1e-9)), int(math.floor((self.h - b[3]) / RES + 1e-9))
        if c_hi < c_lo or r_hi < r_lo:
            return None
        rows = list(range(r_lo, r_hi + 1, step))
        if rows[-1] != r_hi:
            rows.append(r_hi)
        cols = list(range(c_lo, c_hi + 1, step))
        if cols[-1] != c_hi:
            cols.append(c_hi)
        for dr in rows:
            for dc in cols:
                if self.check(foot, dc, dr, probe, level, path_box, measure=False)["ok"]:
                    return dc, dr
        return None


def _n(v: float) -> str:
    s = ("%.1f" % float(v)).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# -------------------------------------------------------------------- drawing on the small screen
PLACE_BOX = (6, 24, 190, 192)    # where the plate goes on the placing screen (x, y, w, h in pixels)
SPOT_BOX = (10, 26, 184, 184)    # ... and on a plate layout's spot map


def fit(pw: float, ph: float, box=PLACE_BOX):
    """Where a pw x ph mm plate goes in a screen box: (x, y, scale, pw, ph); (x, y) is its back-left corner."""
    bx, by, bw, bh = box
    s = min(bw / float(pw), bh / float(ph))
    return (bx + (bw - pw * s) / 2.0, by + (bh - ph * s) / 2.0, s, float(pw), float(ph))


def to_screen(geom, x: float, y: float):
    gx, gy, s, pw, ph = geom
    return gx + x * s, gy + (ph - y) * s


def to_plate(geom, px: float, py: float):
    gx, gy, s, pw, ph = geom
    return (px - gx) / s, ph - (py - gy) / s


def screen_runs(g: Grid, geom, dc: int = 0, dr: int = 0, clip: bool = True) -> list:
    """Rectangles [x, y, w, h] in screen pixels that cover the grid's cells (moved by dc, dr)."""
    if not g.rows:
        return []
    gx, gy, s, pw, ph = geom
    c0, r0, n = g.c0 + dc, g.r0 + dr, len(g.rows)
    # screen rows the grid covers
    top = int(math.floor(gy + (ph - (r0 + n) * RES) * s))
    bottom = int(math.ceil(gy + (ph - r0 * RES) * s))
    if clip:
        top, bottom = max(top, int(math.floor(gy))), min(bottom, int(math.ceil(gy + ph * s)))
    lo_x, hi_x = (int(math.floor(gx)), int(math.ceil(gx + pw * s))) if clip else (-10 ** 6, 10 ** 6)
    rects, open_ = [], {}
    for py in range(top, bottom):
        y_hi = ph - (py - gy) / s           # plate mm at the pixel row's top edge
        y_lo = ph - (py + 1 - gy) / s
        ra = max(r0, int(math.floor(y_lo / RES + 1e-9)))
        rb = min(r0 + n - 1, int(math.ceil(y_hi / RES - 1e-9)) - 1)
        v = 0
        for r in range(ra, rb + 1):
            v |= g.rows[r - r0]
        runs = []
        if v:
            bits = format(v, "b")[::-1]
            for m in _ONES.finditer(bits):
                x0 = int(math.floor(gx + (c0 + m.start()) * RES * s + 1e-9))
                x1 = int(math.ceil(gx + (c0 + m.end()) * RES * s - 1e-9))
                x0, x1 = max(x0, lo_x), min(x1, hi_x)
                if x1 > x0:
                    if runs and x0 <= runs[-1][1]:
                        runs[-1] = (runs[-1][0], max(runs[-1][1], x1))
                    else:
                        runs.append((x0, x1))
        nxt = {}
        for x0, x1 in runs:
            rect = open_.get((x0, x1))
            if rect is not None:
                rect[3] += 1
            else:
                rect = [x0, py, x1 - x0, 1]
                rects.append(rect)
            nxt[(x0, x1)] = rect
        open_ = nxt
    return rects
