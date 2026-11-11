"""Makera Studio connected straight to the Z1 (not through the Pi): the screen says
so, and picks the job up as soon as Makera Studio disconnects."""
from __future__ import annotations
import asyncio, copy, os, sys, tempfile, time, json
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
    cfg.update({"discovery_port": 43333, "relay_port": 44222, "web_port": 48080, "announce_to": ["127.0.0.1"],
                "announce_port": 44333, "forward_ports": [], "poll_interval": 0.5})
    store = JobStore(tempfile.mkdtemp(), [make_library((SIGN,))])
    sim = SimMachine(port=43222, announce_to=("127.0.0.1", 43333), speed=20)
    sim.files[SIGN] = sample_files()[SIGN]
    await sim.start()
    app = await SimApp().connect("127.0.0.1", 43222)       # straight to the machine
    await asyncio.sleep(1.0)
    hub = Hub(cfg, store); web = Web(hub, 48080)
    task = asyncio.create_task(hub.run(web))
    st = await wait_for(lambda: _mode(48080, "direct"), 15, "direct-connection message")
    check(True, "screen says Makera Studio is connected straight to the Z1")
    check(sim.refused == 0, "Pi did not compete for the machine")
    await app.command("play " + SIGN)
    await asyncio.sleep(0.8)
    sim.confirm()                                          # probe
    await asyncio.sleep(0.5)
    await app.close()
    st = await wait_for(lambda: _known(48080), 30, "job picked up after the app left")
    check(st["job"]["file"] == "sample-sign.nc", "file found with 'progress' and the shipped timing")
    check(st["job"]["changes_total"] == 4, "next bit known from the library")
    task.cancel()
    print("direct-connection fallback passed")

async def _mode(port, mode):
    try:
        st = await http_get(port, "/state")
    except OSError:
        return None
    return st if st.get("mode") == mode else None

async def _known(port):
    try:
        st = await http_get(port, "/state")
    except OSError:
        return None
    return st if st.get("job") and st["job"].get("known") else None

asyncio.run(main())
