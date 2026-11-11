"""Practice runs: the screen playing a whole job without the machine.

build_timeline() runs a separate copy of the screen program through a made-up
run of one job file. The Z1 status reports it feeds in are written from the
file's own timing (the same estimate the screen uses), and it records what the
screen shows: Ready, every stop, and once a minute of job time while cutting.
The demo page and the monitor demo both play these recordings back, so what
they show is what the screen program itself produces.

DemoPlayer plays a recording at a chosen speed and waits at each stop until its
front button is pressed. Nothing here talks to the machine.
"""

from __future__ import annotations

import copy
import json
import os
import time

from . import gcode
from .gcode import Profile

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = "sample"                                     # the demo list's name for the built-in sample job
SAMPLE_FILE = os.path.join(HERE, "sample-job.json")   # its profile (made by tools/build_sample.py)
STEP_S = 60.0          # job seconds between recorded frames while cutting
_sample = {}


def sample_profile():
    """The built-in sample job's profile (a made-up sign with three bits), or None if it is missing."""
    if "d" not in _sample:
        d = None
        try:
            with open(SAMPLE_FILE, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if d.get("version") != gcode.PROFILE_VERSION:
                d = None
            elif d.get("tool_specs"):
                d["tools"] = {k: gcode.describe_tool(v) for k, v in d["tool_specs"].items()}
        except (OSError, ValueError):
            d = None
        _sample["d"] = d
    return copy.deepcopy(_sample["d"]) if _sample["d"] is not None else None
SPEEDS = (1.0, 60.0, 360.0)


class _OneJob:
    """Just enough of JobStore for a scratch copy of the hub."""

    state_dir = ""

    def __init__(self, prof: dict | None) -> None:
        self.d = prof
        self.listeners = []

    def find(self, path: str = "", size=None):
        return self.d

    def is_pending(self, path: str) -> bool:
        return False

    def count(self) -> int:
        return 1 if self.d else 0


def _status(state, line=None, pct=0, elapsed=0, tool=-1, target=-1, halt=None) -> str:
    """A status report in the Z1 firmware's format (see status.py)."""
    s = ("<%s|MPos:0.0000,0.0000,0.0000,0.0000,0.0000|WPos:0.0000,0.0000,0.0000,0.0000,0.0000"
         "|F:0.0,0.0,100.0|S:0.0,0.0,100.0,0|T:%d,0.000,%d|W:0.00" % (state, tool, target))
    if line is not None:
        s += "|P:%d,%d,%d" % (line, pct, elapsed)
    if halt:
        s += "|H:%d" % halt
    return s + "|C:4,0,0,1>"


def _scratch(cfg: dict, prof: dict | None, machine_name: str, settings_url: str):
    from .hub import Hub  # here, not at the top: hub.py imports this module
    hub = Hub(copy.deepcopy(cfg), _OneJob(prof))
    hub.m_name = machine_name or "Makera_Z1"
    hub.m_ip = "demo"
    if settings_url:
        hub.settings_url = lambda: settings_url
    return hub


def _line_at(prof: Profile, est: float) -> int:
    """The last line the machine has started at `est` seconds of job time."""
    lo, hi = 1, prof.lines
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if prof.time_at(mid) <= est:
            lo = mid
        else:
            hi = mid - 1
    return lo


def build_timeline(cfg: dict, prof_dict: dict, machine_name: str = "", settings_url: str = "",
                   step: float = STEP_S) -> dict:
    """Record the screen through a made-up run of one job file."""
    prof = Profile(prof_dict)
    path = prof_dict.get("path") or "/sd/gcodes/" + (prof_dict.get("name") or "job.nc")
    hub = _scratch(cfg, prof_dict, machine_name, settings_url)
    lines = max(1, prof.lines)
    frames = []

    def snap(kind, est, **extra):
        f = {"k": kind, "est": round(est, 1), "view": hub.compute_view()}
        f.update(extra)
        frames.append(f)

    def pct(line):
        return int(100 * line / lines)

    changes = prof.changes
    tool = changes[-1]["tool"] if changes else 1   # the bit left in from the last run of this file
    hub.on_status(_status("Idle", tool=tool))
    snap("ready", 0.0)

    hub.on_app_command("play " + path.replace(" ", "\x01"))
    # With Auto Z Probe on, the Z1 first stops for the probe.
    hub.on_status(_status("Tool", 0, 0, 0, tool=tool, target=0))
    snap("stop", 0.0, tool=0)
    tool = 0

    est = 0.0
    first = True
    for ch in changes + [None]:
        end = ch["t"] if ch else prof.total
        while est < end:
            # The firmware reports the line of the last cutting move (0 before the first one),
            # and never a line past the tool change it has not reached yet.
            line = 0 if first else _line_at(prof, est)
            if ch is not None:
                line = min(line, ch["line"] - 1)
            hub.on_status(_status("Run", line, pct(line), round(est), tool=tool))
            snap("run", est)
            est += step
        est = end
        if ch is None:
            break
        hold = 0 if first else max(1, ch["line"] - 3)   # the last cutting line before the change
        hub.on_status(_status("Tool", hold, pct(hold), int(end), tool=tool, target=ch["tool"]))
        snap("stop", end, tool=ch["tool"])
        tool = ch["tool"]
        first = False

    hub.on_status(_status("Run", lines, 100, int(prof.total), tool=tool))
    snap("run", prof.total)
    hub.on_status(_status("Idle", tool=tool))
    if hub.job is not None:
        hub.job["seen"] -= 10                    # the firmware stops sending progress
    hub.on_console_line("Done printing file")
    hub.on_status(_status("Idle", tool=tool))
    snap("done", prof.total)

    # Two things that can go wrong, for the demo page's "what if" buttons.
    extras = {}
    probe_hub = _scratch(cfg, prof_dict, machine_name, settings_url)
    probe_hub.on_app_command("play " + path.replace(" ", "\x01"))
    probe_hub.on_status(_status("Alarm", 0, 0, 0, tool=0, halt=12))
    extras["probe"] = probe_hub.compute_view()
    hand_hub = _scratch(cfg, prof_dict, machine_name, settings_url)
    middle = changes[len(changes) // 2] if changes else {"t": prof.total / 2, "tool": 1}
    mid = min(prof.total, middle["t"] + 600)
    line = _line_at(prof, mid)
    hand_hub.on_app_command("play " + path.replace(" ", "\x01"))
    hand_hub.on_status(_status("Alarm", line, pct(line), int(mid), tool=middle["tool"], halt=1))
    extras["hand"] = hand_hub.compute_view()

    return {
        "version": 1,
        "path": path,
        "name": hub._pretty(path),
        "total": prof.total,
        "step": step,
        "changes": len(changes),
        "frames": frames,
        "extras": extras,
    }


def build_check(cfg: dict, tool: int, entry: dict | None = None, machine_name: str = "",
                settings_url: str = "") -> dict:
    """One frame: the tool-change screen for one bit, to compare the color with its sticker."""
    c = copy.deepcopy(cfg)
    if entry:
        c.setdefault("tools", {})[str(tool)] = dict(entry)
    hub = _scratch(c, None, machine_name, settings_url)
    hub.on_status(_status("Tool", 0, 0, 0, tool=-1, target=tool))
    v = hub.compute_view()
    if v.get("job"):
        v["job"]["name"] = "Color check"
    return {"version": 1, "path": "", "name": "Color check", "total": 0.0, "step": 0, "changes": 0,
            "frames": [{"k": "check", "est": 0.0, "view": v, "tool": tool}], "extras": {}}


class DemoPlayer:
    """Plays a recording. Waits at each stop until press() is called."""

    READY_S = 3.0          # real seconds on Ready before the job starts
    DONE_S = 30.0          # real seconds on the finished screen before the demo ends
    LIMIT_S = 30 * 60.0    # a demo always ends after this many real seconds
    CHECK_S = 20.0         # how long a color check stays on the monitor

    def __init__(self, timeline: dict, speed: float = 60.0, kind: str = "demo", limit: float | None = None,
                 now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.tl = timeline
        self.frames = timeline["frames"]
        self.kind = kind
        self.speed = self._valid_speed(speed)
        self.started = now
        self.last = now
        self.limit = float(limit if limit is not None else (self.CHECK_S if kind == "check" else self.LIMIT_S))
        self.i = 0
        self.est = 0.0
        self.waiting = False
        self.clock = now          # time of day shown on the screen; runs at the demo speed
        self.done_at = None
        self.done_clock = None
        self.presses = 0

    @staticmethod
    def _valid_speed(speed) -> float:
        try:
            s = float(speed)
        except (TypeError, ValueError):
            return 60.0
        return s if s in SPEEDS else 60.0

    def set_speed(self, speed) -> None:
        self.speed = self._valid_speed(speed)

    def tick(self, now: float) -> None:
        dt = max(0.0, min(5.0, now - self.last))
        self.last = now
        if self.kind != "demo" or self.waiting or self.done_at is not None:
            self.clock += dt   # people take real time at a stop
            return
        fr = self.frames
        if fr[self.i]["k"] == "ready":
            self.clock += dt
            if now - self.started >= self.READY_S and self.i + 1 < len(fr):
                self._enter(self.i + 1, now)
            return
        self.clock += dt * self.speed
        self.est += dt * self.speed
        while self.i + 1 < len(fr) and fr[self.i + 1]["est"] <= self.est:
            if self._enter(self.i + 1, now):
                return

    def _enter(self, i: int, now: float) -> bool:
        """Move to frame i. True if the player has to stop there."""
        self.i = i
        f = self.frames[i]
        if f["k"] == "stop":
            self.waiting = True
            self.est = f["est"]
            return True
        if f["k"] == "done":
            self.done_at = now
            self.done_clock = self.clock
            self.est = f["est"]
            return True
        return False

    def press(self, now: float) -> bool:
        """The front button: carries on after a stop. Does nothing at other times."""
        self.tick(now)
        if not self.waiting:
            return False
        self.waiting = False
        self.presses += 1
        if self.i + 1 < len(self.frames):
            self.i += 1
        return True

    def skip(self, now: float) -> bool:
        """Jump to a minute and a half before the next stop (or the end)."""
        self.tick(now)
        if self.kind != "demo" or self.waiting or self.done_at is not None:
            return False
        fr = self.frames
        if fr[self.i]["k"] == "ready":
            self._enter(self.i + 1, now)
            return True
        j = next((k for k in range(self.i + 1, len(fr)) if fr[k]["k"] in ("stop", "done")), None)
        if j is None:
            return False
        target = max(self.est, fr[j]["est"] - 90.0)
        self.clock += max(0.0, target - self.est)
        self.est = target
        while self.i + 1 < j and fr[self.i + 1]["est"] <= self.est:
            self.i += 1
        return True

    def finished(self, now: float) -> str:
        if now - self.started > self.limit:
            return "time"
        if self.done_at is not None and now - self.done_at > self.DONE_S:
            return "done"
        return ""

    def ends_in(self, now: float) -> float:
        left = self.limit - (now - self.started)
        if self.done_at is not None:
            left = min(left, self.DONE_S - (now - self.done_at))
        return max(0.0, left)

    def view(self, now: float) -> dict:
        v = copy.deepcopy(self.frames[self.i]["view"])
        v["clock"] = round(self.clock, 1)
        if v.get("last_job"):
            v["last_job"]["ended"] = round(self.done_clock or self.clock, 1)
        v["demo"] = {
            "kind": self.kind,
            "ends_in": round(self.ends_in(now)),
            "waiting": self.waiting,
            "speed": self.speed,
        }
        return v

    def status(self, now: float) -> dict:
        f = self.frames[self.i]
        st = {
            "active": True,
            "kind": self.kind,
            "job": self.tl.get("name", ""),
            "phase": f["k"],
            "waiting": self.waiting,
            "speed": self.speed,
            "est": round(self.est),
            "total": round(self.tl.get("total", 0)),
            "ends_in": round(self.ends_in(now)),
            "stops_done": self.presses,
        }
        tc = f["view"].get("toolchange")
        if tc:
            st["tool"] = {k: tc.get(k) for k in ("tool", "code", "label", "sticker", "color", "unset")}
        return st


def pretty_job_name(path: str) -> str:
    base = os.path.basename(path or "")
    return base[:-3] if base.lower().endswith(".nc") else base
