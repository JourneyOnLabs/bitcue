"""The Pi's software restarts part way through a job while Makera Studio is
connected through the Pi (for example while an update is installed at a tool
change). The screen asks the machine which file is running, with a progress
request (the only command the Pi adds), and shows the job again with the
bits' names (type and size). When it restarts again later in the job, the job
keeps the area it had on the plate: no area for "a job the Pi couldn't name"
while the file name isn't known yet."""
from __future__ import annotations
import asyncio, copy, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..")); sys.path.insert(0, HERE)
from sim_machine import SimMachine  # noqa
from z1screen import config, framing as fr  # noqa
from z1screen.hub import Hub  # noqa
from z1screen.jobs import JobStore  # noqa
from z1screen.web import Web  # noqa
from run_sim import http_get  # noqa
from common import ANCHOR, SIGN, check, make_library, sample_files  # noqa

LIB = make_library((SIGN,))


async def main():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 61333, "relay_port": 61222, "web_port": 61080, "announce_to": ["127.0.0.1"],
                "announce_port": 61334, "forward_ports": [], "poll_interval": 0.5,
                "anchor1": list(ANCHOR), "read_machine_config": False})
    sim = SimMachine(port=61223, announce_to=("127.0.0.1", 61333), speed=60)
    sim.files[SIGN] = sample_files()[SIGN]
    sim.wco = list(ANCHOR)                          # the job's origin is at Anchor 1
    await sim.start()
    state = tempfile.mkdtemp()

    print("1. the sample sign runs on the machine and waits at the T1 change")
    sim.start_play(SIGN)
    sim.confirm()                                   # the probe was put in
    t = time.time()
    while not (sim.waiting and sim.target == 1) and time.time() - t < 10:
        sim.status()
        await asyncio.sleep(0.02)
    check(sim.waiting and sim.target == 1, "machine waits for T1, the job's first change")

    print("2. the Pi's software starts again while Makera Studio connects through it")
    hub = Hub(cfg, JobStore(state, [LIB]))
    hub.own_next_try = time.time() + 1e9            # the app gets there first: no connection of the Pi's own
    web = Web(hub, 61080)
    task = asyncio.create_task(hub.run(web))
    t = time.time()
    while not hub.m_ip and time.time() - t < 10:
        await asyncio.sleep(0.05)
    check(bool(hub.m_ip), "Pi found the machine")
    r, w = await asyncio.open_connection("127.0.0.1", 61222)
    w.write(fr.build_frame(fr.CTRL_MULTI, b"version"))
    await w.drain()
    parser, got = fr.FrameParser(), []

    async def app_talks():
        while True:
            w.write(fr.build_frame(fr.CTRL_SINGLE, b"?"))
            await w.drain()
            try:
                d = await asyncio.wait_for(r.read(65536), 0.5)
            except asyncio.TimeoutError:
                continue
            if not d:
                return
            got.extend(parser.feed(d))

    app = asyncio.create_task(app_talks())
    t = time.time()
    while not (hub.job and hub.job.get("profile") is not None) and time.time() - t < 25:
        await asyncio.sleep(0.1)
    check(hub.job is not None and hub.job.get("path") == SIGN, "screen learned the running file: " + str((hub.job or {}).get("path")))
    check(hub.job.get("profile") is not None, "and its timing")
    st = await http_get(61080, "/state")
    tc = st.get("toolchange") or {}
    check(st["mode"] == "toolchange" and tc.get("code") == "T1", "screen shows the T1 change")
    check(tc.get("label") == "3.175 × 12 mm flat end mill", "T1 named by type and size: " + str(tc.get("label")))
    check((tc.get("then") or {}).get("label") == "2 × 8 mm ball end mill",
          "then T2, named by type and size: " + str((tc.get("then") or {}).get("label")))
    asked = [c for c in sim.log if c == "progress"]
    check(1 <= len(asked) <= 3, "the Pi asked for the progress %d time(s)" % len(asked))
    check(set(sim.log) <= {"version", "progress"}, "only the app's own command and progress reached the machine: %s"
          % sorted(set(sim.log)))
    info = b"".join(p for t_, p in got if t_ == fr.NORMAL_INFO).decode("utf-8", "replace")
    check("sample-sign.nc" in info, "Makera Studio sees the machine's progress line too (harmless)")

    print("3. after the front button the next bit is T2, by its type and size")
    sim.confirm()
    t = time.time()
    st = {}
    while time.time() - t < 10:
        st = await http_get(61080, "/state")
        if st["mode"] == "running" and (st.get("job") or {}).get("next"):
            break
        await asyncio.sleep(0.2)
    nx = (st.get("job") or {}).get("next") or {}
    check(st["mode"] == "running" and nx.get("code") == "T2", "running, next bit T2")
    check(nx.get("label") == "2 × 8 mm ball end mill", "T2 named by type and size")
    n_before = len([c for c in sim.log if c == "progress"])
    await asyncio.sleep(3)
    check(len([c for c in sim.log if c == "progress"]) == n_before, "no more progress requests once the file is known")

    cuts = [(c["file"], c["state"]) for c in hub.plates.cuts]
    check(cuts == [(SIGN, "started")], "the job's area is on the plate: %s" % cuts)
    first = hub.plates.cuts[0]["id"]

    print("4. the Pi's software starts again at the T2 change; Makera Studio connects again")
    app.cancel()
    w.close()
    t = time.time()
    while sim.client is not None and time.time() - t < 5:
        await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except BaseException:
        pass
    t = time.time()
    while not (sim.waiting and sim.target == 2) and time.time() - t < 20:
        sim.status()
        await asyncio.sleep(0.02)
    check(sim.waiting and sim.target == 2 and sim.elapsed > 180,
          "machine waits for T2, %d s into the job" % sim.elapsed)
    hub2 = Hub(cfg, JobStore(state, [LIB]))
    hub2.own_next_try = time.time() + 1e9
    task2 = asyncio.create_task(hub2.run(Web(hub2, 61081)))
    t = time.time()
    while not hub2.m_ip and time.time() - t < 10:
        await asyncio.sleep(0.05)
    r, w = await asyncio.open_connection("127.0.0.1", 61222)
    w.write(fr.build_frame(fr.CTRL_MULTI, b"version"))
    await w.drain()
    parser = fr.FrameParser()
    app = asyncio.create_task(app_talks())
    t = time.time()
    while not (hub2.job and hub2.job.get("path") and hub2.job.get("cut_id")) and time.time() - t < 30:
        await asyncio.sleep(0.1)
    job = hub2.job or {}
    check(job.get("path") == SIGN, "screen learned the running file again")
    names = [c["name"] for c in hub2.plates.cuts]
    check(job.get("cut_id") == first and len(names) == 1, "the job uses its area from before the restart: %s" % names)

    app.cancel()
    w.close()
    task2.cancel()
    print("restart checks passed")

asyncio.run(main())
