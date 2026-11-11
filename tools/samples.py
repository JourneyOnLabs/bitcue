"""Made-up job files for the demo and the tests.

Nothing here comes from anyone's real jobs: the shapes are simple geometry, the
bits are common sizes, and every number is chosen to be easy to check. The files
are written the way Makera's CAM writes them (";@MKR|..." header lines), with the
optional notes the screen reads (README.md, "Notes in job files").

  PLATE                       the sample plate: 200 x 150 x 6 mm plywood
  sign()                      a sign with three bits: probe, T1, T2, T3, T1 (about 23 minutes)
  tag()                       a smaller job beside it: probe, T1, T3, T1 (about 8 minutes)
  disc(n)                     part n of a 2 x 2 plate layout of discs (4 files), nested so the
                              cut-outs of neighbouring discs run into each other
  part(w, h, depth, r, at)    a small part with no notes (no run window, no plate note)
  config_txt(x, y)            the lines of a machine settings file that give Anchor 1

All functions return the file's bytes and give the same bytes every time.
"""

from __future__ import annotations

import math

PLATE = {"w": 200.0, "h": 150.0, "t": 6.0, "material": "Plywood"}
PLATE_NOTE = "; Plywood plate 200 x 150 x 6 mm, pushed into the Anchor 1 corner."
SHEET_NOTE = "; Plywood sheet 200 x 150 x 6 mm, pushed into the Anchor 1 corner."
DISC_CENTERS = [(30.0, 30.0), (75.0, 30.0), (30.0, 75.0), (75.0, 75.0)]
DISC_D = 40.0

TOOLS = {
    1: "type=Flat End|diameter=3.175|flutelength=12|tipdiameter=3.175|angle=0|cornerradius=0",
    2: "type=Ball End|diameter=2|flutelength=8|tipdiameter=2|angle=0|cornerradius=1",
    3: "type=Engraving|diameter=3.175|flutelength=3|tipdiameter=0.1|angle=60|cornerradius=0",
}
TOOL_NAMES = {1: "Flat end mill 3.175 x 12", 2: "Ball end mill 2 x 8", 3: "V-bit 60 deg 0.1"}


def _n(v: float) -> str:
    s = ("%.3f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _header(tools, stock, notes) -> list:
    L = [";@MKR|BEGIN", ";@MKR|SCHEMA|v=1.0.0", ";@MKR|MACHINE|id=Z1|name=Makera Z1",
         ";@MKR|MATERIAL|id=|name3=other|name1=Wood|name2=Plywood",
         ";@MKR|STOCK|id=cuboid|length=%s|width=%s|height=%s" % (_n(stock[0]), _n(stock[1]), _n(stock[2])),
         ";@MKR|ORIGIN|id=0|type_name=topFrontLeft|x=0|y=0|z=0",
         ";@MKR|CAM|id=z1screen-samples|name=z1screen sample jobs|v=1.0",
         ";@MKR|UNIT|value=mm"]
    for t in tools:
        L.append(";@MKR|TOOL|number=%d|name=%s|%s" % (t, TOOL_NAMES[t], TOOLS[t]))
    L.append(";@MKR|END")
    L.append("")
    L += notes
    L += ["G90 G94 G17 G21", "G54", "G0 Z5"]
    return L


class _Path:
    """Writes moves, keeping track of where the bit is."""

    def __init__(self) -> None:
        self.L = []

    def tool(self, t: int, rpm: int) -> None:
        self.L += ["G0 Z5", "T%d M6" % t, "M3 S%d" % rpm, "G4 P2"]

    def rapid(self, x: float, y: float) -> None:
        self.L += ["G0 Z5", "G0 X%s Y%s" % (_n(x), _n(y))]

    def plunge(self, z: float, f: float = 200) -> None:
        self.L.append("G1 Z%s F%s" % (_n(z), _n(f)))

    def to(self, x: float, y: float, z: float | None = None, f: float | None = None) -> None:
        s = "G1 X%s Y%s" % (_n(x), _n(y))
        if z is not None:
            s += " Z%s" % _n(z)
        if f is not None:
            s += " F%s" % _n(f)
        self.L.append(s)

    def arc(self, x: float, y: float, i: float, j: float, f: float | None = None) -> None:
        self.L.append("G2 X%s Y%s I%s J%s" % (_n(x), _n(y), _n(i), _n(j)) + (" F%s" % _n(f) if f else ""))

    def lift(self) -> None:
        self.L.append("G0 Z5")


def _rounded_rect(p: _Path, x0, y0, x1, y1, r, z, f) -> None:
    """One pass around a rectangle with round corners, counter-clockwise from the front edge."""
    p.rapid(x0 + r, y0)
    p.plunge(z)
    p.to(x1 - r, y0, f=f)
    p.L.append("G3 X%s Y%s I0 J%s" % (_n(x1), _n(y0 + r), _n(r)))
    p.to(x1, y1 - r)
    p.L.append("G3 X%s Y%s I%s J0" % (_n(x1 - r), _n(y1), _n(-r)))
    p.to(x0 + r, y1)
    p.L.append("G3 X%s Y%s I0 J%s" % (_n(x0), _n(y1 - r), _n(-r)))
    p.to(x0, y0 + r)
    p.L.append("G3 X%s Y%s I%s J0" % (_n(x0 + r), _n(y0), _n(r)))
    p.lift()


def _pocket(p: _Path, x0, y0, x1, y1, depth, step, f, passes) -> None:
    """A zig-zag pocket, cut in equal depth passes."""
    for k in range(1, passes + 1):
        z = -depth * k / passes
        y = y0
        p.rapid(x0, y0)
        p.plunge(z)
        flip = False
        while y <= y1 + 1e-9:
            p.to(x0 if flip else x1, y, f=f)
            y += step
            if y <= y1 + 1e-9:
                p.to(x0 if flip else x1, y)
            flip = not flip
        p.lift()


def _relief(p: _Path, x0, y0, x1, y1, base, step, seg, f) -> None:
    """Ball-end finishing passes over a gently waved floor."""
    y = y0
    flip = False
    p.rapid(x0, y0)
    p.plunge(base)
    while y <= y1 + 1e-9:
        n = int(round((x1 - x0) / seg))
        xs = [x0 + (x1 - x0) * i / n for i in range(n + 1)]
        if flip:
            xs.reverse()
        for x in xs:
            z = base - 0.4 * (0.5 + 0.5 * math.sin(x / 4.0) * math.cos(y / 5.0))
            p.to(x, y, z, f)
            f = None
        y = round(y + step, 6)
        flip = not flip
    p.lift()


def _engrave(p: _Path, strokes, depth, f) -> None:
    for pts in strokes:
        p.rapid(*pts[0])
        p.plunge(-depth, 120)
        for i, (x, y) in enumerate(pts[1:]):
            p.to(x, y, f=f if i == 0 else None)
        p.lift()


def _finish(L) -> bytes:
    L += ["G0 Z5", "M5", "M30"]
    return ("\n".join(L) + "\n").encode("utf-8")


def sign() -> bytes:
    """A 80 x 60 mm sign at X10-90 Y10-70: pocket (T1), relief (T2), lettering (T3), cut out (T1)."""
    notes = ["; Sample job for BitCue: a small sign. Made-up geometry, not a real design.",
             PLATE_NOTE,
             "; X0 Y0 at the plate corner on the anchor, Z0 on the top of the plate.",
             "; Run window: work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X5 Y5; "
             "Scan Margin on; Auto Leveling on, 3 x 3, 2 mm."]
    L = _header((1, 2, 3), (PLATE["w"], PLATE["h"], PLATE["t"]), notes)
    p = _Path()
    p.tool(1, 18000)
    _pocket(p, 22, 22, 78, 58, 1.5, 1.5, 900, 3)
    p.tool(2, 20000)
    _relief(p, 24, 24, 76, 56, -1.5, 0.2, 0.5, 1000)
    p.tool(3, 20000)
    strokes = [[(15, 15), (85, 15), (85, 65), (15, 65), (15, 15)]]
    for k in range(6):                                   # six letter-like strokes along the top band
        x = 26 + k * 9
        strokes.append([(x, 60), (x + 3, 63), (x + 6, 60)])
    _engrave(p, strokes, 0.5, 400)
    p.tool(1, 18000)
    for z in (-1.5, -3.0, -4.5, -6.2):
        _rounded_rect(p, 10, 10, 90, 70, 5, z, 700)
    return _finish(L + p.L)


def tag() -> bytes:
    """A 60 x 40 mm tag at X110-170 Y20-60: pocket (T1), lettering (T3), cut out (T1)."""
    notes = ["; Sample job for BitCue: a tag. Made-up geometry, not a real design.",
             PLATE_NOTE,
             "; Run window: work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X104 Y14; "
             "Scan Margin on; Auto Leveling off."]
    L = _header((1, 3), (PLATE["w"], PLATE["h"], PLATE["t"]), notes)
    p = _Path()
    p.tool(1, 18000)
    _pocket(p, 118, 28, 162, 52, 1.0, 1.2, 900, 2)
    p.tool(3, 20000)
    _engrave(p, [[(114, 24), (166, 24), (166, 56), (114, 56), (114, 24)],
                 [(125, 40), (135, 46), (145, 40), (155, 46)]], 0.4, 400)
    p.tool(1, 18000)
    for z in (-2.0, -4.0, -6.2):
        _rounded_rect(p, 110, 20, 170, 60, 4, z, 700)
    return _finish(L + p.L)


def disc(n: int) -> bytes:
    """Part n (1-4) of a 2 x 2 layout: a 40 mm disc with an engraved ring, cut out."""
    cx, cy = DISC_CENTERS[n - 1]
    r = DISC_D / 2.0
    notes = ["; Sample job for BitCue: a disc, part of a 2 x 2 sheet layout. Made-up geometry.",
             SHEET_NOTE,
             "; Part %d of the 4 that fit on this sheet: center X%s Y%s, turned 0°" % (n, _n(cx), _n(cy)),
             "; Run window: work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X%s Y%s; "
             "Scan Margin on; Auto Leveling off." % (_n(cx), _n(cy))]
    L = _header((1, 3), (PLATE["w"], PLATE["h"], PLATE["t"]), notes)
    p = _Path()
    p.tool(3, 20000)
    ring = [(cx + (r - 6) * math.cos(a * math.pi / 18), cy + (r - 6) * math.sin(a * math.pi / 18))
            for a in range(37)]
    _engrave(p, [ring], 0.4, 400)
    p.tool(1, 18000)
    rr = r + 1.5875                                       # the bit's centre runs outside the disc
    for z in (-2.0, -4.0, -6.2):
        p.rapid(cx + rr, cy)
        p.plunge(z)
        p.arc(cx + rr, cy, -rr, 0, 700)
        p.lift()
    return _finish(L + p.L)


def part(w: float, h: float, depth: float = 1.2, r: float | None = None, at=(0.0, 0.0)) -> bytes:
    """A small job with no notes: a pocket line, an outline and an optional circle, from X0 Y0 (or from at)."""
    x0, y0 = at
    L = [";@MKR|BEGIN", ";@MKR|TOOL|number=1|name=Flat 3.175|type=Flat End|diameter=3.175|flutelength=12",
         ";@MKR|STOCK|id=cuboid|length=200|width=150|height=6", ";@MKR|END", "G21", "G90", "M6 T1", "M3 S12000",
         "G0 Z5", "G0 X%g Y%g" % (x0, y0), "G1 Z-0.4 F100", "G1 X%g F300" % (x0 + w), "G1 Y%g" % (y0 + h),
         "G1 X%g" % x0, "G1 Y%g" % y0, "G1 Z-%g F100" % depth, "G1 X%g Y%g F300" % (x0 + w, y0 + h)]
    if r:
        L += ["G0 Z5", "G0 X%g Y%g" % (x0 + w / 2.0 + r, y0 + h / 2.0), "G1 Z-0.6 F100",
              "G2 X%g Y%g I%g J0 F300" % (x0 + w / 2.0 + r, y0 + h / 2.0, -r)]
    L += ["G0 Z5", "M5", "M30"]
    return ("\n".join(L) + "\n").encode()


def config_txt(x: float, y: float) -> bytes:
    """The lines of a machine settings file (/sd/config.txt) that give Anchor 1, among a few others."""
    return ("# Makera Z1 settings (made up for the tests)\n"
            "coordinate.anchor1_x            %s      # anchor 1\n"
            "coordinate.anchor1_y            %s\n"
            "coordinate.anchor2_offset_x     90\n"
            "coordinate.anchor2_offset_y     45\n" % (_n(x), _n(y))).encode()


def write_all(folder: str) -> dict:
    """Write every sample file into folder (for looking at them). Returns {file name: size}."""
    import os
    os.makedirs(folder, exist_ok=True)
    out = {}
    files = {"sample-sign.nc": sign(), "sample-tag.nc": tag()}
    files.update({"disc-position-%d.nc" % n: disc(n) for n in range(1, 5)})
    for name, data in files.items():
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(data)
        out[name] = len(data)
    return out


if __name__ == "__main__":
    import sys
    print(write_all(sys.argv[1] if len(sys.argv) > 1 else "samples"))
