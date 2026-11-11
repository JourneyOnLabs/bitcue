"""The small screen program against the pretend Z1, drawing into a pretend PiTFT.

The real drawing loop (tft.run) runs in a thread with a pretend Pi: a framebuffer
listed like the PiTFT (fb1, 320 x 240, 16 bit) whose device is a plain file.
Checks: the splash before the screen service answers, turned when screen.json
says upside down; Ready on the screen; the probe stop turns it white; the settings
page's upside-down switch turns the picture; the T1 stop turns it red; the preview
picture; it says so when the screen service stops answering; and nothing but
status and progress requests reach the Z1.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import struct
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from PIL import Image, ImageChops  # noqa: E402
from sim_machine import SimMachine  # noqa: E402
from z1screen import config, tft  # noqa: E402
from z1screen.hub import Hub  # noqa: E402
from z1screen.jobs import JobStore  # noqa: E402
from z1screen.web import Web  # noqa: E402
from run_settings import http, wait_for  # noqa: E402
from common import SIGN, check, make_library, sample_files  # noqa: E402

WEB = 38080


def rgb565(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def share(dev, color):
    """How much of the pretend screen is this color."""
    try:
        with open(dev, "rb") as fh:
            data = fh.read()
    except OSError:
        return 0.0
    if len(data) < 320 * 240 * 2:
        return 0.0
    want = struct.pack("<H", rgb565(color))
    px = [data[i:i + 2] for i in range(0, len(data), 2)]
    return sum(1 for p in px if p == want) / len(px)


def picture(dev):
    with open(dev, "rb") as fh:
        data = fh.read()
    img = Image.new("RGB", (320, 240))
    vals = struct.unpack("<%dH" % (320 * 240), data[:320 * 240 * 2])
    img.putdata([(((v >> 11) & 31) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3) for v in vals])
    return img


def same(a, b):
    """Share of pixels that match (to RGB565 precision)."""
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    worst = ImageChops.lighter(ImageChops.lighter(*diff.split()[:2]), diff.split()[2])
    return sum(worst.histogram()[:9]) / float(a.width * a.height)


async def main():
    root = tempfile.mkdtemp(prefix="z1tft-run-")
    sysfs, devdir = os.path.join(root, "sys"), os.path.join(root, "dev")
    os.makedirs(os.path.join(sysfs, "fb0"))
    os.makedirs(os.path.join(sysfs, "fb1"))
    os.makedirs(devdir)
    for n, vals in ((0, ("vc4drmfb", "1920,1080", "32", "7680")), (1, ("fb_ili9340", "320,240", "16", "640"))):
        for k, val in zip(("name", "virtual_size", "bits_per_pixel", "stride"), vals):
            with open(os.path.join(sysfs, "fb%d" % n, k), "w") as fh:
                fh.write(val + "\n")
        open(os.path.join(devdir, "fb%d" % n), "wb").close()
    tft.SYSFS, tft.DEVDIR, tft.TTY_ACTIVE = sysfs, devdir, os.path.join(root, "no-console")
    dev = os.path.join(devdir, "fb1")

    state_dir = tempfile.mkdtemp(prefix="z1s-")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 33333, "relay_port": 34222, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 34333, "forward_ports": [], "poll_interval": 0.5, "announce_interval": 0.5})

    print("0. the splash, before the screen service answers (nothing is listening yet)")
    fonts = tft.Fonts()
    splash_up, splash_down = (tft.render({"mode": "_starting"}, fonts, flip=f) for f in (False, True))
    check(same(splash_up, splash_down) < 0.9, "turned round, the splash looks different")
    cfg_down = copy.deepcopy(cfg)
    cfg_down["tft"]["flip"] = True

    def shows(want):
        return os.path.getsize(dev) == 320 * 240 * 2 and same(picture(dev), want) > 0.97

    for why, run_cfg, saved, want in (
            ("upside down when screen.json says so (install.sh --flip, or the settings page's switch)", cfg,
             {"flip": True}, splash_down),
            ("the right way up with no choice saved", cfg, None, splash_up),
            ("the right way up when screen.json says so, over a settings file saying upside down", cfg_down,
             {"flip": False}, splash_up)):
        early_dir = tempfile.mkdtemp(prefix="z1s-")
        if saved is not None:
            with open(os.path.join(early_dir, "screen.json"), "w", encoding="utf-8") as fh:
                json.dump(saved, fh)
        open(dev, "wb").close()
        early = {"now": False}
        early_thread = threading.Thread(target=tft.run, args=(run_cfg, "", "", 0.25, early),
                                        kwargs={"state_dir": early_dir}, daemon=True)
        early_thread.start()
        await wait_for(lambda: shows(want), 10, "the splash " + why)
        check(True, "the splash is drawn " + why)
        early["now"] = True
        early_thread.join(5)
    open(dev, "wb").close()

    store = JobStore(state_dir, [make_library((SIGN,))])
    hub = Hub(cfg, store)
    web = Web(hub, WEB)
    sim = SimMachine(port=33222, announce_to=("127.0.0.1", 33333), speed=60)
    sim.files[SIGN] = sample_files()[SIGN]
    await sim.start()
    task = asyncio.create_task(hub.run(web))

    stop = {"now": False}
    thread = threading.Thread(target=tft.run, args=(cfg, "", "", 0.25, stop), daemon=True)
    thread.start()

    print("1. the screen before and after the Z1 is found")
    await wait_for(lambda: os.path.getsize(dev) == 320 * 240 * 2, 10, "the first picture on the pretend PiTFT")
    check(os.path.getsize(os.path.join(devdir, "fb0")) == 0, "the monitor's framebuffer is left alone")
    await wait_for(lambda: hub.status is not None, 10, "the Pi talking to the Z1")
    await wait_for(lambda: (hub.view.get("mode") == "idle") or None, 10, "Ready")
    await asyncio.sleep(1.0)
    ready = tft.render(hub.view, tft.Fonts())
    check(same(picture(dev), ready) > 0.97, "the pretend PiTFT shows Ready, as drawn from /state")
    check(share(dev, tft.GROUND) > 0.6, "dark background")

    print("2. a job starts: probe stop")
    sim.start_play(SIGN)
    await wait_for(lambda: share(dev, "#F5F5F5") > 0.5 or None, 8, "the white probe screen")
    check(True, "the screen turns the probe's white (%.0f%% of it)" % (100 * share(dev, "#F5F5F5")))

    print("3. upside down from the settings page")
    code, res = await http("POST", "/api/screen", {"flip": "yes"})
    check(code == 400, "a value that is not true or false is refused")
    code, res = await http("POST", "/api/screen", {"flip": True})
    check(code == 200 and res["flip"] is True, "switched to upside down")
    fonts = tft.Fonts()

    def turned():
        v = hub.view
        up = tft.render(v, fonts, flip=False)
        down = tft.render(v, fonts, flip=True)
        pic = picture(dev)
        return same(pic, down) > 0.95 and same(pic, up) < 0.9

    await wait_for(turned, 5, "the turned picture")
    check(True, "the picture on the PiTFT is turned round")
    check(json.load(open(os.path.join(state_dir, "screen.json")))["flip"] is True, "kept in screen.json")
    check(Hub(copy.deepcopy(cfg), store).screen_info()["flip"] is True, "and still upside down after a restart")
    code, png = await http("GET", "/screen.png")
    img = Image.open(__import__("io").BytesIO(png))
    check(code == 200 and img.size == (320, 240), "settings page preview: a 320 x 240 picture")

    async def upright():
        # the hub can build new screen data between the download and this drawing, so try again until they match
        code, png = await http("GET", "/screen.png")
        pic = Image.open(__import__("io").BytesIO(png)).convert("RGB")
        return code == 200 and same(pic, tft.render(hub.view, fonts, flip=False)) > 0.95

    await wait_for(upright, 5, "the preview the right way up")
    check(True, "the preview stays the right way up")
    await http("POST", "/api/screen", {"flip": False})

    print("4. the next stop")
    sim.confirm()
    await wait_for(lambda: share(dev, "#D32F2F") > 0.5 or None, 15, "the red T1 screen")
    check(True, "T1's stop turns the screen red (%.0f%%)" % (100 * share(dev, "#D32F2F")))
    sim.confirm()
    await wait_for(lambda: (hub.view.get("mode") == "running") or None, 10, "cutting")
    await asyncio.sleep(1.0)
    check(share(dev, "#D32F2F") > 0.3, "while cutting, the big card is T1's red, the bit cutting now (%.0f%%)"
          % (100 * share(dev, "#D32F2F")))
    check(share(dev, "#FFD600") > 0.08, "and the next bit's strip is T2's yellow (%.0f%%)" % (100 * share(dev, "#FFD600")))

    print("5. the screen service stops answering")
    task.cancel()
    await asyncio.sleep(0.5)
    lost = tft.render({"mode": "_lost", "settings_url": hub.view.get("settings_url", "")}, fonts)
    await wait_for(lambda: same(picture(dev), lost) > 0.97 or None, 12,
                   "the no-data message")
    check(True, "the screen says the screen service isn't answering")
    stop["now"] = True
    thread.join(5)
    check(not thread.is_alive() and same(picture(dev), tft.render({"mode": "_stopped"}, fonts)) > 0.97,
          "when the screen program stops it leaves a 'Screen stopped' picture, not a stale job")

    print("6. what reached the Z1")
    others = [c for c in sim.log if c != "progress"]
    check(not others, "only status and progress requests (%s)" % (others[:3] or "none other"))
    print("small screen checks against the pretend Z1 passed")


if __name__ == "__main__":
    asyncio.run(main())
