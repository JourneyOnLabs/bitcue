"""Starting a job from the Pi's small touch screen.

Makera Studio starts a job with three commands, and the Pi sends the same three:

  G10L2P0X<x>Y<y>     work origin, as Apply Settings in the run window does:
                      the anchor's position plus the offsets
  buffer M495 X<x0>Y<y0>[C<x1>D<y1>][O<dx>F<dy>][A<w>B<h>I<i>J<j>H<z>][P1]
                      the run window's edge scan (C, D), Z probe (O, F, measured
                      from the job's lowest X and Y) and leveling (A, B: the size
                      of the job's box; I, J: points; H: clearance), done by the
                      machine at the start of the next play. P1 alone: just go
                      to the job's start point first.
  play <path>         start the file

The M495 numbers come from the job's box: the lowest and highest X and Y the
file moves to. This is how Makera's controller builds the command
(Carvera Community Controller, Controller.autoCommand and CNC.pathMargins), and
it matches what Makera Studio sends. For example, for a job whose box is
X10 Y10 to X70 Y50, with Anchor 1 at X-200 Y-200, offsets 0, the Z probe at
X5 Y5, edge scan on and leveling 5 x 5 with 2 mm clearance:
  G10L2P0X-200.000Y-200.000
  buffer M495 X10Y10C70D50O-5F-5A60B40I5J5H2
  play /sd/gcodes/parts/bracket.nc

A file that states its run window in its header (see "Notes in job files" in
README.md) runs with those settings. Any other file runs with the Pi's settings
for files without one (DEFAULT_RUN, or run_defaults in the settings): edge scan
on, Z probe in the middle of the part's cutting area, leveling off. Moving a
part on the plate moves its work origin: the G10 gets the move added, and the
box, edge scan and probe (all measured from the work origin) move with it.
"""

from __future__ import annotations

import os
import re


def fmt(v: float) -> str:
    """A number the way the run window writes it: up to 3 decimals, no trailing zeros."""
    s = ("%.3f" % float(v)).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def escape_path(path: str) -> str:
    """A path as the machine's console takes it (spaces and a few signs are coded)."""
    return (path.replace(" ", "\x01").replace("?", "\x02").replace("&", "\x03")
            .replace("!", "\x04").replace("~", "\x05"))


def origin_command(anchor_xy, offset) -> str:
    """Written with 3 decimals, as Makera Studio writes it (G10L2P0X-200.000Y-200.000)."""
    return "G10L2P0X%.3fY%.3f" % (anchor_xy[0] + offset[0], anchor_xy[1] + offset[1])


def m495_command(box, run) -> str:
    x0, y0, x1, y1 = box
    cmd = "M495 X%sY%s" % (fmt(x0), fmt(y0))
    extra = False
    if run.get("margin"):
        cmd += "C%sD%s" % (fmt(x1), fmt(y1))
        extra = True
    probe = run.get("probe")
    if probe:
        cmd += "O%sF%s" % (fmt(probe["x"] - x0), fmt(probe["y"] - y0))
        extra = True
    level = run.get("level")
    if level:
        cmd += "A%sB%sI%dJ%dH%s" % (fmt(x1 - x0), fmt(y1 - y0), int(level["i"]), int(level["j"]), fmt(level["h"]))
        extra = True
    if not extra:
        cmd += "P1"
    return cmd


def play_command(path: str) -> str:
    return "play " + escape_path(path)


DEFAULT_RUN = {"margin": True, "probe": "middle", "level": None}


def run_for(prof: dict, defaults=None):
    """(run settings, "file" or "default") for a job profile.

    The file's own run window if it states one; else the defaults, with the Z
    probe in the middle of the box of the bit's cutting path (bare plate when
    the job starts, since the job's area must be clear)."""
    notes = prof.get("notes") or {}
    if notes.get("run"):
        return notes["run"], "file"
    d = dict(DEFAULT_RUN, **(defaults or {}))
    run = {"anchor": 1, "offset": [0.0, 0.0], "margin": bool(d.get("margin")), "probe": None, "level": None}
    pb = prof.get("path_box") or prof.get("box")
    if d.get("probe") and pb:
        run["probe"] = {"rel": "work", "x": round((pb[0] + pb[2]) / 2.0, 1), "y": round((pb[1] + pb[3]) / 2.0, 1)}
    lv = d.get("level")
    if isinstance(lv, dict) and lv.get("i") and lv.get("j"):
        run["level"] = {"i": int(lv["i"]), "j": int(lv["j"]), "h": float(lv.get("h", 2))}
    return run, "default"


def commands(prof: dict, anchor_xy, run=None, extra=(0.0, 0.0)) -> list:
    """The three commands for a job, or raise ValueError saying why it can't be started.

    run: the run settings (the file's own if None); extra: how far the part is
    moved on the plate from the file's own place, in mm."""
    if run is None:
        run = (prof.get("notes") or {}).get("run")
    if not run:
        raise ValueError("This file doesn't state its run settings, so start it from Makera Studio.")
    box = prof.get("box")
    if not box:
        raise ValueError("This file has no moves.")
    if anchor_xy is None:
        raise ValueError("The Pi doesn't know where Anchor 1 is on this machine.")
    if run.get("anchor") != 1:
        raise ValueError("This file's origin is Anchor %s; the Pi only knows Anchor 1." % run.get("anchor"))
    off = run.get("offset") or [0, 0]
    return [origin_command(anchor_xy, [off[0] + extra[0], off[1] + extra[1]]),
            "buffer " + m495_command(box, run),
            play_command(prof["path"])]


# ---------------------------------------------------------------- words for the screens
DEFAULT_DESIGN_NAMES = (r"^(?P<design>.+?)-position-\d+(?:-.*)?$",)
_design_res = [re.compile(p, re.I) for p in DEFAULT_DESIGN_NAMES]


def set_design_names(patterns) -> None:
    """The settings' "design_names": regular expressions with a group named "design", tried in order on a
    file name without its extension. A pattern that doesn't compile or has no "design" group is skipped."""
    global _design_res
    out = []
    for p in patterns if isinstance(patterns, (list, tuple)) else DEFAULT_DESIGN_NAMES:
        try:
            r = re.compile(str(p), re.I)
        except re.error:
            continue
        if "design" in r.groupindex:
            out.append(r)
    _design_res = out


def design_key(path: str) -> str:
    """Files of one design at different spots share this key: bracket-position-2.nc -> bracket."""
    base = os.path.splitext(os.path.basename(path or ""))[0]
    for r in _design_res:
        m = r.match(base)
        if m and m.group("design"):
            return m.group("design")
    return base


def design_name(key: str) -> str:
    """bracket -> Bracket, wall-hook-v2 -> Wall Hook v2."""
    parts = [p for p in re.split(r"[-_ ]+", key or "") if p]
    out = []
    for p in parts:
        out.append(p if any(c.isdigit() for c in p) or p.isupper() else p[:1].upper() + p[1:])
    return " ".join(out) or key


def run_lines(run, extra=(0.0, 0.0)) -> list:
    """The run settings as short lines for the review screen (extra: the part's move on the plate, mm)."""
    if not run:
        return []
    off = run.get("offset") or [0, 0]
    ox, oy = off[0] + extra[0], off[1] + extra[1]
    origin = "Anchor %d" % run["anchor"] + ("" if not (ox or oy) else ", offset X%s Y%s" % (fmt(ox), fmt(oy)))
    probe = run.get("probe")
    level = run.get("level")
    return [
        "Origin: " + origin,
        "Z probe: " + ("X%s Y%s on the plate" % (fmt(probe["x"] + ox), fmt(probe["y"] + oy)) if probe else "off"),
        "Edge scan: " + ("on" if run.get("margin") else "off") + " · Leveling: " +
        ("%d × %d, %s mm" % (level["i"], level["j"], fmt(level["h"])) if level else "off"),
    ]


def thickness_check(prof: dict):
    """(plate thickness, deepest cut, thickest plate that still cuts through) in mm, or None."""
    stock = prof.get("stock") or {}
    zmin = prof.get("zmin")
    t = stock.get("height") or ((prof.get("notes") or {}).get("plate_note_dims") or [None, None, None])[2]
    if not t or zmin is None:
        return None
    depth = -float(zmin)
    return float(t), depth, round(depth - 0.10, 2)
