"""The small screen's touch layer: reading touches, calibrating them, and the job screens.

The Adafruit PiTFT Plus 2.8" (product 2298) has a resistive touch layer read by
an STMPE610 chip. Linux reports it as an input device (/dev/input/eventN,
named "stmpe-ts") with raw readings from 0 to 4095. calibrate() turns raw
readings into screen points from four dots the person touches once. The
calibration is saved with the size of the screen it was made on (Panel.screen),
and the screen service gives it back only to that screen: the 3.5inch DPI
screen's Goodix touch layer reads in a range of its own. With none saved for
it, the 3.5inch DPI screen gets a built-in one, so it works without the dots.

Panel is the touch part of the screen: the JOBS button on the Ready screen, the
list of folders and files on the Z1, the plate with what is already cut on it,
placing a part (touch or drag it, nudge it with the arrows; the Pi checks it
stays clear of every cut area), the spot map of a plate layout, the plate check
(with NEW PLATE: a bare plate, its size and thickness typed on a number pad, and
LAST PLATE, which brings back the record NEW PLATE replaced) and the start
screen (hold START for 2 seconds). It asks the screen service on
this Pi for the data (http://127.0.0.1:8080/api/...) and to start a job
(/api/start, which the service only accepts from the Pi itself, and checks the
place again).
"""

from __future__ import annotations

import glob
import json
import logging
import math
import os
import queue
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import platemap as pm
from . import start as start_mod

log = logging.getLogger("z1screen.touch")

W, H = 320, 240
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE = 0x00, 0x01, 0x18
ABS_MT_X, ABS_MT_Y = 0x35, 0x36
BTN_TOUCH = 0x14A
SYSFS_INPUT = "/sys/class/input"
DEVDIR_INPUT = "/dev/input"
NAMES = ("stmpe", "touch", "ads7846", "tsc2007", "ft5", "ft6", "goodix", "edt-ft")

CAL_POINTS = [(40, 40), (280, 40), (280, 200), (40, 200)]
HOLD_START_S = 2.0       # hold START this long
HOLD_SPOT_S = 1.5        # hold a spot this long to mark it cut or free
IDLE_CLOSE_S = 120.0     # back to the Ready screen after this long without a touch
ROWS = 4                 # list rows per page


# ---------------------------------------------------------------- reading touches
def find_device(sysfs: str = "", devdir: str = "") -> str:
    """The touch layer's /dev/input/eventN, or ""."""
    sysfs, devdir = sysfs or SYSFS_INPUT, devdir or DEVDIR_INPUT
    for p in sorted(glob.glob(os.path.join(sysfs, "event[0-9]*")), key=lambda s: int(s.rsplit("event", 1)[1])):
        try:
            with open(os.path.join(p, "device", "name")) as fh:
                name = fh.read().strip().lower()
        except OSError:
            continue
        if any(k in name for k in NAMES):
            return os.path.join(devdir, os.path.basename(p))
    return ""


class EventParser:
    """Turns Linux input events into touches: ("down" | "move" | "up", raw x, raw y, time)."""

    FMT = "llHHi" if struct.calcsize("llHHi") == 24 else "qqHHi"

    def __init__(self) -> None:
        self.size = struct.calcsize(self.FMT)
        self.buf = b""
        self.x = self.y = None
        self.pressure = None
        self.btn = None
        self.down = False

    def feed(self, data: bytes) -> list:
        out = []
        self.buf += data
        while len(self.buf) >= self.size:
            sec, usec, typ, code, value = struct.unpack(self.FMT, self.buf[:self.size])
            self.buf = self.buf[self.size:]
            if typ == EV_ABS:
                if code in (ABS_X, ABS_MT_X):
                    self.x = value
                elif code in (ABS_Y, ABS_MT_Y):
                    self.y = value
                elif code == ABS_PRESSURE:
                    self.pressure = value
            elif typ == EV_KEY and code == BTN_TOUCH:
                self.btn = bool(value)
            elif typ == EV_SYN and code == 0:
                pressed = self.btn if self.btn is not None else bool(self.pressure)
                t = sec + usec / 1e6
                if pressed and self.x is not None and self.y is not None:
                    out.append(("move" if self.down else "down", self.x, self.y, t))
                    self.down = True
                elif not pressed and self.down:
                    out.append(("up", self.x, self.y, t))
                    self.down = False
        return out


class TouchInput:
    """Reads the touch device on a thread; touches() hands over what came in."""

    def __init__(self, device: str = "") -> None:
        self.device = device
        self.q = queue.Queue()
        self.thread = None
        self.stop = False
        self.next_try = 0.0
        self.warned = False

    def ok(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def ensure(self, now: float) -> None:
        if self.ok() or now < self.next_try:
            return
        self.next_try = now + 10.0
        dev = self.device or find_device()
        if not dev:
            if not self.warned:
                log.info("no touch screen found (checking every 10 s)")
                self.warned = True
            return
        try:
            fd = os.open(dev, os.O_RDONLY)
        except OSError as exc:
            if not self.warned:
                log.warning("can't read the touch screen %s (%s)", dev, exc)
                self.warned = True
            return
        log.info("touch screen: %s", dev)
        self.thread = threading.Thread(target=self._read, args=(fd,), daemon=True)
        self.thread.start()

    def _read(self, fd: int) -> None:
        parser = EventParser()
        try:
            while not self.stop:
                data = os.read(fd, 24 * 64)
                if not data:
                    break
                for ev in parser.feed(data):
                    self.q.put(ev)
        except OSError as exc:
            log.warning("touch screen read failed (%s)", exc)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def touches(self, wait: float = 0.0) -> list:
        out = []
        try:
            out.append(self.q.get(timeout=wait) if wait > 0 else self.q.get_nowait())
            while True:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out


# ---------------------------------------------------------------- calibration
def _solve3(a, b):
    """Solve a 3 x 3 linear system (Gaussian elimination with pivoting)."""
    m = [list(a[i]) + [b[i]] for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-12:
            raise ValueError("singular")
        m[c], m[p] = m[p], m[c]
        for r in range(3):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, 4):
                    m[r][k] -= f * m[c][k]
    return [m[i][3] / m[i][i] for i in range(3)]


def calibrate(raw_points, screen_points=CAL_POINTS, flip: bool = False) -> dict:
    """An affine map raw -> screen from touches on known dots. Raises ValueError if they don't line up."""
    if len(raw_points) != len(screen_points) or len(raw_points) < 3:
        raise ValueError("Not enough touches.")
    s = [[0.0] * 3 for _ in range(3)]
    bx, by = [0.0] * 3, [0.0] * 3
    for (rx, ry), (sx, sy) in zip(raw_points, screen_points):
        v = (rx, ry, 1.0)
        for i in range(3):
            for j in range(3):
                s[i][j] += v[i] * v[j]
            bx[i] += v[i] * sx
            by[i] += v[i] * sy
    try:
        ax, ay = _solve3(s, bx), _solve3(s, by)
    except ValueError:
        raise ValueError("The touches didn't line up.")
    cal = {"ax": ax, "ay": ay, "flip": bool(flip)}
    for (rx, ry), (sx, sy) in zip(raw_points, screen_points):
        px, py = apply(cal, rx, ry, flip)
        if abs(px - sx) > 14 or abs(py - sy) > 14:
            raise ValueError("The touches didn't line up.")
    return cal


def apply(cal: dict, rx: float, ry: float, flip: bool):
    """Raw reading -> point on the screen as it is drawn (before it is turned for an upside-down mount)."""
    ax, ay = cal["ax"], cal["ay"]
    x = ax[0] * rx + ax[1] * ry + ax[2]
    y = ay[0] * rx + ay[1] * ry + ay[2]
    if bool(cal.get("flip")) != bool(flip):
        x, y = W - 1 - x, H - 1 - y
    return x, y


# ---------------------------------------------------------------- talking to the screen service
class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path: str, timeout: float = 4.0):
        with self.opener.open(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path: str, data: dict, timeout: float = 20.0):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode("utf-8"))
            except Exception:
                return {"error": "The screen service answered %d." % exc.code}


def _q(**kw) -> str:
    return "?" + urllib.parse.urlencode(kw)


# ---------------------------------------------------------------- the job screens
STEPS = [(1, "0.5 mm"), (2, "1 mm"), (10, "5 mm")]     # cells moved by one arrow press
NP_FIELDS = (("w", "WIDTH X"), ("h", "DEPTH Y"), ("t", "THICKNESS"))       # NEW PLATE's boxes
NP_LIMITS = {"w": (10, 400), "h": (10, 400), "t": (0.1, 100)}             # as on the settings page (plates.set_size)
DEFAULT_STEP = 1
READ_POLL_S = 0.6


class Panel:
    """What the touch screens show and do. layout() in tft.py draws self.ui and reports the buttons."""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.ui = None              # None: the normal screens; else a dict describing the touch screen
        self.mode = ""              # the screen service's mode (idle, running, toolchange ...)
        self.hits = []              # [(x, y, w, h, action)] from the last drawing
        self.press = None           # {"action", "t", "x", "y", "fired"}
        self.last_touch = 0.0
        self.work = None            # a request running on a thread: {"thread", "result", "then"}
        self.cal = None             # touch calibration from the service
        self.cal_raw = []
        self.cal_request_seen = None
        self.screen = None          # (width, height) of the framebuffer drawn on, set by tft.run
        self.jobs = None
        self.folder = None
        self.design = None
        self.selected = None
        self.plate_checked = False
        self.newplate = None        # the NEW PLATE screen: {"vals", "field", "fresh", "material", "cuts", "previous"}
        self.flow = ""              # "spot": a design of a plate layout; "place": a file placed freely
        self.place = None           # the placing screen's working data
        self.path = None            # the file being placed or read

    # ---------------- state from the service
    def set_calibration(self, cal) -> None:
        self.cal = cal if isinstance(cal, dict) else None

    def ready(self) -> bool:
        return self.cal is not None

    def watch(self, v: dict, now: float) -> None:
        """Close the touch screens when the machine gets busy or the screen sits untouched."""
        self.mode = v.get("mode")
        req = v.get("touch_request")
        if self.cal_request_seen is None:
            self.cal_request_seen = req
        elif req is not None and req != self.cal_request_seen and self.mode == "idle":
            self.cal_request_seen = req          # (asked during a job: shown once the Z1 is idle)
            self.open_calibration()
        if self.ui is None:
            return
        scr = self.ui.get("screen")
        busy = v.get("mode") in ("running", "paused", "toolchange", "alarm")
        if scr == "starting":
            if busy:
                self.close()
            elif self.work is None and now - self.ui.setdefault("t", now) > 45:
                self.message("No start seen", "The Z1 hasn't started moving. Check it, and Makera Studio.")
            return
        if busy or v.get("mode") not in ("idle",) or now - self.last_touch > IDLE_CLOSE_S:
            if scr == "reading" and v.get("mode") in ("idle", "connecting") and not busy \
                    and now - self.last_touch <= IDLE_CLOSE_S:
                return                           # no status polls while the Pi reads a file
            self.close()

    def close(self) -> None:
        self.ui = None
        self.press = None
        self.selected = None
        self.plate_checked = False
        self.newplate = None
        self.place = None

    # ---------------- background requests
    def _run(self, fn, then) -> None:
        box = {"result": None, "error": None}

        def job():
            try:
                box["result"] = fn()
            except Exception as exc:
                box["error"] = str(exc) or exc.__class__.__name__
        t = threading.Thread(target=job, daemon=True)
        self.work = {"thread": t, "box": box, "then": then}
        t.start()

    def tick(self, now: float) -> bool:
        """Finish background requests, long presses and drags. True if the screen changed."""
        changed = False
        w = self.work
        if w and not w["thread"].is_alive():
            self.work = None
            box = w["box"]
            if box["error"] is not None:
                self.message("The Pi couldn't do that", box["error"])
            else:
                w["then"](box["result"])
            changed = True
        p = self.press
        if p and not p.get("fired"):
            held = now - p["t"]
            a = p["action"]
            if a and a[0] == "hold_start":
                changed = True
                if held >= HOLD_START_S:
                    p["fired"] = True
                    self.do(("start",))
            elif a and a[0] == "spot" and held >= HOLD_SPOT_S:
                p["fired"] = True
                self.do(("toggle_spot", a[1]))
                changed = True
        pl = self.place
        if pl is not None and pl.get("drag") is not None and self.ui and self.ui.get("screen") == "place":
            x, y = pl.pop("drag")
            self._place_at(x, y)
            changed = True
        if self.ui and self.ui.get("screen") == "reading" and self.work is None and now >= self.ui.get("poll_at", 0):
            path = self.ui.get("path")
            self.ui["poll_at"] = now + 30
            self._run(lambda: self.api.get("/api/read" + _q(path=path)), self._got_read)
        return changed

    def hold_progress(self, now: float) -> float:
        p = self.press
        if p and p["action"] and p["action"][0] == "hold_start" and not p.get("fired"):
            return min(1.0, (now - p["t"]) / HOLD_START_S)
        return 0.0

    # ---------------- touches
    def hit(self, x: float, y: float):
        for hx, hy, hw, hh, action in reversed(self.hits):
            pad = 0 if (action and action[0] == "plate_area") else 4
            if action and hx - pad <= x <= hx + hw + pad and hy - pad <= y <= hy + hh + pad:
                if action[0] == "spot":
                    cx, cy, r = hx + hw / 2.0, hy + hh / 2.0, hw / 2.0
                    if (x - cx) ** 2 + (y - cy) ** 2 > (r + 4) ** 2:
                        continue
                return action
        return None

    def touch(self, kind: str, rx: float, ry: float, t: float, flip: bool) -> bool:
        """One touch event (raw reading). True if the screen should be drawn again."""
        self.last_touch = time.time()
        if self.ui and self.ui.get("screen") == "calibrate":
            if kind == "up":
                return self._cal_touch(rx, ry, flip)
            return False
        if self.cal is None:
            if kind == "up" and self.mode == "idle":     # never over a job's screen
                self.open_calibration()
                return True
            return False
        x, y = apply(self.cal, rx, ry, flip)
        if kind == "down":
            a = self.hit(x, y)
            self.press = {"action": a, "t": time.time(), "x": x, "y": y, "fired": False}
            if a and a[0] == "plate_area" and self.place is not None:
                self.place["drag"] = (x, y)
            return True
        if kind == "move":
            p = self.press
            if p and p["action"] and p["action"][0] == "plate_area" and self.place is not None:
                self.place["drag"] = (x, y)          # the part follows the finger
                return True
            if p and p["action"] and self.hit(x, y) != p["action"]:
                self.press = {"action": None, "t": p["t"], "x": x, "y": y, "fired": True}   # slid off: cancel
                return True
            return False
        p, self.press = self.press, None
        if not p or p.get("fired") or not p["action"]:
            return True
        a = p["action"]
        if a[0] == "plate_area":
            return True
        if a[0] == "hold_start":
            if self.ui is not None:
                self.ui["note"] = "Keep holding START for 2 seconds."
            return True
        if self.hit(x, y) == a:
            self.do(a)
        return True

    # ---------------- calibration
    def open_calibration(self) -> None:
        self.cal_raw = []
        self.ui = {"screen": "calibrate", "step": 0, "target": CAL_POINTS[0],
                   "text": "Touch the middle of the dot. 4 dots, one after another."}
        self.last_touch = time.time()

    def _cal_touch(self, rx, ry, flip) -> bool:
        self.cal_raw.append((rx, ry))
        step = len(self.cal_raw)
        if step < len(CAL_POINTS):
            self.ui = {"screen": "calibrate", "step": step, "target": CAL_POINTS[step],
                       "text": "Touch the middle of the dot. %d of 4." % (step + 1)}
            return True
        try:
            cal = calibrate(self.cal_raw, flip=flip)
        except ValueError as exc:
            self.cal_raw = []
            self.ui = {"screen": "calibrate", "step": 0, "target": CAL_POINTS[0],
                       "text": str(exc) + " Touch the middle of the dot again, 4 dots."}
            return True
        cal["screen"] = self.screen         # the screen it was made on
        self.cal = cal
        self._run(lambda: self.api.post("/api/touch", {"cal": cal}),
                  lambda res: self.message("Touch screen set", "Touches now land where you press.", button="OK"))
        self.ui = {"screen": "busy", "text": "Saving"}
        return True

    # ---------------- actions
    def message(self, title: str, text: str, button: str = "OK", then=("ok",)) -> None:
        self.ui = {"screen": "message", "title": title, "text": text, "button": button, "then": then}

    def do(self, a) -> None:
        kind = a[0]
        if kind == "next_bit":            # at a bit-change stop: another bit of the same kind (the record only)
            slot = a[1]
            self._run(lambda: self.api.post("/api/bitlife", {"action": "next", "slot": slot}), lambda res: None)
            return
        if kind == "jobs":
            self.ui = {"screen": "busy", "text": "Reading the Z1's folders"}
            self._run(lambda: self.api.get("/api/jobs" + _q(refresh=1), timeout=20), self._got_jobs)
        elif kind == "home":
            self.close()
        elif kind == "page":
            if self.ui:
                self.ui["page"] = max(0, self.ui.get("page", 0) + a[1])
        elif kind == "folder":
            self.folder = a[1]
            self._show_items()
        elif kind == "back_folders":
            self._show_folders()
        elif kind == "back_items":
            self.place = None
            self._show_items()
        elif kind == "design":
            self.flow = "spot"
            self.design = a[1]
            self.selected = None
            self.ui = {"screen": "busy", "text": "Reading the plate"}
            self._run(lambda: self.api.get("/api/design" + _q(folder=self.folder, design=self.design)),
                      self._got_design)
        elif kind == "file":
            self.flow = "place"
            self.path = a[1]
            self.place = None
            self.ui = {"screen": "busy", "text": "Working out where it fits"}
            path = a[1]
            self._run(lambda: self.api.get("/api/place" + _q(path=path), timeout=60), self._got_place)
        elif kind == "unread":
            self.message("Not read yet", a[1])
        elif kind == "spot":
            if self.ui and self.ui.get("screen") == "plate":
                spot = next((s for s in self.ui["data"]["spots"] if s["key"] == a[1]), None)
                if spot and spot["mine"] and spot["state"] == "free" and spot.get("why"):
                    self.ui["note"] = "Spot %d: %s" % (spot["n"], spot["why"])
                elif spot and spot["mine"] and spot["state"] == "free":
                    self.selected = a[1]
                    self.ui["selected"] = a[1]
                    self.ui["note"] = ""
                elif spot and spot["state"] == "taken":
                    self.ui["note"] = "Spot %d is covered by another cut area." % spot["n"]
                elif spot and spot["state"] != "free":
                    self.ui["note"] = "Spot %d is already cut. Hold it 2 s if the plate is bare there." % spot["n"]
                elif spot:
                    self.ui["note"] = "There's no %s file for spot %d." % (self.ui["data"]["name"], spot["n"])
        elif kind == "toggle_spot":
            d = (self.ui or {}).get("data") or {}
            spot = next((s for s in d.get("spots", []) if s["key"] == a[1]), None)
            if spot and spot["state"] == "taken":
                self.ui["note"] = ("Spot %d is covered by another cut area. Remove that area on the settings "
                                   "page if the plate is bare there." % spot["n"])
            elif spot:
                act = "free" if spot["state"] != "free" else "cut"
                folder, design = self.folder, self.design
                self._run(lambda: self.api.post("/api/plate", {"action": "spot", "folder": folder, "key": a[1],
                                                               "state": act, "design": design}),
                          lambda res: self.do(("design", self.design)))
        elif kind == "back_plate":
            if self.flow == "place" and self.place is not None:
                self._update_place()
            else:
                self.do(("design", self.design))
        elif kind == "nudge":
            if self.place is not None:
                n = STEPS[self.place["step"]][0]
                self._move_to(self.place["dc"] + a[1] * n, self.place["dr"] + a[2] * n)
        elif kind == "step":
            if self.place is not None:
                self.place["step"] = (self.place["step"] + 1) % len(STEPS)
                self._update_place()
        elif kind == "to_check":
            if self.flow == "place":
                self._show_place_check()
            else:
                self._show_check()
        elif kind == "new_plate":
            self._open_new_plate()
        elif kind == "np_field":
            if self.newplate:
                self.newplate.update(field=a[1], fresh=True)
                self._show_new_plate()
        elif kind == "np_key":
            if self.newplate:
                self._np_key(a[1])
                self._show_new_plate()
        elif kind == "np_start":
            self._start_new_plate()
        elif kind == "last_plate":
            again = ("file", self.path) if self.flow == "place" else ("design", self.design)
            self.ui = {"screen": "busy", "text": "Bringing back the last plate"}
            self._run(lambda: self.api.post("/api/plate", {"action": "previous"}), lambda res: self.do(again))
        elif kind == "checked":
            self.plate_checked = True
            if self.flow == "place":
                self._show_place_review()
            else:
                self._show_review()
        elif kind == "back_check":
            if self.flow == "place":
                self._show_place_check()
            else:
                self._show_check()
        elif kind == "start":
            if self.flow == "place":
                p = self.place
                if not p:
                    return
                path = p["d"]["path"]
                offset = [p["dc"] * pm.RES, p["dr"] * pm.RES]
            else:
                f = self._file()
                if not f:
                    return
                path, offset = f["path"], [0, 0]
            self.ui = {"screen": "starting", "text": "Starting %s" % (self.ui or {}).get("title", "")}
            checked = self.plate_checked
            self._run(lambda: self.api.post("/api/start", {"path": path, "plate_checked": checked,
                                                           "offset": offset}), self._started)
        elif kind == "ok":
            then = (self.ui or {}).get("then") or ("ok",)
            if then[0] == "ok":
                self.close()
            else:
                self.do(then)

    # ---------------- the job list
    def _got_jobs(self, res) -> None:
        self.jobs = res or {}
        url = self.jobs.get("settings_url") or "the settings page"
        if self.jobs.get("start_enabled") is False:
            self.message("Starting is off", "Turn it on in Setup at %s" % url)
            return
        if self.jobs.get("plate_set") is False:
            self.message("Plate size not set", "Set it at %s" % url)
            return
        self._show_folders()

    def _show_folders(self) -> None:
        folders = (self.jobs or {}).get("folders") or []
        items = []
        for f in folders:
            n = len(f["items"])
            if f.get("layout"):
                sub = "%d design%s · spot map" % (n, "" if n == 1 else "s")
            else:
                unread = sum(1 for it in f["items"] if not it.get("read"))
                sub = "%d file%s" % (n, "" if n == 1 else "s") + (" · %d not read yet" % unread if unread else "")
            items.append({"name": f["name"], "sub": sub, "action": ("folder", f["path"])})
        self.ui = {"screen": "list", "title": "JOBS ON THE Z1", "items": items, "page": 0, "back": ("home",),
                   "empty": "No job files found. Upload them to the Z1 from Makera Studio."}

    def _show_items(self) -> None:
        f = next((x for x in (self.jobs or {}).get("folders") or [] if x["path"] == self.folder), None)
        if f is None:
            self._show_folders()
            return
        items = []
        blocked = (self.jobs or {}).get("read_blocked") or ""
        for it in f["items"]:
            if it["kind"] == "design":
                spots = ("%d spots" % it["spots"]) if it["spots"] > 1 else "1 spot"
                sub = "%s · %s" % (spots, _dur(it.get("time")))
                if not it.get("startable"):
                    sub = spots + " · start in Makera Studio"
                items.append({"name": it["name"], "sub": sub, "action": ("design", it["key"]),
                              "preview": it.get("preview") or ""})
            elif not it.get("read"):
                if blocked:
                    items.append({"name": it["name"], "sub": "Not read yet",
                                  "action": ("unread", blocked)})
                else:
                    items.append({"name": it["name"], "sub": "Not read yet · tap to read it",
                                  "action": ("file", it["path"])})
            else:
                sub = _dur(it.get("time")) + " · place on the plate"
                if it.get("why_not"):
                    sub = "start in Makera Studio"
                items.append({"name": it["name"], "sub": sub, "preview": it.get("preview") or "",
                              "action": ("file", it["path"]) if not it.get("why_not") else ("unread", it["why_not"])})
        self.ui = {"screen": "list", "title": f["name"].upper(), "items": items, "page": 0,
                   "back": ("back_folders",), "empty": "No job files in this folder."}

    # ---------------- reading a file from the Z1
    def _got_read(self, st) -> None:
        st = st or {}
        if self.ui is None or self.ui.get("screen") not in ("reading", "busy"):
            return
        state = st.get("state")
        if state == "error" or (st.get("error") and state != "done"):
            self.message("Couldn't read the file", st.get("error") or "The Z1 didn't send the file.",
                         then=("back_items",))
        elif state == "done":
            self.do(("file", self.path))
        else:
            name = (self.ui or {}).get("title") or ""
            self.ui = {"screen": "reading", "title": name, "path": self.path, "pct": int(st.get("pct") or 0),
                       "profiling": state == "profiling", "poll_at": time.time() + READ_POLL_S,
                       "back": ("back_items",)}

    # ---------------- placing a file on the plate
    def _got_place(self, d) -> None:
        d = d or {}
        if not d.get("read"):
            r = d.get("reading") or {}
            if r.get("state") in ("waiting", "reading", "profiling"):
                self.ui = {"screen": "reading", "title": d.get("name", ""), "path": self.path, "pct": r.get("pct", 0),
                           "poll_at": time.time() + READ_POLL_S, "back": ("back_items",)}
                return
            if d.get("read_blocked"):
                self.message("Can't read it now", d["read_blocked"], then=("back_items",))
                return
            path = self.path
            self.ui = {"screen": "reading", "title": d.get("name", ""), "path": path, "pct": 0,
                       "poll_at": time.time() + 30, "back": ("back_items",)}
            self._run(lambda: self.api.post("/api/read", {"path": path}), self._got_read)
            return
        if d.get("why_not"):
            self.message("Can't place this file", d["why_not"], then=("back_items",))
            return
        plate = d.get("plate") or {"w": 200.0, "h": 200.0}
        geom = pm.fit(plate["w"], plate["h"], pm.PLACE_BOX)
        occ = pm.Grid.from_json(d.get("occ"))
        foot = pm.Grid.from_json(d.get("foot"))
        if not foot:
            self.message("Can't place this file", "The Pi found no cutting moves in it.", then=("back_items",))
            return
        fb = foot.bbox_mm()
        at = d.get("start_at") or [0.0, 0.0]
        self.place = {
            "d": d, "geom": geom, "foot": foot,
            "P": pm.Plate(plate["w"], plate["h"], occ, gap=d.get("gap", pm.GAP), probe_edge=d.get("probe_edge", 3.0)),
            "center": ((fb[0] + fb[2]) / 2.0, (fb[1] + fb[3]) / 2.0),
            "dc": int(round(at[0] / pm.RES)), "dr": int(round(at[1] / pm.RES)), "step": DEFAULT_STEP,
            "occ_runs": pm.screen_runs(pm.Grid.from_json(d.get("occ_all")) if d.get("occ_all") else occ, geom),
            "started_runs": pm.screen_runs(pm.Grid.from_json(d.get("started_area")), geom),
            "probe": d.get("probe"), "level": d.get("level"), "path_box": d.get("path_box"),
        }
        self._update_place(note="No clear place found. Move the part, or start a new plate." if d.get("no_room")
                           else "")

    def _limits(self):
        """The moves (in cells) that keep the bit's path on the plate."""
        p = self.place
        w, h = p["P"].w, p["P"].h
        b = p.get("path_box") or p["foot"].bbox_mm()
        r = pm.RES
        return (int(math.ceil(-b[0] / r - 1e-9)), int(math.floor((w - b[2]) / r + 1e-9)),
                int(math.ceil(-b[1] / r - 1e-9)), int(math.floor((h - b[3]) / r + 1e-9)))

    def _move_to(self, dc: int, dr: int) -> None:
        c0, c1, r0, r1 = self._limits()
        self.place["dc"] = max(c0, min(c1, int(dc))) if c0 <= c1 else int(dc)
        self.place["dr"] = max(r0, min(r1, int(dr))) if r0 <= r1 else int(dr)
        self._update_place()

    def _place_at(self, x: float, y: float) -> None:
        """Put the part's middle under the finger."""
        p = self.place
        px, py = pm.to_plate(p["geom"], x, y)
        cx, cy = p["center"]
        self._move_to(int(round((px - cx) / pm.RES)), int(round((py - cy) / pm.RES)))

    def _update_place(self, note: str = "") -> None:
        p = self.place
        d, geom = p["d"], p["geom"]
        res = p["P"].check(p["foot"], p["dc"], p["dr"], p["probe"], p["level"], p["path_box"])
        if d.get("unknown"):
            res = dict(res, ok=False, code="unknown", text=d["unknown"])
        dx, dy = p["dc"] * pm.RES, p["dr"] * pm.RES
        bad = []
        if res["code"] in ("overlap", "close"):
            bad = pm.screen_runs(p["foot"].moved(p["dc"], p["dr"]).both(p["P"].occ_gap), geom)
        probe = p.get("probe")
        level = p.get("level")
        off = (d.get("run") or {}).get("offset") or [0.0, 0.0]
        p["check"] = res
        self.ui = {
            "screen": "place", "title": d.get("name", ""), "geom": list(geom), "plate": d.get("plate"),
            "occ_runs": p["occ_runs"], "started_runs": p["started_runs"],
            "part_runs": pm.screen_runs(p["foot"], geom, p["dc"], p["dr"]), "bad_runs": bad,
            "probe_px": list(pm.to_screen(geom, probe[0] + dx, probe[1] + dy)) if probe else None,
            "level_px": [pm.to_screen(geom, level[0] + dx, level[1] + dy),
                         pm.to_screen(geom, level[2] + dx, level[3] + dy)] if level else None,
            "ok": res["ok"], "code": res["code"], "text": res["text"], "near": res["near"],
            "offset": [off[0] + dx, off[1] + dy], "step": STEPS[p["step"]][1], "note": note,
            "back": ("back_items",),
        }

    def _show_place_check(self) -> None:
        p = self.place
        if not p or not (p.get("check") or {}).get("ok"):
            return
        d = p["d"]
        pl = d.get("plate") or {}
        t = float(pl.get("t") or 0)
        lines = ["%s plate %s × %s × %s mm, pushed into the Anchor 1 corner." % (
            (pl.get("material") or "The").capitalize(), _n(pl.get("w")), _n(pl.get("h")), _n(t))]
        depth = d.get("depth")
        if depth and t:
            if depth > t:
                lines.append("Measure it: %.2f mm thick. The deepest cut is %.2f mm, so over %.2f mm the cut "
                             "won't go through." % (t, depth, depth - 0.10))
            else:
                lines.append("The deepest cut is %.2f mm, into the %.2f mm plate." % (depth, t))
        box = p["check"].get("box")
        if box:
            lines.append("The part's area, X%s to %s and Y%s to %s mm from the corner, must be bare." % (
                _n(max(0, box[0])), _n(min(float(pl.get("w", box[2])), box[2])),
                _n(max(0, box[1])), _n(min(float(pl.get("h", box[3])), box[3]))))
        names = [c["name"] for c in d.get("cuts") or [] if c.get("name")]
        if names:
            lines.append("Cut on this plate: " + ", ".join(names[:4]) + (" and %d more" % (len(names) - 4)
                                                                        if len(names) > 4 else "") + ".")
        self.ui = {"screen": "check", "lines": lines, "has_used": bool(d.get("cuts")),
                   "back": ("back_plate",), "expects": expects(d.get("file_plate"), pl)}

    # ---------------- NEW PLATE and LAST PLATE (from the plate check)
    def _check_data(self) -> dict:
        """The data behind the plate check now: the file being placed, or the layout's design."""
        if self.flow == "place" and self.place:
            return self.place["d"]
        return self._last_data or {}

    def _open_new_plate(self) -> None:
        d = self._check_data()
        pl = d.get("plate") or {}
        self.newplate = {"vals": {f: _v2(pl.get(f)) if pl.get(f) else "" for f, _ in NP_FIELDS},
                         "field": "w", "fresh": True, "material": pl.get("material") or "",
                         "cuts": len(d.get("cuts") or []), "previous": d.get("previous")}
        self._show_new_plate()

    def _np_key(self, k: str) -> None:
        """A key on the number pad: the first one after picking a box replaces its number."""
        np = self.newplate
        f = np["field"]
        s = "" if np["fresh"] else np["vals"][f]
        np["fresh"] = False
        if k == "del":
            s = s[:-1]
        elif k == ".":
            if "." not in s:
                s = (s or "0") + "."
        elif len(s.replace(".", "")) < 5 and not ("." in s and len(s.split(".")[1]) >= 2):
            s = ("" if s == "0" else s) + k
        np["vals"][f] = s

    def _show_new_plate(self, note: str = "") -> None:
        """note: what to fix before START NEW PLATE (shown in amber); else what LAST PLATE does."""
        np = self.newplate
        prev = np.get("previous")
        warn = bool(note)
        if not note:
            if prev:
                note = "LAST PLATE: back to the %s × %s × %s mm plate with %d cut area%s." % (
                    _n(prev.get("w")), _n(prev.get("h")), _v2(prev.get("t")), prev["cuts"],
                    "" if prev["cuts"] == 1 else "s")
            elif np["cuts"]:
                note = "Nothing is lost: this plate's cut areas are kept, and LAST PLATE here brings them back."
            else:
                note = "Type the plate's size. Measure its thickness."
        self.ui = {"screen": "newplate", "vals": dict(np["vals"]), "field": np["field"], "fresh": np["fresh"],
                   "labels": dict(NP_FIELDS), "last": bool(prev), "note": note, "warn": warn, "back": ("back_check",)}

    def _start_new_plate(self) -> None:
        np = self.newplate
        if not np:
            return
        size = {"material": np["material"]}
        for f, label in NP_FIELDS:
            lo, hi = NP_LIMITS[f]
            try:
                v = float(np["vals"][f])
            except ValueError:
                v = None
            if v is None or not lo <= v <= hi:
                np.update(field=f, fresh=True)
                self._show_new_plate("Type the %s: %s to %s mm." % (label.split()[0].lower(), _v2(lo), _v2(hi)))
                return
            size[f] = v
        again = ("file", self.path) if self.flow == "place" else ("design", self.design)
        self.ui = {"screen": "busy", "text": "New plate"}
        self._run(lambda: self.api.post("/api/plate", dict(size, action="new")), lambda res: self.do(again))

    def _show_place_review(self) -> None:
        p = self.place
        if not p:
            return
        d = p["d"]
        dx, dy = p["dc"] * pm.RES, p["dr"] * pm.RES
        fb = d.get("first_bit") or {}
        bits = "Probe first, then %s" % (fb.get("code") or "the first bit")
        if fb.get("sticker"):
            bits += " (%s sticker)" % fb["sticker"]
        if not d.get("probe_first"):
            bits = "First bit: %s" % (fb.get("code") or "-")
        lines = start_mod.run_lines(d.get("run"), (dx, dy)) + [bits, "Run time about %s" % _dur(d.get("time"))]
        if d.get("run_source") == "default":
            lines.insert(0, "This file has no run settings, so the Pi's are used.")
        self.ui = {"screen": "review", "title": d.get("name", ""), "lines": lines, "why": d.get("ready") or "",
                   "note": "", "back": ("back_check",), "preview": d.get("preview") or "",
                   "expects": expects(d.get("file_plate"), d.get("plate")), "bit_warnings": d.get("bit_warnings") or []}

    # ---------------- a design of a plate layout
    def _got_design(self, res) -> None:
        d = res or {}
        if not d.get("files"):
            self.message("Nothing to start", "The Pi has no file for this design.")
            return
        plate = d.get("plate") or {"w": 200.0, "h": 200.0}
        geom = pm.fit(plate["w"], plate["h"], pm.SPOT_BOX)
        d["geom"] = list(geom)
        d["occ_runs"] = pm.screen_runs(pm.Grid.from_json(d.get("occ")), geom)
        d["started_runs"] = pm.screen_runs(pm.Grid.from_json(d.get("started_area")), geom)
        sel = self.selected if self.selected and any(
            s["key"] == self.selected and s["mine"] and s["state"] == "free" and not s.get("why")
            for s in d["spots"]) else None
        self.selected = sel
        self.ui = {"screen": "plate", "data": d, "selected": sel, "note": d.get("unknown") or ""}

    def _data(self):
        return (self.ui or {}).get("data") or self._last_data

    _last_data = None

    def _file(self):
        d = self._data() or {}
        return (d.get("files") or {}).get(self.selected) if self.selected else None

    def _show_check(self) -> None:
        d = (self.ui or {}).get("data") or self._last_data or {}
        self._last_data = d
        f = (d.get("files") or {}).get(self.selected)
        if not f:
            return
        spot = next((s for s in d.get("spots", []) if s["key"] == self.selected), None)
        used = [s for s in d.get("spots", []) if s["state"] not in ("free",)]
        others = [c["name"] for c in d.get("cuts") or [] if c.get("layout") != d.get("folder") and c.get("name")]
        th = f.get("thickness")
        lines = []
        if f.get("plate_note"):
            lines.append(f["plate_note"])
        if th:
            lines.append("Measure it: %.2f mm thick. Over %.2f mm, the last cut won't go through." % (th["plate"], th["max_ok"]))
        if spot:
            lines.append("Spot %d (%s) must be bare." % (spot["n"], spot["where"]))
        if used or others:
            parts = ["spot %d%s" % (s["n"], (" (" + s["cut_design"] + ")") if s.get("cut_design") else "")
                     for s in used if s["state"] != "taken"] + others
            lines.append("Already cut on this plate: " + ", ".join(parts) + ".")
        self.ui = {"screen": "check", "data": d, "lines": lines, "has_used": bool(d.get("cuts")),
                   "back": ("back_plate",) if d.get("spots") else ("back_items",),
                   "expects": expects(f.get("file_plate"), d.get("plate"))}

    def _show_review(self) -> None:
        d = self._last_data or {}
        f = (d.get("files") or {}).get(self.selected) or {}
        spot = next((s for s in d.get("spots", []) if s["key"] == self.selected), None)
        title = d.get("name", "")
        if spot:
            title += " · spot %d, %s" % (spot["n"], spot["where"])
        fb = f.get("first_bit") or {}
        bits = "Probe first, then %s" % (fb.get("code") or "the first bit")
        if fb.get("sticker"):
            bits += " (%s sticker)" % fb["sticker"]
        if not f.get("probe_first"):
            bits = "First bit: %s" % (fb.get("code") or "-")
        lines = list(f.get("run") or []) + [bits, "Run time about %s" % _dur(f.get("time"))]
        why = f.get("why_not") or d.get("ready") or ""
        self.ui = {"screen": "review", "title": title, "lines": lines, "why": why, "note": "",
                   "back": ("back_check",), "preview": f.get("preview") or "",
                   "expects": expects(f.get("file_plate"), d.get("plate")), "bit_warnings": f.get("bit_warnings") or []}

    def _started(self, res) -> None:
        if res and res.get("ok"):
            self.ui = {"screen": "starting", "text": "Started. The Z1 is getting ready."}
        else:
            self.message("Not started", (res or {}).get("error") or "The Z1 didn't start.")


def _material(m) -> str:
    return " ".join(str(m or "").lower().replace("-", " ").split())


def expects(fp, pl) -> dict:
    """What a job file says it was made for (material, size, thickness), against the plate on record, for the
    warning before a start: {"text", "detail", "differs"}."""
    pl = pl or {}
    if not fp or not fp.get("w"):
        return {"text": "This file doesn't say what plate it was made for. Check the plate yourself.",
                "detail": "", "differs": False}
    mat = str(fp.get("material") or "").strip()
    size = "%s × %s × %s mm" % (_n(fp["w"]), _n(fp["h"]), _n(fp.get("t") or 0))
    text = "This job expects: %s, %s." % (mat.capitalize() if mat else "a plate", size)
    pw, ph, pt = float(pl.get("w") or 0), float(pl.get("h") or 0), float(pl.get("t") or 0)
    fw, fh, ft = float(fp["w"]), float(fp["h"]), float(fp.get("t") or 0)
    same_size = ((abs(fw - pw) <= 0.5 and abs(fh - ph) <= 0.5) or (abs(fw - ph) <= 0.5 and abs(fh - pw) <= 0.5))
    a, b = _material(mat), _material(pl.get("material"))
    same_material = not a or not b or a in b or b in a
    differs = not same_size or (ft > 0 and abs(ft - pt) > 0.05) or not same_material
    detail = ""
    if differs:
        detail = "The plate on record: %s, %s × %s × %s mm." % (
            str(pl.get("material") or "no material set").capitalize(), _n(pw), _n(ph), _n(pt))
    return {"text": text, "detail": detail, "differs": differs}


def _dur(s) -> str:
    try:
        s = int(round(float(s)))
    except (TypeError, ValueError):
        return "-"
    h, m = divmod(s // 60, 60)
    return "%d min" % m if h == 0 else "%d h %02d min" % (h, m)


def _n(v) -> str:
    try:
        return start_mod.fmt(round(float(v), 1))
    except (TypeError, ValueError):
        return "-"


def _v2(v) -> str:
    """A size the way NEW PLATE's boxes show it: up to 2 decimals (a 6.35 mm plate)."""
    try:
        return start_mod.fmt(round(float(v), 2))
    except (TypeError, ValueError):
        return "-"
