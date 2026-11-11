"""Read a G-code job and work out its tool changes and timing.

The result ("profile") holds, for every few lines of the file, the estimated
machine time to reach that line, plus the line and tool of every tool change.
With the line number the machine reports while cutting, the screen can then
show the time left, the time to the next tool change and the next tool.

The time model was calibrated on a 4 h 48 min run on a Z1 Pro: each move
takes distance / feed (feeds capped at 1200 mm/min, Z at 600 mm/min, rapids at
1200 mm/min across and 600 mm/min down), with a floor of 35 ms per move
(26 ms for a V-bit), 102 s at the start for probing and 60 s for each later
tool change. When the file carries Makera's own estimate
(";@MKR|TIME|seconds=..."), the estimate is scaled to match it. While the job
runs, the screen also corrects the estimate with the machine's real elapsed
time.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import os
import re
from array import array

from . import platemap

PROFILE_VERSION = 7   # 5: the job's box, depth, plate and run window; 6: the bits' sizes and the job's
#                       footprint; 7: short bit descriptions, and the tool lines' fields (tool_specs)
DEFAULT_BIT_D = 3.175  # mm, for a bit whose size the file doesn't state (the Z1's 1/8" shank)
RAPID_XY = 1200.0   # mm/min
RAPID_Z = 600.0     # mm/min
FEED_CAP = 1200.0   # mm/min
PLUNGE_CAP = 600.0  # mm/min
VBIT_FLOOR_S = 0.026   # s per move for a V-bit (short engraving moves run a little faster)
DEFAULT_FLOOR_S = 0.035
START_S = 102.0
CHANGE_S = 60.0
MAX_CHECKPOINTS = 2400

_WORD = re.compile(rb"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def _num(v: float) -> str:
    return ("%.3f" % v).rstrip("0").rstrip(".")


# The fields of a ;@MKR|TOOL line kept in a profile, so a bit's description can be
# made again later without the file.
TOOL_FIELDS = ("name", "type", "diameter", "flutelength", "angle", "tipdiameter", "cornerradius")


def describe_tool(fields: dict) -> str:
    """A short description of a bit from its ;@MKR|TOOL line: what it is and its size, with no brand or
    part number. For example '3.175 × 12 mm flat end mill', '2 × 6 mm ball end mill', '30° V-bit, 0.2 mm tip',
    '90° chamfer bit', '1 mm drill'. Works the same for any bit: only the line's type and sizes are used.
    A type it doesn't know keeps the name the file gives it."""
    d = _describe(fields)
    return d[:1].upper() + d[1:] if d[:1].isalpha() and d[:1].islower() else d


def _describe(fields: dict) -> str:
    typ = " ".join((fields.get("type") or "").split()).lower()
    name = " ".join((fields.get("name") or "").split())

    def f(key):
        try:
            v = float(fields.get(key, ""))
        except (TypeError, ValueError):
            return None
        return v if v > 0 else None

    dia, flute, angle, tip, corner = (f("diameter"), f("flutelength"), f("angle"), f("tipdiameter"),
                                      f("cornerradius"))
    size = ("%s \u00d7 %s mm" % (_num(dia), _num(flute))) if dia and flute else ("%s mm" % _num(dia) if dia else "")
    if "taper" in typ and "ball" in typ:
        if corner:
            return "tapered ball nose, R%s" % _num(corner)
        return "tapered ball nose, %s mm tip" % _num(tip) if tip else "tapered ball nose"
    if "ball" in typ:
        return (size + " ball end mill").strip()
    if "bull" in typ:
        return (size + " bull nose" + (", R%s" % _num(corner) if corner else "")).strip()
    if "flat" in typ:
        return (size + " flat end mill").strip()
    if is_vbit(fields):
        if not angle:
            return "V-bit"
        return "%s\u00b0 V-bit" % _num(angle) + (", %s mm tip" % _num(tip) if tip else "")
    if "chamfer" in typ:
        return "%s\u00b0 chamfer bit" % _num(angle) if angle else "chamfer bit"
    if "drill" in typ:
        return ("%s mm drill" % _num(dia)) if dia else "drill"
    if name:
        return name
    if typ:
        return (size + " " + typ).strip()
    return ("%s mm bit" % _num(dia)) if dia else ""


def is_vbit(fields: dict) -> bool:
    """True for a V-bit / engraving bit, from its ;@MKR|TOOL line's type."""
    typ = " ".join((fields.get("type") or "").split()).lower()
    return "engrav" in typ or "v-bit" in typ or "vbit" in typ or typ == "v"


def friendly_tool_name(fields: dict) -> str:
    """The bit's name for the screen (see describe_tool)."""
    return describe_tool(fields)


def _arc_length(x0, y0, x1, y1, i, j, r, cw):
    if r is not None and (i is None and j is None):
        chord = math.hypot(x1 - x0, y1 - y0)
        if r == 0 or chord == 0:
            return chord
        half = min(1.0, chord / (2 * abs(r)))
        ang = 2 * math.asin(half)
        if r < 0:
            ang = 2 * math.pi - ang
        return abs(r) * ang
    cx = x0 + (i or 0.0)
    cy = y0 + (j or 0.0)
    rad = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    d = a0 - a1 if cw else a1 - a0
    while d <= 0:
        d += 2 * math.pi
    if abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
        d = 2 * math.pi
    return rad * d


_NUM = r"[-+]?\d+(?:\.\d+)?"
# Optional notes a job file can carry in its first comment lines (see "Notes in job files" in README.md):
#   ; Run window: work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X5 Y5; Scan Margin on; Auto Leveling off.
#   ; Aluminium plate 100 x 100 x 3 mm, pushed into the Anchor 1 corner.
#   ; Part 2 of the 5 that fit on this sheet: center X50 Y50, turned 0°
_RE_PART = re.compile(r"Part (\d+) of the (\d+) that fit on this (?:plate|sheet|stock): center X(%s) Y(%s)"
                      r"(?:, turned (%s)\s*°)?" % (_NUM, _NUM, _NUM), re.I)
_RE_PLATE = re.compile(r"^(\S.*?) (?:plate|sheet|stock) (%s)\s*[×xX]\s*(%s)\s*[×xX]\s*(%s)\s*mm" % (_NUM, _NUM, _NUM))


def parse_run_window(text: str):
    """The run settings a job file states in its header ("; Run window: ..."), or None if any part is unclear.

    "work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X5 Y5;
     Scan Margin on; Auto Leveling on, 5 x 5, 2 mm."
    """
    run = {"anchor": None, "offset": [0.0, 0.0], "probe": None, "margin": None, "level": None}
    seen = set()
    for seg in [p.strip() for p in text.strip().rstrip(".").split(";") if p.strip()]:
        low = seg.lower()
        if low.startswith("work origin"):
            m = re.search(r"anchor\s*(\d)", low)
            if not m:
                return None
            run["anchor"] = int(m.group(1))
            mo = re.search(r"offsets?\s+(%s)(?:\s*,?\s*(%s))?$" % (_NUM, _NUM), low)
            mx = re.search(r"x\s*(%s)" % _NUM, low.split("offset", 1)[-1]) if "offset" in low else None
            my = re.search(r"y\s*(%s)" % _NUM, low.split("offset", 1)[-1]) if "offset" in low else None
            if mx or my:
                run["offset"] = [float(mx.group(1)) if mx else 0.0, float(my.group(1)) if my else 0.0]
            elif mo:
                a = float(mo.group(1))
                run["offset"] = [a, float(mo.group(2)) if mo.group(2) else a]
            seen.add("origin")
        elif low.startswith("z probe"):
            if low.endswith(" off"):
                run["probe"] = None
            else:
                m = re.search(r"at x\s*(%s)\s*y\s*(%s)" % (_NUM, _NUM), low)
                if not m or "work origin" not in low:
                    return None
                run["probe"] = {"rel": "work", "x": float(m.group(1)), "y": float(m.group(2))}
            seen.add("probe")
        elif low.startswith("scan margin"):
            run["margin"] = low.endswith(" on")
            if not (low.endswith(" on") or low.endswith(" off")):
                return None
            seen.add("margin")
        elif low.startswith("auto leveling"):
            if low.endswith(" off"):
                run["level"] = None
            else:
                m = re.search(r"on,\s*(\d+)\s*x\s*(\d+),\s*(%s)\s*mm" % _NUM, low)
                if not m:
                    return None
                run["level"] = {"i": int(m.group(1)), "j": int(m.group(2)), "h": float(m.group(3))}
            seen.add("level")
    if seen != {"origin", "probe", "margin", "level"} or run["anchor"] is None:
        return None
    return run


def _header_notes(lines, limit: int = 80) -> dict:
    """Plate, part position and run window from the comment lines at the top of a job file."""
    out = {}
    for raw in lines[:limit]:
        s = raw.strip()
        if not s.startswith(b";") or s.startswith(b";@MKR") or s.startswith(b";("):
            continue
        t = s[1:].decode("utf-8", "replace").strip()
        m = _RE_PART.search(t)
        if m:
            out["part"] = {"n": int(m.group(1)), "of": int(m.group(2)), "cx": float(m.group(3)),
                           "cy": float(m.group(4)), "turn": float(m.group(5)) if m.group(5) else 0.0}
            continue
        if t.lower().startswith("run window:"):
            out["run_text"] = t.split(":", 1)[1].strip()
            out["run"] = parse_run_window(out["run_text"])
            continue
        m = _RE_PLATE.match(t)
        if m:
            out["plate_note"] = t
            out["plate_note_dims"] = [float(m.group(2)), float(m.group(3)), float(m.group(4))]
            out["plate_note_material"] = m.group(1)
    return out


def analyze(data: bytes, path: str = "") -> dict:
    """Build a profile from the raw bytes of a G-code file."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    n = len(lines)
    cum = array("d", [0.0]) * (n + 1)  # cum[k] = estimated seconds after line k
    tools = {}
    tool_specs = {}     # bit number -> the fields of its ;@MKR|TOOL line (TOOL_FIELDS)
    tool_d = {}         # bit number -> cutting diameter (mm) from the ;@MKR|TOOL lines
    vbits = set()       # bit numbers whose ;@MKR|TOOL line says V-bit / engraving
    cut_paths = {}      # bit number -> platemap.Raster of its center while it cuts (Z <= 0)
    air_paths = {}      # the same for feed moves above the plate (used only if nothing cuts at Z <= 0)
    header_time = None
    stock = None
    material = ""
    bx0 = by0 = bz0 = None
    bx1 = by1 = None
    changes = []
    x = y = z = 0.0
    have_pos = False
    mode = 0
    feed = None
    unit = 1.0
    absolute = True
    tool = None
    pending_tool = None
    first_change = True
    t = START_S
    for k in range(1, n + 1):
        raw = lines[k - 1]
        s = raw.strip()
        if not s:
            cum[k] = t
            continue
        c0 = s[0]
        if c0 == 0x3B or c0 == 0x28:  # ';' or '('
            if s.startswith(b";@MKR|"):
                parts = s.decode("utf-8", "replace").rstrip("\r").split("|")
                kind = parts[1] if len(parts) > 1 else ""
                kv = {}
                for p in parts[2:]:
                    if "=" in p:
                        a, b = p.split("=", 1)
                        kv[a] = b
                if kind == "TOOL" and kv.get("number"):
                    try:
                        tools[int(kv["number"])] = friendly_tool_name(kv)
                        tool_specs[int(kv["number"])] = {k: kv[k] for k in TOOL_FIELDS if kv.get(k)}
                        if is_vbit(kv):
                            vbits.add(int(kv["number"]))
                        d = float(kv.get("diameter") or 0)
                        if 0 < d < 60:
                            tool_d[int(kv["number"])] = d
                    except ValueError:
                        pass
                elif kind == "TIME" and kv.get("seconds"):
                    try:
                        header_time = float(kv["seconds"])
                    except ValueError:
                        pass
                elif kind == "STOCK":
                    try:
                        stock = {"shape": kv.get("id", ""), "length": float(kv.get("length", "")),
                                 "width": float(kv.get("width", "")), "height": float(kv.get("height", ""))}
                    except ValueError:
                        pass
                elif kind == "MATERIAL":
                    material = (kv.get("name2") or kv.get("name1") or "").strip()
            cum[k] = t
            continue
        sc = s.split(b";", 1)[0]
        if b"(" in sc:
            sc = re.sub(rb"\([^)]*\)", b"", sc)
        words = _WORD.findall(sc)
        if not words:
            cum[k] = t
            continue
        w = {}
        gs = []
        ms = []
        for letter, val in words:
            L = letter.upper()
            try:
                v = float(val)
            except ValueError:
                continue
            if L == b"G":
                gs.append(v)
            elif L == b"M":
                ms.append(v)
            else:
                w[L] = v
        for g in gs:
            if g in (0, 1, 2, 3):
                mode = int(g)
            elif g == 20:
                unit = 25.4
            elif g == 21:
                unit = 1.0
            elif g == 90:
                absolute = True
            elif g == 91:
                absolute = False
            elif g == 4:
                if b"S" in w:
                    t += w[b"S"]
                else:
                    p = w.get(b"P", 0.0)
                    t += p if p <= 60 else p / 1000.0  # grbl mode: seconds; large values are milliseconds
        if b"T" in w:
            pending_tool = int(w[b"T"])
        if 6 in ms:
            new_tool = pending_tool if pending_tool is not None else tool
            if new_tool is not None:
                changes.append({"line": k, "tool": new_tool, "t": round(t, 1)})
                tool = new_tool
                if not first_change:
                    t += CHANGE_S
                first_change = False
            cum[k] = t
            continue
        if b"F" in w:
            feed = w[b"F"] * unit
        if b"X" in w or b"Y" in w or b"Z" in w:
            machine_coords = 53 in gs
            nx, ny, nz = x, y, z
            if absolute:
                if b"X" in w:
                    nx = w[b"X"] * unit
                if b"Y" in w:
                    ny = w[b"Y"] * unit
                if b"Z" in w:
                    nz = w[b"Z"] * unit
            else:
                nx += w.get(b"X", 0.0) * unit
                ny += w.get(b"Y", 0.0) * unit
                nz += w.get(b"Z", 0.0) * unit
            if have_pos:
                dz = abs(nz - z)
                if mode == 0:
                    dt = max(math.hypot(nx - x, ny - y) / RAPID_XY, dz / RAPID_Z) * 60.0
                else:
                    if mode in (2, 3) and (b"I" in w or b"J" in w or b"R" in w):
                        dxy = _arc_length(x, y, nx, ny,
                                          w[b"I"] * unit if b"I" in w else None,
                                          w[b"J"] * unit if b"J" in w else None,
                                          w[b"R"] * unit if b"R" in w else None,
                                          mode == 2)
                    else:
                        dxy = math.hypot(nx - x, ny - y)
                    f = min(feed or FEED_CAP, FEED_CAP)
                    dt = max(math.hypot(dxy, dz) / max(f, 1.0), dz / PLUNGE_CAP) * 60.0
                floor = VBIT_FLOOR_S if tool in vbits else DEFAULT_FLOOR_S
                t += dt if dt > floor else floor
            if not machine_coords and mode in (0, 1, 2, 3):
                if b"X" in w or b"Y" in w:
                    bx0 = nx if bx0 is None else min(bx0, nx)
                    bx1 = nx if bx1 is None else max(bx1, nx)
                    by0 = ny if by0 is None else min(by0, ny)
                    by1 = ny if by1 is None else max(by1, ny)
                if b"Z" in w:
                    bz0 = nz if bz0 is None else min(bz0, nz)
                if mode and have_pos:
                    # the path of the bit's center, for the job's footprint on the plate
                    key = -1 if tool is None else tool
                    paths = cut_paths if min(z, nz) <= 1e-6 else air_paths
                    ras = paths.get(key)
                    if ras is None:
                        ras = paths[key] = platemap.Raster()
                    arc = None
                    if mode in (2, 3) and (b"I" in w or b"J" in w or b"R" in w):
                        arc = platemap.arc_center(x, y, nx, ny,
                                                  w[b"I"] * unit if b"I" in w else None,
                                                  w[b"J"] * unit if b"J" in w else None,
                                                  w[b"R"] * unit if b"R" in w else None, mode == 2)
                    if arc is not None:
                        ras.arc(*arc)
                    else:
                        ras.line(x, y, nx, ny)
            x, y, z = nx, ny, nz
            have_pos = True
        cum[k] = t

    total = t
    scale = 1.0
    if header_time and total > 0:
        r = header_time / total
        if 0.5 <= r <= 2.0:
            scale = r
    step = max(1, math.ceil((n + 1) / MAX_CHECKPOINTS))
    cp = [round(cum[i] * scale, 1) for i in range(0, n + 1, step)]
    if (n % step) != 0:
        cp.append(round(cum[n] * scale, 1))
    for ch in changes:
        ch["t"] = round(ch["t"] * scale, 1)
    used = []
    for ch in changes:
        if ch["tool"] not in used:
            used.append(ch["tool"])
    radii = {t: d / 2.0 for t, d in tool_d.items()}
    paths = cut_paths or air_paths
    foot = platemap.footprint(paths, radii, DEFAULT_BIT_D / 2.0)
    path_box = None
    for ras in paths.values():
        b = ras.box
        if b is not None:
            path_box = list(b) if path_box is None else [min(path_box[0], b[0]), min(path_box[1], b[1]),
                                                          max(path_box[2], b[2]), max(path_box[3], b[3])]
    return {
        "version": PROFILE_VERSION,
        "path": path,
        "name": os.path.basename(path) if path else "",
        "size": len(data),
        "lines": n,
        "md5": hashlib.md5(data).hexdigest(),
        "total": round(total * scale, 1),
        "model_total": round(total, 1),
        "header_time": header_time,
        "tools": {str(k): v for k, v in tools.items()},
        "tool_specs": {str(k): v for k, v in tool_specs.items()},
        "used_tools": used,
        "box": [round(bx0, 4), round(by0, 4), round(bx1, 4), round(by1, 4)] if bx0 is not None else None,
        "zmin": round(bz0, 4) if bz0 is not None else None,
        "tool_d": {str(k): v for k, v in tool_d.items()},
        "cut": foot.to_json() if foot else None,
        "cut_box": foot.bbox_mm() if foot else None,
        "path_box": [round(v, 3) for v in path_box] if path_box else None,
        "cut_above": not cut_paths and bool(air_paths),
        "stock": stock,
        "material": material,
        "notes": _header_notes(lines),
        "changes": changes,
        "cp_step": step,
        "cp": cp,
    }


class Profile:
    """Fast lookups on a profile dict."""

    def __init__(self, d: dict) -> None:
        self.d = d
        self.total = float(d["total"])
        self.step = int(d["cp_step"])
        self.cp = d["cp"]
        self.changes = d["changes"]
        self.lines = int(d["lines"])
        self.tools = {int(k): v for k, v in d.get("tools", {}).items()}
        self._change_lines = [c["line"] for c in self.changes]

    def time_at(self, line: int) -> float:
        """Estimated seconds when the machine starts the given 1-based line."""
        pos = max(0, min(self.lines, line - 1))
        i = pos // self.step
        if i >= len(self.cp) - 1:
            return float(self.cp[-1])
        a = self.cp[i]
        b = self.cp[i + 1]
        span = self.step if (i + 1) * self.step <= self.lines else max(1, self.lines - i * self.step)
        frac = (pos - i * self.step) / span
        t = a + (b - a) * min(1.0, frac)
        # The checkpoints are a few hundred lines apart, so the minute added at a tool
        # change would otherwise leak into the lines just before it. A line before a
        # change can't be later than the change, and a line after it can't be earlier.
        k = bisect.bisect_left(self._change_lines, line)
        if k < len(self.changes):
            t = min(t, self.changes[k]["t"])
        if k > 0:
            t = max(t, self.changes[k - 1]["t"])
        return t

    def next_change(self, line: int):
        """Index of the first tool change after the given line, or None."""
        for i, ch in enumerate(self.changes):
            if ch["line"] > line:
                return i
        return None

    def segments(self):
        """(tool, start fraction, end fraction) for each stretch of the job."""
        out = []
        if not self.changes or self.total <= 0:
            return out
        for i, ch in enumerate(self.changes):
            start = ch["t"] / self.total
            end = self.changes[i + 1]["t"] / self.total if i + 1 < len(self.changes) else 1.0
            if i == 0:
                start = 0.0
            out.append((ch["tool"], max(0.0, start), min(1.0, end)))
        return out
