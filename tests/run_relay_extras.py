"""Relay edge cases: a second app connection, an app that never asks for the
status, and the plain port pass-through used for the camera and web page."""
from __future__ import annotations
import asyncio, copy, json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..")); sys.path.insert(0, HERE)
from sim_machine import SimMachine  # noqa
from z1screen import config, framing as fr  # noqa
from z1screen.hub import Hub  # noqa
from z1screen.jobs import JobStore  # noqa
from z1screen.web import Web  # noqa

def check(c, msg):
    if not c: raise AssertionError(msg)
    print("  ok -", msg)

async def main():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 33333, "relay_port": 34222, "web_port": 38080, "announce_to": ["127.0.0.1"],
                "announce_port": 34333, "forward_ports": ["38082:39082"], "poll_interval": 0.5})
    store = JobStore(tempfile.mkdtemp(), [])
    hub = Hub(cfg, store); web = Web(hub, 38080)
    sim = SimMachine(port=33222, announce_to=("127.0.0.1", 33333))
    await sim.start()
    # pretend camera/web server on the machine
    async def cam(r, w):
        await r.read(100); w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\ncamera"); await w.drain(); w.close()
    camsrv = await asyncio.start_server(cam, "127.0.0.1", 39082)
    task = asyncio.create_task(hub.run(web))
    t = time.time()
    while not (hub.own and hub.status) and time.time() - t < 10: await asyncio.sleep(0.1)
    check(hub.own is not None, "Pi connected on its own")

    print("1. app that never asks for the status")
    r1, w1 = await asyncio.open_connection("127.0.0.1", 34222)
    w1.write(fr.build_frame(fr.CTRL_MULTI, b"version")); await w1.drain()
    p = fr.FrameParser(); got = []
    t = time.time()
    while time.time() - t < 8:
        try: d = await asyncio.wait_for(r1.read(65536), 0.5)
        except asyncio.TimeoutError: continue
        got += [x for x in p.feed(d) if x[0] == fr.STATUS_RES]
        if len(got) >= 2: break
    check(len(got) >= 2, "Pi asked for the status between the app's frames (%d replies)" % len(got))
    check(time.time() - hub.status_t < 3, "screen kept getting fresh status")

    print("2. second app while the first is connected")
    r2, w2 = await asyncio.open_connection("127.0.0.1", 34222)
    closed = False
    try:
        d = await asyncio.wait_for(r2.read(100), 10)
        closed = d == b""
    except asyncio.TimeoutError:
        closed = False
    check(closed, "second app connection closed because the machine allows one (sim refused %d)" % sim.refused)
    w1.write(fr.build_frame(fr.CTRL_SINGLE, b"?")); await w1.drain()
    d = await asyncio.wait_for(r1.read(65536), 3)
    check(any(x[0] == fr.STATUS_RES for x in p.feed(d)), "first app still works")
    w1.close(); await asyncio.sleep(2.5)
    check(hub.own is not None and not hub.clients, "Pi reconnected on its own after the app left")

    print("3. camera/web port passes straight through")
    r3, w3 = await asyncio.open_connection("127.0.0.1", 38082)
    w3.write(b"GET / HTTP/1.1\r\n\r\n"); await w3.drain()
    body = await asyncio.wait_for(r3.read(), 5)
    check(body.endswith(b"camera"), "forwarded port answered by the machine")
    dbg = await (await asyncio.open_connection("127.0.0.1", 38080))[1].wait_closed() if False else None
    task.cancel(); camsrv.close()
    print("all relay checks passed")

asyncio.run(main())
