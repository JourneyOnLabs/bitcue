"""Shared pieces for the tests: the made-up sample jobs (tools/samples.py), a library and a starting
plate record made from them, and settings for a test machine. Nothing here needs files from outside
this folder."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for p in (ROOT, os.path.join(ROOT, "tools"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import samples  # noqa: E402
from z1screen import config, gcode, platemap as pm  # noqa: E402

ANCHOR = [-200.0, -200.0]                   # Anchor 1 of the pretend machine (machine coordinates)
SAMPLES = "/sd/gcodes/samples"
SIGN = SAMPLES + "/sample-sign.nc"          # probe, T1, T2, T3, T1; states its run window
TAG = SAMPLES + "/sample-tag.nc"            # probe, T1, T3, T1; beside the sign
SHEET = "/sd/gcodes/sample-sheet"           # a plate layout: 4 discs
DISC = [SHEET + "/disc-position-%d.nc" % n for n in range(1, 5)]
PLATE = dict(samples.PLATE)                 # 200 x 150 x 6 mm plywood
SEED_CUT_BOX = (120.0, 90.0, 180.0, 140.0)  # "Sample cut" in the starting plate record


def check(c, msg):
    if not c:
        raise AssertionError(msg)
    print("  ok -", msg)


def sample_files() -> dict:
    files = {SIGN: samples.sign(), TAG: samples.tag()}
    for n in range(1, 5):
        files[DISC[n - 1]] = samples.disc(n)
    return files


_profiles = {}


def profile(path: str) -> dict:
    """The profile of one sample file (worked out once)."""
    if path not in _profiles:
        _profiles[path] = gcode.analyze(sample_files()[path], path)
    return copy.deepcopy(_profiles[path])


def make_library(paths=(SIGN, TAG), seed: bool = False) -> str:
    """A library folder with the profiles of these sample files, and, with seed=True, a starting plate
    record: the sample plate with "Sample cut" (a rectangle, cut) and the tag started part way."""
    d = tempfile.mkdtemp(prefix="z1lib-")
    for p in paths:
        prof = profile(p)
        with open(os.path.join(d, prof["md5"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(prof, fh)
    if seed:
        os.makedirs(os.path.join(d, "seed"))
        now = time.time() - 3600
        rec = {"version": 2, "seed": "tests",
               "plate": dict(PLATE, since=now, set=True),
               "cuts": [{"id": 1, "name": "Sample cut", "file": "", "state": "cut", "by": "kit", "t": now,
                         "shift": [0.0, 0.0], "layout": "", "spot": "",
                         "shape": pm.Grid.rect(*SEED_CUT_BOX).to_json()},
                        {"id": 2, "name": "Sample tag", "file": TAG, "state": "started", "by": "kit", "t": now,
                         "shift": [0.0, 0.0], "layout": "", "spot": "", "shape": profile(TAG)["cut"]}],
               "next_id": 3}
        with open(os.path.join(d, "seed", "plate.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
    return d


def cfg(**changes) -> dict:
    """Settings for a test machine: the defaults, with Anchor 1 known, the sample plate set, starting from
    the screen on and the sample sheet as a layout folder; then the changes given."""
    c = copy.deepcopy(config.DEFAULTS)
    c.update({"anchor1": list(ANCHOR), "plate": dict(PLATE), "start_from_screen": True, "plate_layouts": [SHEET]})
    c.update(changes)
    return c


def status(state, line=None, pct=0, el=0, tool=1, target=-1, halt=None, mpos=(-20.0, -20.0), wpos=(180.0, 180.0)):
    """A status report in the Z1 firmware's format."""
    s = ("<%s|MPos:%.4f,%.4f,-20.0000,0.0000,0.0000|WPos:%.4f,%.4f,60.0000,0.0000,0.0000|F:0,0,100|S:0,0,100,0"
         "|T:%d,-1.250,%d|W:0.00|L:0, 0, 0, 0.0,100.0" % (state, mpos[0], mpos[1], wpos[0], wpos[1], tool, target))
    if line is not None:
        s += "|P:%d,%d,%d" % (line, pct, el)
    if halt:
        s += "|H:%d" % halt
    return s + "|C:4,0,0,1>"
