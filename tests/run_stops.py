"""The next bit must move on as soon as a tool change is confirmed, even though the
machine keeps reporting the last cutting line for a while (tool length check,
rapid moves), and in a file with no cutting moves at all (line stays 0)."""
from __future__ import annotations
import asyncio, copy, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..")); sys.path.insert(0, HERE)
from sim_machine import SimMachine  # noqa
from sim_app import SimApp  # noqa
from z1screen import config  # noqa
from z1screen.hub import Hub  # noqa
from z1screen.jobs import JobStore  # noqa
from z1screen.web import Web  # noqa
from run_sim import http_get, wait_for  # noqa
from common import SIGN, check, make_library, sample_files  # noqa

AIR = "/sd/gcodes/air-test.nc"

async def follow(port, sim, expect_tools, freeze_check):
    seen = []; bad = []
    t0 = time.time()
    while time.time() - t0 < 200:
        st = await http_get(port, "/state")
        if st["mode"] == "toolchange":
            tc = st["toolchange"]
            if not seen or seen[-1]["tool"] != tc["tool"] or seen[-1]["n"] != len(seen):
                seen.append({"tool": tc["tool"], "index": tc.get("index"), "then": (tc.get("then") or {}).get("tool"), "n": len(seen) + 1})
                await asyncio.sleep(0.3); sim.confirm(); await asyncio.sleep(0.3)
                # right after the confirm, while the line is still frozen
                st2 = await http_get(port, "/state")
                if st2["mode"] in ("running", "paused") and st2["job"]["known"]:
                    nx = st2["job"]["next"]
                    freeze_check(seen[-1], nx, st2["job"], bad)
        elif st["mode"] == "idle" and st.get("last_job"):
            break
        await asyncio.sleep(0.15)
    return seen, bad

async def main():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 53333, "relay_port": 54222, "web_port": 58080, "announce_to": ["127.0.0.1"],
                "announce_port": 54333, "forward_ports": [], "poll_interval": 0.3})
    store = JobStore(tempfile.mkdtemp(), [make_library((SIGN,))])
    air = open(os.path.join(HERE, "..", "air-test.nc"), "rb").read()
    from z1screen import gcode
    store._index(gcode.analyze(air, AIR))
    sim = SimMachine(port=53222, announce_to=("127.0.0.1", 53333), speed=120, freeze_after_change=2.0)
    sim.files[SIGN] = sample_files()[SIGN]
    sim.files[AIR] = air
    await sim.start()
    hub = Hub(cfg, store); web = Web(hub, 58080)
    task = asyncio.create_task(hub.run(web))
    await wait_for(lambda: hub.own is not None and hub.status is not None, 10, "own connection")
    app = await SimApp().connect("127.0.0.1", 54222)

    print("1. the sample sign, line frozen for a while after each change")
    order = [1, 2, 3, 1]
    def check_next(stop, nx, job, bad):
        if stop["tool"] == 0 or stop["index"] is None:
            return
        k = stop["index"]          # 1-based index of the change just made
        want = order[k] if k < len(order) else None
        got = nx["tool"] if nx else None
        if got != want or job["changes_left"] != len(order) - k:
            bad.append((stop, got, want, job["changes_left"]))
    await app.command("play " + SIGN)
    seen, bad = await follow(58080, sim, order, check_next)
    check([s["tool"] for s in seen] == [0] + order, "stops in order: %s" % [s["tool"] for s in seen])
    check([s["index"] for s in seen[1:]] == [1, 2, 3, 4], "change numbers 1 to 4")
    check(not bad, "next bit moves on right after each confirm (problems: %s)" % bad[:3])

    print("2. air test, no cutting moves (line stays 0)")
    sim.freeze_after_change = 0.0
    sim.line_zero = True
    sim.tool = 1           # T1 already in the spindle from the last job
    sim.probe_first = False
    order2 = [2, 3, 1]
    def check_next2(stop, nx, job, bad):
        k = stop["index"]
        want = order2[k] if k is not None and k < len(order2) else None
        got = nx["tool"] if nx else None
        if got != want:
            bad.append((stop, got, want))
    await asyncio.sleep(1.0)
    await app.command("play " + AIR)
    await wait_for(lambda: hub.job is not None and hub.job.get("path") == AIR, 10, "air test job started")
    await asyncio.sleep(0.6)
    seen, bad = await follow(58080, sim, order2, check_next2)
    check([s["tool"] for s in seen] == order2, "air test stops: %s" % [s["tool"] for s in seen])
    check([s["index"] for s in seen] == [1, 2, 3], "air test change numbers 1 to 3")
    check([s["then"] for s in seen] == [3, 1, None], "air test 'after this' bits: %s" % [s["then"] for s in seen])
    check(not bad, "air test next bit correct between stops (problems: %s)" % bad[:3])
    task.cancel()
    print("stop tracking passed")

asyncio.run(main())
