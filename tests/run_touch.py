"""The touch screens, end to end: the real drawing loop (tft.run) with a pretend
touch layer and a pretend PiTFT, the screen service and the pretend Z1.

Taps go in as raw readings, the way the STMPE610 sends them. The test sets up
the touch screen with the four dots, opens JOBS, picks a file the Pi hasn't
read yet (the Pi reads it from the Z1), places it on the plate (the Pi's
suggestion, a drag onto a cut area, which turns NEXT off, and the arrows),
checks the plate, holds START, and checks the Z1 got the three start commands
with the move added. Then it opens the sample sheet layout's spot map. Last,
the card moves to a Pi with the 3.5inch DPI screen (640 x 480, a Goodix touch
layer, hung upside down in the housing): the PiTFT's calibration isn't used
there, the built-in one is, so JOBS works without the dots; then a setup from
the settings page is saved for that screen and used instead. It saves a
picture of every PiTFT screen in tests/out/touch-shots (or the folder given as
the first argument).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import queue
import random
import sys
import tempfile
import threading
import time

from common import ANCHOR, DISC, HERE, SHEET, SIGN, TAG, check, cfg as test_cfg, make_library, sample_files  # noqa: E402
from sim_machine import SimMachine  # noqa: E402
from z1screen import config, jobs_api, platemap as pm, start, tft, touch  # noqa: E402
from z1screen.hub import Hub  # noqa: E402
from z1screen.jobs import JobStore  # noqa: E402
from z1screen.web import Web  # noqa: E402
from run_settings import wait_for  # noqa: E402
from run_tft import picture  # noqa: E402
from run_start import BADGE, badge_file  # noqa: E402

WEB = 46080
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out", "touch-shots")


class FakeTouch:
    """Raw readings like the PiTFT's: about 13 counts per pixel, X and Y swapped, Y turned round."""

    def __init__(self):
        self.q = queue.Queue()
        self.rng = random.Random(7)

    def ok(self):
        return True

    def ensure(self, now):
        pass

    def raw(self, x, y):
        return (int(3900 - y * 14.5 + self.rng.uniform(-6, 6)), int(250 + x * 11.2 + self.rng.uniform(-6, 6)))

    def press(self, x, y, hold=0.08):
        self.drag([(x, y)], hold)

    def drag(self, points, hold=0.08):
        """Touch the first point, slide through the others, let go at the last."""
        rx, ry = self.raw(*points[0])
        self.q.put(("down", rx, ry, time.time()))
        steps = max(1, int(hold / 0.05))
        for x, y in points[1:] or points:
            for i in range(steps):
                time.sleep(hold / steps)
                rx, ry = self.raw(x, y)
                self.q.put(("move", rx, ry, time.time()))
        self.q.put(("up", rx, ry, time.time()))

    def touches(self, wait=0.0):
        out = []
        try:
            out.append(self.q.get(timeout=wait) if wait > 0 else self.q.get_nowait())
            while True:
                out.append(self.q.get_nowait())
        except queue.Empty:
            pass
        return out


class FakeGoodix(FakeTouch):
    """Raw readings like a real 3.5inch DPI screen's Goodix touch layer hung upside down in the housing (about 0 to
    483 across, 0 to 610 down): the calibration made on one, worked backwards. The built-in one should match it."""

    AX, AY = (-0.66246, -0.00757, 322.105), (-0.03491, -0.39334, 247.210)

    def raw(self, x, y):
        (a, b, c), (d, e, f) = self.AX, self.AY
        u, v, det = x - c, y - f, a * e - b * d
        return (int(round((u * e - b * v) / det + self.rng.uniform(-0.5, 0.5))),
                int(round((a * v - d * u) / det + self.rng.uniform(-0.5, 0.5))))


async def main():
    os.makedirs(OUT, exist_ok=True)
    root = tempfile.mkdtemp(prefix="z1touch-")
    sysfs, devdir = os.path.join(root, "sys"), os.path.join(root, "dev")
    os.makedirs(os.path.join(sysfs, "fb1"))
    os.makedirs(devdir)
    for k, val in zip(("name", "virtual_size", "bits_per_pixel", "stride"), ("fb_ili9340", "320,240", "16", "640")):
        with open(os.path.join(sysfs, "fb1", k), "w") as fh:
            fh.write(val + "\n")
    open(os.path.join(devdir, "fb1"), "wb").close()
    tft.SYSFS, tft.DEVDIR, tft.TTY_ACTIVE = sysfs, devdir, os.path.join(root, "no-console")
    dev = os.path.join(devdir, "fb1")

    cfg = test_cfg(plate=None)
    cfg.update({"discovery_port": 46333, "relay_port": 46224, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 46334, "forward_ports": [], "poll_interval": 0.5, "announce_interval": 0.5,
                "read_machine_config": False})
    hub = Hub(cfg, JobStore(tempfile.mkdtemp(), [make_library([SIGN, TAG] + DISC, seed=True)]))
    sim = SimMachine(port=46222, announce_to=("127.0.0.1", 46333), speed=300, compress_downloads=True)
    files = sample_files()
    for path in (SIGN, TAG, DISC[0], DISC[2]):
        sim.files[path] = files[path]
    sim.files[BADGE] = badge_file()
    await sim.start()
    task = asyncio.create_task(hub.run(Web(hub, WEB)))
    await wait_for(lambda: hub.own is not None and hub.status is not None, 10, "the Pi's own connection")

    fake = FakeTouch()
    stop = {"now": False}
    seen = {}
    thread = threading.Thread(target=tft.run, args=(cfg, "", "", 0.25, stop),
                              kwargs={"touch_input": fake, "expose": seen}, daemon=True)
    thread.start()
    await wait_for(lambda: "panel" in seen and os.path.getsize(dev) == 320 * 240 * 2, 10, "the first picture")
    panel = seen["panel"]
    n = [0]
    loop = asyncio.get_running_loop()

    def shot(name):
        n[0] += 1
        picture(dev).save(os.path.join(OUT, "%02d-%s.png" % (n[0], name)))

    async def screen(name, timeout=10):
        await wait_for(lambda: (panel.ui or {}).get("screen") == name, timeout, "the %s screen" % name)
        await asyncio.sleep(0.5)

    def target(action):
        for x, y, w, h, a in panel.hits:
            if a == action:
                return x + w / 2.0, y + h / 2.0
        raise AssertionError("no %r on the screen: %s" % (action, [h[4] for h in panel.hits]))

    async def tap(action, hold=0.08):
        x, y = target(action)
        await loop.run_in_executor(None, fake.press, x, y, hold)
        await asyncio.sleep(0.4)

    print("1. first touch: set up the touch screen with four dots")
    await wait_for(lambda: hub.view.get("mode") == "idle", 10, "Ready")
    await asyncio.sleep(0.8)
    shot("ready-set-up-touch")
    await loop.run_in_executor(None, fake.press, 160, 120, 0.08)
    await screen("calibrate")
    for (x, y) in touch.CAL_POINTS:
        await loop.run_in_executor(None, fake.press, x, y, 0.08)
        await asyncio.sleep(0.3)
    await screen("message")
    check(hub.touch is not None and panel.cal is not None, "calibration saved by the screen service")
    for (x, y) in touch.CAL_POINTS:
        px, py = touch.apply(panel.cal, *fake.raw(x, y), False)
        check(abs(px - x) < 3 and abs(py - y) < 3, "dot at %d,%d lands at %.0f,%.0f" % (x, y, px, py))
    await tap(("ok",))
    await wait_for(lambda: panel.ui is None, 5, "back to Ready")
    await asyncio.sleep(0.6)
    shot("ready-jobs")

    print("2. JOBS: folders, then a folder's files")
    await tap(("jobs",))
    await screen("list", 20)
    shot("folders")
    names = [it["name"] for it in panel.ui["items"]]
    check(names == ["parts", "sample-sheet", "samples"], "folders on the Z1: %s" % names)
    await tap(("folder", "/sd/gcodes/parts"))
    await asyncio.sleep(0.5)
    shot("files")
    check([it["name"] for it in panel.ui["items"]] == ["badge 30x20"] and "tap to read" in panel.ui["items"][0]["sub"],
          "the badge, not read yet")

    print("3. the Pi reads the file from the Z1, then shows the plate")
    await tap(("file", BADGE))
    await wait_for(lambda: (panel.ui or {}).get("screen") in ("reading", "place"), 10, "reading")
    if panel.ui.get("screen") == "reading":
        shot("reading")
    await screen("place", 30)
    shot("place-suggested")
    ui = panel.ui
    check(ui["ok"] and ui["code"] == "ok" and ui["offset"] != [0.0, 0.0],
          "the Pi suggests a clear place, X%s Y%s from Anchor 1" % tuple(ui["offset"]))
    check(len(ui["occ_runs"]) >= 2 and ui["part_runs"], "the cut area and the tag drawn as cut areas (%d pieces), the part in green"
          % len(ui["occ_runs"]))
    suggested = list(ui["offset"])

    print("4. drag the part onto the tag's area: it turns amber, NEXT turns off")
    geom = ui["geom"]
    pts = [pm.to_screen(geom, 120 + i * 3, 30 + i * 2) for i in range(0, 6)]
    await loop.run_in_executor(None, fake.drag, [target(("plate_area",))] + pts + [pm.to_screen(geom, 140, 40)], 0.1)
    await asyncio.sleep(0.6)
    ui = panel.ui
    check(not ui["ok"] and ui["code"] == "overlap" and ui["bad_runs"], "overlap shown in red: %s" % ui["text"])
    check(not any(a == ("to_check",) for *_, a in panel.hits), "NEXT can't be pressed")
    shot("place-overlap")

    print("5. the arrows: 5 mm steps to the left until it is clear")
    check(panel.ui["step"] == "1 mm", "arrows start at 1 mm steps")
    await tap(("step",))
    check(panel.ui["step"] == "5 mm", "one tap on the middle button: 5 mm steps")
    for _ in range(30):
        if panel.ui["ok"]:
            break
        await tap(("nudge", -1, 0))
    check(panel.ui["ok"], "clear again at X%s Y%s (%s)" % (panel.ui["offset"][0], panel.ui["offset"][1],
                                                          "nearest cut %s mm" % panel.ui["near"]))
    shot("place-arrows")
    off = list(panel.ui["offset"])

    print("6. plate check, then hold START")
    await tap(("to_check",))
    await screen("check")
    shot("check")
    text = " ".join(panel.ui["lines"])
    check("Plywood plate 200 × 150 × 6 mm" in text and "Sample cut" in text and "Sample tag" in text,
          "plate size and what is cut on it are listed")
    await tap(("checked",))
    await screen("review")
    shot("review")
    check(any("offset X%s Y%s" % (start.fmt(off[0]), start.fmt(off[1])) in l for l in panel.ui["lines"]),
          "the review shows the move as Makera Studio's offsets")
    x, y = target(("hold_start",))
    await loop.run_in_executor(None, fake.press, x, y, 2.4)
    await wait_for(lambda: any(c.startswith("play") for c in sim.log), 10, "the play command")
    sent = [c for c in sim.log if c.startswith(("G10", "buffer", "play"))]
    prof = hub.store.exact(BADGE)
    run, _ = start.run_for(prof)
    check(sent == start.commands(prof, ANCHOR, run, tuple(off)), "the Z1 got: %s" % sent)
    await wait_for(lambda: panel.ui is None, 15, "the job screen")
    await asyncio.sleep(0.6)
    shot("job-probe-stop")
    await loop.run_in_executor(None, fake.press, 160, 120, 0.1)
    await asyncio.sleep(0.6)
    check(panel.ui is None, "touching the screen during a job does nothing")
    while sim.playing:
        if sim.waiting:
            sim.confirm()
        await asyncio.sleep(0.05)
    await wait_for(lambda: hub.job is None and hub.view.get("mode") == "idle", 20, "the end of the job")
    check(any(c["name"] == "badge 30x20" and c["state"] == "cut" and c["shift"] == off for c in hub.plates.cuts),
          "its area is on the plate record where it was placed")

    print("7. the sample sheet's spot map shows the cut areas under the spots")
    await asyncio.sleep(1.0)
    await tap(("jobs",))
    await screen("list", 20)
    await tap(("folder", SHEET))
    await asyncio.sleep(0.4)
    await tap(("design", "disc"))
    await screen("plate")
    shot("spots-used-plate")
    st = {s["n"]: s["state"] for s in panel.ui["data"]["spots"]}
    badge = next(c for c in hub.plates.cuts if c["name"] == "badge 30x20")
    check(st == {1: "free", 2: "taken", 3: "free", 4: "taken"},
          "the badge (placed at X%s Y%s from its file's place) covers spots 2 and 4; 1 and 3 are free: %s"
          % (badge["shift"][0], badge["shift"][1], st))

    print("7b. NEW PLATE with its own size on the number pad, then LAST PLATE brings the old plate back")
    cuts_before = [c["id"] for c in hub.plates.cuts]
    one = next(s["key"] for s in panel.ui["data"]["spots"] if s["n"] == 1)
    await tap(("spot", one))
    await tap(("to_check",))
    await screen("check")
    await tap(("new_plate",))
    await screen("newplate")
    check(panel.ui["vals"] == {"w": "200", "h": "150", "t": "6"} and panel.ui["field"] == "w" and not panel.ui["last"],
          "the boxes start with the plate on record, 200 x 150 x 6 mm: %s" % panel.ui["vals"])
    for k in ("2", "5", "0"):
        await tap(("np_key", k))
    await tap(("np_field", "t"))
    for k in ("6", ".", "3", "5"):
        await tap(("np_key", k))
    check(panel.ui["vals"] == {"w": "250", "h": "150", "t": "6.35"}, "typed 250 and 6.35: %s" % panel.ui["vals"])
    shot("new-plate")
    await tap(("np_field", "h"))
    await tap(("np_key", "del"))
    await tap(("np_start",))
    check(panel.ui["screen"] == "newplate" and panel.ui["warn"] and panel.ui["field"] == "h",
          "an empty box isn't taken: %s" % panel.ui["note"])
    for k in ("1", "5", "0"):
        await tap(("np_key", k))
    await tap(("np_start",))
    await screen("plate")
    pl = hub.plates.plate
    check(hub.plates.cuts == [] and (pl["w"], pl["h"], pl["t"]) == (250, 150, 6.35)
          and hub.plates.previous_info()["cuts"] == len(cuts_before),
          "a bare 250 x 150 x 6.35 mm plate; the old one is kept with its %d cut areas" % len(cuts_before))
    await tap(("spot", one))
    await tap(("to_check",))
    await screen("check")
    await tap(("new_plate",))
    await screen("newplate")
    check(panel.ui["last"] and "200 × 150 × 6 mm plate with %d cut areas" % len(cuts_before) in panel.ui["note"],
          "LAST PLATE is offered: %s" % panel.ui["note"])
    shot("new-plate-last")
    await tap(("last_plate",))
    await screen("plate")
    check([c["id"] for c in hub.plates.cuts] == cuts_before and hub.plates.plate["w"] == 200,
          "LAST PLATE: the 200 x 150 plate and its cut areas are back")
    st = {s["n"]: s["state"] for s in panel.ui["data"]["spots"]}
    check(st == {1: "free", 2: "taken", 3: "free", 4: "taken"}, "the spot map shows them again: %s" % st)

    print("8. with starting turned off, JOBS says so")
    await tap(("home",)) if any(a == ("home",) for *_, a in panel.hits) else None
    hub.cfg["start_from_screen"] = False
    panel.close()
    await asyncio.sleep(0.8)
    await tap(("jobs",))
    await screen("message", 20)
    shot("starting-off")
    check(panel.ui["title"] == "Starting is off" and "settings" in panel.ui["text"], "message: %s" % panel.ui["text"])

    print("9. the card moves to a Pi with the 3.5inch DPI screen, hung upside down: no setup needed")
    stop["now"] = True
    thread.join(5)
    with open(os.path.join(hub.state_dir, "touch.json"), "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in hub.touch.items() if k != "screen"}, fh)     # as older versions saved it
    hub.touch = hub._load_touch()                                                 # read as at the Pi's start
    hub.set_screen({"flip": True})                                                # as install.sh --flip sets it
    for k, val in zip(("name", "virtual_size", "stride"), ("vc4drmfb", "640,480", "1280")):
        with open(os.path.join(sysfs, "fb1", k), "w") as fh:
            fh.write(val + "\n")
    open(dev, "wb").close()
    fake, stop, seen = FakeGoodix(), {"now": False}, {}
    thread = threading.Thread(target=tft.run, args=(cfg, "", "", 0.25, stop),
                              kwargs={"touch_input": fake, "expose": seen}, daemon=True)
    thread.start()
    await wait_for(lambda: "panel" in seen and os.path.getsize(dev) == 640 * 480 * 2, 10,
                   "the first 640 x 480 picture")
    panel = seen["panel"]
    await wait_for(lambda: panel.ready(), 10, "a calibration for the 640 x 480 screen")
    check(panel.cal == jobs_api.BUILT_IN_TOUCH[(640, 480)] and hub.debug_info()["touch_calibration"] == "built-in"
          and hub.setup_info()["touch_calibrated"], "the PiTFT's calibration isn't used there, the built-in one is, "
          "and the settings page says the touch screen is set up")
    await asyncio.sleep(0.6)
    await tap(("jobs",))
    await screen("message", 20)
    check(panel.ui["title"] == "Starting is off", "a tap on JOBS lands on it, with no setup")
    await tap(("ok",))
    await wait_for(lambda: panel.ui is None, 5, "back to Ready")
    hub.set_touch({"action": "calibrate"})                    # the settings page's "Set up the touch screen"
    await screen("calibrate")
    for (x, y) in touch.CAL_POINTS:
        await loop.run_in_executor(None, fake.press, x, y, 0.08)
        await asyncio.sleep(0.3)
    await screen("message")
    check(hub.touch.get("screen") == [640, 480] and hub.debug_info()["touch_calibration"] == "saved",
          "Set up the touch screen, on the settings page: saved for the 640 x 480 screen and used instead")
    for (x, y) in touch.CAL_POINTS:
        px, py = touch.apply(panel.cal, *fake.raw(x, y), True)
        check(abs(px - x) < 3 and abs(py - y) < 3, "dot at %d,%d lands at %.0f,%.0f" % (x, y, px, py))
    stop["now"] = True
    task.cancel()
    print("touch checks passed; pictures in", OUT)

asyncio.run(main())
