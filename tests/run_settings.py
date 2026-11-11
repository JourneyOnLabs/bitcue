"""Settings page, bit colors and the demo on the monitor, against the pretend Z1.

Checks: the page and its data load; only JSON is accepted, only from the Pi's
own page and only by a local name; a bad color is refused with a clear message;
a new bit's color is used at once; the setup section (plate size first, then
starting from the screen with the warning accepted, Anchor 1 typed or read,
run settings, layout folders, the PIN), kept after a restart; the color check
and the demo take over the screen only while the Z1 is idle; the demo waits at
each stop for its front button; it stops by itself the moment the Z1 starts a
real job; and nothing but status and progress requests reach the Z1.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time

from common import DISC, SIGN, TAG, check, make_library, sample_files  # noqa: E402
from sim_machine import SimMachine  # noqa: E402
from z1screen import __version__, config  # noqa: E402
from z1screen.hub import Hub  # noqa: E402
from z1screen.jobs import JobStore  # noqa: E402
from z1screen.web import Web  # noqa: E402

WEB = 38080


async def http(method, path, body=None, ctype="application/json", host="x", headers=None, local_addr=None):
    r, w = await asyncio.open_connection("127.0.0.1", WEB, local_addr=local_addr)
    data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    head = "%s %s HTTP/1.1\r\nHost: %s\r\n" % (method, path, host)
    for k, v in (headers or {}).items():
        head += "%s: %s\r\n" % (k, v)
    if method == "POST":
        head += "Content-Type: %s\r\nContent-Length: %d\r\n" % (ctype, len(data))
    w.write((head + "\r\n").encode() + data)
    await w.drain()
    raw = await r.read()
    w.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    code = int(head.split(b" ")[1])
    if b"application/json" in head:
        return code, json.loads(payload)
    return code, payload


async def wait_for(cond, timeout, what):
    t = time.time()
    while time.time() - t < timeout:
        v = cond()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        await asyncio.sleep(0.1)
    raise AssertionError("timed out waiting for " + what)


async def state():
    return (await http("GET", "/state"))[1]


async def main():
    state_dir = tempfile.mkdtemp(prefix="z1s-")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 33333, "relay_port": 34222, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 34333, "forward_ports": [], "poll_interval": 0.5, "announce_interval": 0.5})
    store = JobStore(state_dir, [make_library([SIGN, TAG] + DISC)])
    hub = Hub(copy.deepcopy(cfg), store)
    web = Web(hub, WEB)
    sim = SimMachine(port=33222, announce_to=("127.0.0.1", 33333), speed=300)
    sim.files[SIGN] = sample_files()[SIGN]
    await sim.start()
    task = asyncio.create_task(hub.run(web))
    await wait_for(lambda: hub.status is not None, 10, "the Pi talking to the Z1")
    await asyncio.sleep(1.2)

    print("1. settings page and its data")
    code, page = await http("GET", "/settings")
    check(code == 200 and b"Bits and sticker colors" in page, "settings page served")
    code, info = await http("GET", "/api/bits")
    check(code == 200 and info["tools"]["3"]["sticker"] == "Blue" and info["seen"]["3"]["files"] == 6
          and len(info["palette"]) == 12, "bit table, bits seen in job files and the sticker colors")
    code, body = await http("GET", "/health")
    check(code == 200 and body == ("ok " + __version__).encode(), "health answer with the version: %s" % body)

    print("2. saving colors")
    code, res = await http("POST", "/api/bits", b'{"tools":{}}', ctype="text/plain")
    check(code == 415, "only JSON is accepted (so another website can't post a form here)")
    code, res = await http("POST", "/api/bits", b'{"tools":{}}', ctype="text/plain; x=application/json")
    check(code == 415, "a form type that only mentions JSON is refused too")
    code, res = await http("POST", "/api/bits", {"tools": {}}, headers={"Origin": "http://evil.example.com"})
    check(code == 403, "a change sent from another website is refused")
    code, res = await http("POST", "/api/bits", {"tools": {}}, host="z1screen.local:38080",
                           headers={"Origin": "http://evil.example.com"})
    check(code == 403, "even when it names the Pi")
    code, res = await http("GET", "/api/bits", host="evil.example.com")
    check(code == 403, "a page asked for by another site's name (DNS rebinding) is refused")
    code, res = await http("GET", "/api/bits", host="z1screen.local:38080")
    check(code == 200, "z1screen.local is fine")
    bad = copy.deepcopy(info["tools"])
    bad["3"]["color"] = "blue"
    code, res = await http("POST", "/api/bits", {"tools": bad})
    check(code == 400 and "T3" in res["error"], "a bad color is refused: %s" % res["error"])
    good = copy.deepcopy(info["tools"])
    good["12"] = {"name": "6 mm flat end mill", "color": "#7B1FA2", "sticker": "Purple"}
    code, res = await http("POST", "/api/bits", {"tools": good})
    check(code == 200 and res["saved"] and hub.tool_info(12)["color"] == "#7B1FA2", "T12 saved in purple")

    print("3. the setup section")
    code, su = await http("GET", "/api/setup")
    check(code == 200 and not su["start_from_screen"] and not su["plate_set"] and su["anchor"]["mode"] == "auto"
          and su["version"] == __version__, "a fresh Pi: starting off, plate size not set, Anchor 1 read from the Z1")
    code, res = await http("POST", "/api/setup", {"start_from_screen": True})
    check(code == 400 and "box" in res["error"], "starting can't be turned on without accepting the warning")
    code, res = await http("POST", "/api/setup", {"start_from_screen": True, "accept": True})
    check(code == 400 and "plate size" in res["error"], "nor before the plate size is set: %s" % res["error"])
    code, res = await http("POST", "/api/plate", {"action": "size", "w": 200, "h": 150, "t": 6, "material": "Plywood"})
    check(code == 200 and res["plate"]["set"], "plate size set")
    code, res = await http("POST", "/api/setup", {"start_from_screen": True, "accept": True})
    check(code == 200 and res["start_from_screen"] and hub.start_enabled(), "then it turns on")
    code, res = await http("POST", "/api/setup", {"anchor1": ["-210.5", -195]})
    check(code == 200 and hub.anchor1 == [-210.5, -195.0] and res["anchor"]["mode"] == "manual", "Anchor 1 typed")
    code, res = await http("POST", "/api/setup", {"anchor1": [1]})
    check(code == 400 and hub.anchor1 == [-210.5, -195.0], "a wrong Anchor 1 is refused and changes nothing")
    code, res = await http("POST", "/api/setup", {"run_defaults": {"probe": None, "margin": False,
                                                                   "level": {"i": 3, "j": 3, "h": 1.5}}})
    check(code == 200 and hub.cfg["run_defaults"]["level"] == {"i": 3, "j": 3, "h": 1.5}, "run settings saved")
    code, res = await http("POST", "/api/setup", {"plate_layouts": "/sd/gcodes/sample-sheet\n"})
    check(code == 200 and hub.layout_folders == {"/sd/gcodes/sample-sheet"}, "layout folders saved")
    code, res = await http("POST", "/api/screen", {"clock_24h": True})
    check(code == 200 and res["clock_24h"] and not res["flip"], "24-hour clock on the small screen")
    hub_b = Hub(copy.deepcopy(cfg), JobStore(state_dir, []))
    check(hub_b.start_enabled() and hub_b.anchor1 == [-210.5, -195.0] and hub_b.layout_folders == {"/sd/gcodes/sample-sheet"}
          and hub_b.screen_info()["clock_24h"] and hub_b.plates.is_set, "all kept after a restart (prefs.json, screen.json, plates.json)")
    code, res = await http("POST", "/api/setup", {"anchor1": "auto"})
    check(code == 200 and hub.anchor_auto and hub.anchor1 is None, "back to reading Anchor 1 from the Z1 (no config.txt here)")
    code, res = await http("POST", "/api/setup", {"start_from_screen": False})
    check(code == 200 and not hub.start_enabled(), "starting turned off again")
    code, res = await http("POST", "/api/setup", {"pin": "12"})
    check(code == 400, "a PIN must be 4 to 12 digits")
    code, res = await http("POST", "/api/setup", {"pin": "2468"})
    check(code == 200 and res["pin"] and "2468" not in open(os.path.join(state_dir, "prefs.json")).read(),
          "PIN set, kept only as a hash")
    try:
        code, res = await http("POST", "/api/screen", {"flip": False}, local_addr=("127.0.0.2", 0))
        far = True
    except OSError:
        far = False                                     # (no second loopback address on this computer)
    if far:
        check(code == 403 and res.get("pin"), "with a PIN, a change from another computer needs it")
        code, res = await http("POST", "/api/screen", {"flip": False}, local_addr=("127.0.0.2", 0),
                               headers={"X-Settings-PIN": "1111"})
        check(code == 403 and "not right" in res["error"], "a wrong PIN is refused")
        code, res = await http("POST", "/api/screen", {"flip": False}, local_addr=("127.0.0.2", 0),
                               headers={"X-Settings-PIN": "2468"})
        check(code == 200, "the right PIN works")
    else:
        print("  skipped - PIN from another computer (no 127.0.0.2 here)")
    code, res = await http("POST", "/api/screen", {"flip": False})
    check(code == 200, "the Pi's own screen (127.0.0.1) needs no PIN")
    code, res = await http("POST", "/api/setup", {"pin": ""})
    check(code == 200 and not res["pin"], "PIN removed")

    print("4. color check on the monitor")
    code, res = await http("POST", "/api/check", {"tool": 12})
    check(code == 200 and res["kind"] == "check", "color check started")
    st = await wait_for(lambda: _mode(state, "toolchange"), 3, "color check on the screen")
    check(st["demo"]["kind"] == "check" and st["toolchange"]["code"] == "T12" and st["toolchange"]["color"] == "#7B1FA2",
          "screen shows T12 in purple with the color-check strip")
    await http("POST", "/api/demo", {"action": "stop"})
    st = await wait_for(lambda: _mode(state, "idle"), 3, "back to Ready")
    check("demo" not in st, "stopping returns the screen to Ready")

    print("5. demo on the monitor")
    code, res = await http("POST", "/api/demo", {"action": "start", "speed": 360})
    check(code == 200 and res["active"] and res["phase"] == "ready", "demo started on the Ready screen")
    st = await wait_for(lambda: _mode(state, "toolchange"), 6, "the probe stop")
    check(st["demo"]["kind"] == "demo" and st["toolchange"]["tool"] == 0, "then stops for the probe")
    await asyncio.sleep(1.5)
    code, ds = await http("GET", "/api/demo")
    check(ds["waiting"] and ds["tool"]["tool"] == 0, "and waits there")
    await http("POST", "/api/demo", {"action": "press"})
    st = await wait_for(lambda: _tool(state, 1), 5, "the T1 stop")
    check(st["toolchange"]["index"] == 1 and st["toolchange"]["color"] == "#D32F2F", "front button (demo) leads on to the T1 stop")
    await http("POST", "/api/demo", {"action": "press"})
    st = await wait_for(lambda: _mode(state, "running"), 5, "cutting")
    check(st["demo"]["kind"] == "demo" and st["job"]["next"]["code"] == "T2", "cutting with T1, next bit T2")
    check(st.get("clock", 0) > time.time(), "the screen's time of day runs ahead at the demo speed")

    print("6. the Z1 starts a real job: the demo gives way")
    sim.start_play(SIGN)
    st = await wait_for(lambda: _real(state), 5, "the real job on the screen")
    check(st["mode"] == "toolchange" and st["toolchange"]["tool"] == 0, "real probe stop shown, no demo strip")
    code, ds = await http("GET", "/api/demo")
    check(not ds["active"] and "started a job" in ds["note"], "demo status says why it stopped")
    code, res = await http("POST", "/api/demo", {"action": "start"})
    check(code == 409 and "running a job" in res["error"], "a demo can't start while the Z1 runs a job")
    code, res = await http("POST", "/api/check", {"tool": 3})
    check(code == 409, "neither can a color check")
    sim.confirm()
    await asyncio.sleep(1.0)

    print("7. what reached the Z1")
    others = [c for c in sim.log if c != "progress"]
    check(not others, "only status and progress requests (%s)" % (others[:3] or "none other"))
    task.cancel()
    print("settings and demo checks passed")


async def _mode(get, mode):
    st = await get()
    return st if st.get("mode") == mode else None


async def _tool(get, tool):
    st = await get()
    return st if st.get("mode") == "toolchange" and st["toolchange"]["tool"] == tool and st.get("demo") else None


async def _real(get):
    st = await get()
    return st if st.get("mode") == "toolchange" and "demo" not in st else None


if __name__ == "__main__":
    asyncio.run(main())
