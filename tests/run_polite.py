"""Polite mode: the Pi connects only briefly, so an app can connect straight to the Z1."""
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

async def main():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 63333, "relay_port": 64222, "web_port": 61080, "announce_to": ["127.0.0.1"],
                "announce_port": 64333, "forward_ports": [], "poll_interval": 0.5, "connection": "polite",
                "polite_gap": 3.0})
    store = JobStore(tempfile.mkdtemp(), [make_library((SIGN,))])
    sim = SimMachine(port=63222, announce_to=("127.0.0.1", 63333), speed=20)
    sim.files[SIGN] = sample_files()[SIGN]
    await sim.start()
    hub = Hub(cfg, store); web = Web(hub, 61080)
    task = asyncio.create_task(hub.run(web))
    await wait_for(lambda: hub.status is not None, 15, "first status")
    busy = 0; samples = 0
    t = time.time()
    while time.time() - t < 8:
        samples += 1; busy += sim.client is not None
        await asyncio.sleep(0.05)
    check(busy / samples < 0.35, "machine free most of the time (%.0f%% held)" % (100 * busy / samples))
    st = await http_get(61080, "/state")
    check(st["mode"] == "idle", "screen shows the machine while polite (%s)" % st["mode"])
    # app connects straight to the machine (retry like an app would if it hits the brief window)
    app = None
    for _ in range(10):
        try:
            app = await SimApp().connect("127.0.0.1", 63222)
            await asyncio.sleep(0.5)
            if not app.closed and app.statuses:
                break
        except OSError:
            pass
        await asyncio.sleep(0.5)
    check(app is not None and app.statuses, "app connected straight to the Z1")
    conns = sim.connections
    await asyncio.sleep(6)
    check(sim.connections == conns and sim.refused == 0, "Pi stayed away while the app was connected")
    await app.command("play " + SIGN)
    await asyncio.sleep(0.8); sim.confirm(); await asyncio.sleep(0.4)
    await app.close()
    st = await wait_for(lambda: _known(61080), 30, "job picked up")
    check(st["job"]["changes_total"] == 4, "job and next bit shown after the app left")
    task.cancel()
    print("polite mode passed")

async def _known(port):
    try:
        st = await http_get(port, "/state")
    except OSError:
        return None
    return st if st.get("job") and st["job"].get("known") else None

asyncio.run(main())
