"""End-to-end test with a pretend Z1 and a pretend Makera Studio.

Checks: the Pi finds the machine, holds its connection, hands over to the app,
copies a compressed download and an upload, follows a whole job through every
tool change (with the app disconnecting part way), learns the file name with
"progress" after a restart in the middle of a job, and reports the job done.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
import time

from common import HERE, SIGN, TAG, check, sample_files  # noqa: E402
from sim_app import SimApp  # noqa: E402
from sim_machine import SimMachine  # noqa: E402
from z1screen import config  # noqa: E402
from z1screen.hub import Hub  # noqa: E402
from z1screen.jobs import JobStore  # noqa: E402
from z1screen.web import Web  # noqa: E402
import qlz_store  # noqa: E402

WEB = 28080
OUT = os.path.join(HERE, "out")
SNAPS = []


async def http_get(port, path):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(("GET %s HTTP/1.1\r\nHost: x\r\n\r\n" % path).encode())
    await w.drain()
    data = await r.read()
    w.close()
    head, _, body = data.partition(b"\r\n\r\n")
    return json.loads(body) if path in ("/state", "/debug") else body


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


async def main():
    state_dir = tempfile.mkdtemp(prefix="z1s-")
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 23333, "relay_port": 24222, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 24333, "forward_ports": [], "poll_interval": 0.5, "announce_interval": 0.5})
    store = JobStore(state_dir, [])
    hub = Hub(cfg, store)
    web = Web(hub, WEB)
    sim = SimMachine(port=23222, announce_to=("127.0.0.1", 23333), speed=60, compress_downloads=True)
    files = sample_files()
    sim.files[SIGN] = files[SIGN]
    tag_data = files[TAG]
    await sim.start()
    hub_task = asyncio.create_task(hub.run(web))

    print("1. finding the machine")
    await wait_for(lambda: hub.own is not None and hub.status is not None, 10, "own connection")
    await asyncio.sleep(0.8)
    st = await http_get(WEB, "/state")
    check(st["mode"] == "idle", "screen shows idle (%s)" % st["mode"])
    check(st["machine"]["name"] == "Makera_Z1P_SIM", "machine name found from its announcement")
    SNAPS.append(("idle", st))

    print("2. app connects through the relay")
    app = await SimApp().connect("127.0.0.1", 24222)
    await wait_for(lambda: len(app.statuses) >= 2, 10, "status through relay")
    check(len(hub.clients) == 1 and hub.own is None, "Pi handed its connection to the app")
    check(sim.refused == 0, "machine never saw two connections at once")

    print("3. app downloads the job (compressed) and uploads another")
    got = await app.download(SIGN)
    check(got == qlz_store.container(sim.files[SIGN]), "download passed through byte for byte (%d bytes)" % len(got))
    await wait_for(lambda: store.find(SIGN) is not None, 60, "profile from captured download")
    prof = store.find(SIGN)
    check(prof["size"] == len(sim.files[SIGN]), "captured file unpacked to the right size")
    check([c["tool"] for c in prof["changes"]] == [1, 2, 3, 1], "tool changes read from the file")
    await app.upload(TAG, tag_data)
    await wait_for(lambda: store.find(TAG) is not None, 60, "profile from captured upload")
    check(sim.files[TAG] == tag_data, "upload reached the machine intact")

    print("4. job runs; follow every tool change")
    await app.command("G10L2P0X-200.000Y-200.000")
    await app.command("buffer M495 X10Y10C90D70O-5F-5A80B60I3J3H2")
    await app.command("play " + SIGN.replace(" ", "\x01"))
    seen = []
    nexts = []
    lefts = []
    disconnected = False
    took_back = False
    t0 = time.time()
    while time.time() - t0 < 240:
        st = await http_get(WEB, "/state")
        if st["mode"] == "toolchange":
            tc = st["toolchange"]
            if not seen or seen[-1][0] != tc["tool"] or seen[-1][2] != sim.connections:
                seen.append((tc["tool"], tc.get("index"), sim.connections, tc["color"]))
                SNAPS.append(("toolchange-%s" % tc["code"], st))
                await asyncio.sleep(0.3)
                sim.confirm()
                await asyncio.sleep(0.4)
        elif st["mode"] == "running" and st["job"]:
            j = st["job"]
            if j["next"]:
                nexts.append((j["next"]["tool"], round(j["next"]["in"]), round(j["left"] or 0), round(j["percent"], 1)))
            lefts.append(j["left"])
            if len(SNAPS) < 12 and j["next"] and j["next"]["tool"] == 3 and not any(s[0] == "running" for s in SNAPS):
                SNAPS.append(("running", st))
            if len(seen) >= 3 and not disconnected:
                await app.close()
                disconnected = True
                print("   (app disconnected part way through)")
        if disconnected and hub.own is not None and not hub.clients:
            took_back = True
        elif st["mode"] == "idle" and st.get("last_job"):
            break
        await asyncio.sleep(0.15)
    tools = [s[0] for s in seen]
    print("   tool changes seen:", tools)
    check(tools == [0, 1, 2, 3, 1], "every tool change shown, in order, probe first")
    check([s[1] for s in seen[1:]] == [1, 2, 3, 4], "change numbers 1 to 4 of 4")
    check(seen[1][3] == cfg["tools"]["1"]["color"] and seen[3][3] == cfg["tools"]["3"]["color"], "colors from the sticker table")
    check(hub.app_origin and hub.app_origin["x"] == -200.0 and hub.app_origin["y"] == -200.0,
          "the work origin Makera Studio set is noted for the settings page")
    check(disconnected and took_back, "Pi took the connection back after the app left")
    st = await http_get(WEB, "/state")
    check(st["mode"] == "idle" and st["last_job"]["done"], "job reported done")
    SNAPS.append(("done", st))
    # predictions behaved
    nx_t2 = [n for n in nexts if n[0] == 3]
    check(len(nexts) > 20, "next-tool countdown shown while cutting (%d samples)" % len(nexts))
    decreasing = all(a[1] >= b[1] - 2 for a, b in zip(nx_t2, nx_t2[1:]))
    check(decreasing, "time to the V-bit change counts down")

    print("5. Pi restarts in the middle of a job (no app connected)")
    sim.speed = 15
    sim.start_play(SIGN)
    sim.confirm()
    await asyncio.sleep(3)
    hub_task.cancel()
    try:
        await hub_task
    except BaseException:
        pass
    await asyncio.sleep(1.5)
    hub2 = Hub(cfg, store)
    web2 = Web(hub2, WEB + 1)
    hub2_task = asyncio.create_task(hub2.run(web2))
    st = await wait_for(lambda: _known(WEB + 1), 30, "job identified after restart")
    check(st["job"]["file"] == "sample-sign.nc", "file name learned with 'progress'")
    SNAPS.append(("rejoined", st))
    # alarm display
    sim.halt = 12
    sim.state = "Alarm"
    await asyncio.sleep(1.5)
    st = await http_get(WEB + 1, "/state")
    check(st["mode"] == "alarm" and "probe" in st["alarm"].lower(), "alarm shown: %s" % st["alarm"])
    SNAPS.append(("alarm", st))
    hub2_task.cancel()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "snapshots.json"), "w") as fh:
        json.dump(SNAPS, fh, indent=1)
    print("all checks passed; %d screen snapshots saved" % len(SNAPS))


async def _known(port):
    try:
        st = await http_get(port, "/state")
    except OSError:
        return None
    if st.get("job") and st["job"].get("known"):
        return st
    return None


if __name__ == "__main__":
    asyncio.run(main())
