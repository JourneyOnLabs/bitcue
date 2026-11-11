"""Bit life against the pretend Z1: the made-up sample sign (probe, T1, T2, T3, T1) run twice. Each stop names
the bit to put in; at the first run's T2 stop a second bit of that kind is added and picked (the tap on the
screen); the time counted for each bit matches the Z1's own job clock; the next run suggests the least used bit.
Nothing is sent to the Z1 for any of it."""
from __future__ import annotations
import asyncio, copy, json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..")); sys.path.insert(0, HERE)
from sim_machine import SimMachine  # noqa
from sim_app import SimApp  # noqa
from z1screen import bitlife, config  # noqa
from z1screen.hub import Hub  # noqa
from z1screen.jobs import JobStore  # noqa
from z1screen.web import Web  # noqa
from run_sim import http_get, wait_for  # noqa
from common import SIGN, check, make_library, sample_files  # noqa

WEB = 58180


async def post(path, body):
    r, w = await asyncio.open_connection("127.0.0.1", WEB)
    data = json.dumps(body).encode()
    w.write(("POST %s HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
             % (path, len(data))).encode() + data)
    await w.drain()
    raw = await r.read()
    w.close()
    return json.loads(raw.partition(b"\r\n\r\n")[2])


async def run_job(app, sim, at_stop):
    """Plays the sign, confirming each stop after at_stop(state) looked at it. Returns the stops seen."""
    stops = []
    await app.command("play " + SIGN)
    t0 = time.time()
    while time.time() - t0 < 120:
        st = await http_get(WEB, "/state")
        if st["mode"] == "toolchange" and sim.waiting:
            await asyncio.sleep(1.2)                       # the screen data settles
            st = await http_get(WEB, "/state")
            stops.append(st["toolchange"])
            await at_stop(st)
            sim.confirm()
            await asyncio.sleep(0.5)
        elif st["mode"] == "idle" and st.get("last_job") and stops:
            return stops
        await asyncio.sleep(0.15)
    raise AssertionError("the job didn't end")


async def main():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update({"discovery_port": 53433, "relay_port": 54322, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 54433, "forward_ports": [], "poll_interval": 0.3})
    store = JobStore(tempfile.mkdtemp(), [make_library((SIGN,))])
    sim = SimMachine(port=53322, announce_to=("127.0.0.1", 53433), speed=120)
    sim.files[SIGN] = sample_files()[SIGN]
    await sim.start()
    hub = Hub(cfg, store)
    task = asyncio.create_task(hub.run(Web(hub, WEB)))
    try:
        await wait_for(lambda: hub.own is not None and hub.status is not None, 10, "own connection")
        app = await SimApp().connect("127.0.0.1", 54322)
        sent0 = len(sim.log)

        print("1. the first run: each kind gets its bit A; at T2 a bit B is added and picked")

        async def first(st):
            tc = st["toolchange"]
            if tc["tool"] == 0:
                check("bit" not in tc, "the probe stop names no bit")
                return
            check(tc["bit"]["label"] == "A", "T%d's stop: put bit A in (%s)" % (tc["tool"], tc["bit"]["text"]))
            if tc["tool"] == 2 and tc.get("index") == 2:
                await post("/api/bitlife", {"action": "add", "kind": tc["bit"]["kind"]})
                await post("/api/bitlife", {"action": "next", "slot": 2})
                await asyncio.sleep(1.5)
                again = (await http_get(WEB, "/state"))["toolchange"]
                check(again["bit"]["label"] == "B" and again["bit"]["choices"] == 2,
                      "after the tap the stop says bit B, of 2 (%s)" % again["bit"]["text"])
        stops = await run_job(app, sim, first)
        check([s["tool"] for s in stops] == [0, 1, 2, 3, 1], "stops: %s" % [s["tool"] for s in stops])
        elapsed = sim.elapsed
        await asyncio.sleep(1.0)
        life = hub.bitlife
        kinds = {k["label"]: k for k in life.info()["kinds"]}
        check(len(kinds) == 3, "three kinds of bit: %s" % sorted(kinds))
        t2 = next(k for k in kinds.values() if any(b["label"] == "B" for b in k["bits"]))
        a2, b2 = sorted(t2["bits"], key=lambda b: b["label"])
        check(not a2["secs"] and sum(b2["secs"].values()) > 0, "T2's time went to bit B, the one picked")
        total = sum(sum(b["secs"].values()) for b in life.d["bits"].values())
        prof = store.exact(SIGN)
        probe_t = prof["changes"][0]["t"] if prof["changes"][0]["tool"] == 0 else 0
        want = elapsed - probe_t * 1.0
        check(abs(total - want) <= max(60.0, 0.1 * want),
              "all bits together: %.0f s, the job clock without the probing: %.0f s" % (total, want))
        t1 = next(i for i, b in life.d["bits"].items() if b["kind"] == bitlife.kind_key(stops[1]["label"]))
        check(life.d["bits"][t1]["jobs"] == 1 and len([e for e in life.d["events"] if e["bit"] == t1]) == 2,
              "T1 cut twice in the job: two stretches, one job")

        print("2. the next run suggests the least used bit")
        seen = []

        async def second(st):
            tc = st["toolchange"]
            if tc["tool"] == 2:
                seen.append(tc["bit"]["label"])
        await run_job(app, sim, second)
        check(seen == ["A"], "T2: bit A now (B has cut): %s" % seen)
        await asyncio.sleep(1.0)
        a2 = life.d["bits"][a2["id"]]
        check(sum(a2["secs"].values()) > 0, "and A got the time this run")
        check(life.d["bits"][t1]["jobs"] == 2, "the same file run again is another job")
        saved = json.load(open(os.path.join(store.state_dir, bitlife.FILE_NAME)))
        check(saved["bits"] == life.d["bits"], "kept in the Pi's state folder")
        check(not any(c.startswith(("G", "M", "buffer")) for c in sim.log[sent0:]), "nothing about bits is sent to the Z1")
    finally:
        task.cancel()
    print("bit life against the pretend Z1 passed")

asyncio.run(main())
