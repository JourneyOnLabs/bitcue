"""Pictures of the parts of job files the Pi already knows, against the pretend Z1: while the Z1 is idle the Pi
reads such a file again, one at a time, just to draw its part; anything asked for on the screens (here, reading
another file) stops that read at once, the Z1 told so (FILE_CAN); it is read again later. Nothing sent moves the
Z1. Made-up sample jobs only."""
from __future__ import annotations
import asyncio, json, os, tempfile, time
from common import SIGN, TAG, check, cfg as test_cfg, make_library, sample_files  # noqa
import samples  # noqa
from sim_machine import SimMachine  # noqa
from z1screen.hub import Hub  # noqa
from z1screen.jobs import JobStore  # noqa
from z1screen.web import Web  # noqa

WEB = 46580
BIG = "/sd/gcodes/parts/big plate.nc"     # known to the Pi (kit library), slow to read from the pretend Z1
NEW = "/sd/gcodes/parts/new part.nc"      # not known: tapped on the screen


async def http(method, path, body=None):
    r, w = await asyncio.open_connection("127.0.0.1", WEB)
    data = b"" if body is None else json.dumps(body).encode()
    head = "%s %s HTTP/1.1\r\nHost: x\r\n" % (method, path)
    if method == "POST":
        head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(data)
    w.write((head + "\r\n").encode() + data)
    await w.drain()
    raw = await r.read()
    w.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), payload


async def wait_for(cond, timeout, what):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        await asyncio.sleep(0.1)
    raise AssertionError("timed out waiting for " + what)


def big_file() -> bytes:
    # a plate of many small parts: a few hundred KB, so a slow read takes a while
    return b"".join(samples.part(8, 8, at=(x * 12.0, y * 12.0)) for x in range(10) for y in range(8))


async def main():
    files = sample_files()
    big, new = big_file(), samples.part(30, 20)
    lib = make_library([SIGN, TAG])
    store_probe = JobStore(tempfile.mkdtemp(), [])
    big_prof = store_probe.add_file(BIG, big)
    with open(os.path.join(lib, big_prof["md5"] + ".json"), "w") as fh:
        json.dump(big_prof, fh)
    cfg = test_cfg(anchor1="auto", plate=None)
    cfg.update({"discovery_port": 46533, "relay_port": 46524, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 46534, "forward_ports": [], "poll_interval": 0.5})
    sim = SimMachine(port=46522, announce_to=("127.0.0.1", 46533), speed=600)
    sim.files.update({SIGN: files[SIGN], TAG: files[TAG], BIG: big, NEW: new})
    await sim.start()
    store = JobStore(tempfile.mkdtemp(), [lib])
    hub = Hub(cfg, store)
    hub.preview_tried.add(BIG)                  # held back until step 2
    task = asyncio.create_task(hub.run(Web(hub, WEB)))
    known = [store.exact(p) for p in (SIGN, TAG, BIG)]
    check(all(p is not None and not store.preview_file(p["md5"]) for p in known),
          "the kit's profiles of three files on the Z1 have no pictures")
    try:
        await wait_for(lambda: hub.own is not None and hub.status is not None, 10, "the Pi's own connection")

        print("1. while the Z1 is idle, the Pi reads them again to draw their parts")
        await wait_for(lambda: all(store.preview_file(p["md5"]) for p in known[:2]), 60, "the sign's and tag's pictures")
        check(True, "the sample sign and tag have pictures now")
        check(("download", SIGN) in sim.transfers and ("download", TAG) in sim.transfers, "read from the Z1's card")
        check(not any(c.startswith(("G", "M", "buffer", "play")) for c in sim.log), "nothing sent moves the Z1")
        code, body = await http("GET", "/api/jobs?refresh=1")
        items = {i["path"]: i for f in json.loads(body)["folders"] for i in f["items"] if i["kind"] == "file"}
        check(items[SIGN]["preview"] == known[0]["md5"], "the job list names the sign's picture")
        code, png = await http("GET", "/preview/%s.png" % known[0]["md5"])
        check(code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", "and serves it")

        print("2. a tap on the screen stops a read done only for a picture")
        sim.packet_delay = 0.5
        hub.preview_tried.discard(BIG)
        await wait_for(lambda: ("download", BIG) in sim.transfers, 30, "the big plate's read for its picture")
        await asyncio.sleep(1.0)
        t0 = time.time()
        code, body = await http("POST", "/api/read", {"path": NEW})
        check(code == 200, "a file not read yet is tapped on the screen")
        await wait_for(lambda: ("cancel", BIG) in sim.transfers, 5, "the picture read to stop")
        check(True, "the read for the picture stops at once (%.1f s), and the Z1 is told so" % (time.time() - t0))
        sim.packet_delay = 0.0
        await wait_for(lambda: store.exact(NEW) is not None, 20, "the tapped file to be read")
        check(sim.transfers.index(("cancel", BIG)) < sim.transfers.index(("download", NEW)),
              "the tapped file is read after it, on the same connection")
        await wait_for(lambda: store.preview_file(store.exact(NEW)["md5"]), 30, "the tapped file's picture")
        check(True, "the tapped file gets its picture too")
        await wait_for(lambda: store.preview_file(known[2]["md5"]), 60, "the big plate's picture")
        check(sim.transfers.count(("download", BIG)) >= 2, "the big plate is read again later and drawn")

        print("3. turned off in the settings: no reads for pictures")
        hub.cfg["read_for_previews"] = False
        n = len(sim.transfers)
        os.remove(store.preview_file(known[0]["md5"]))
        hub.preview_tried.clear()
        await asyncio.sleep(8)
        check(len(sim.transfers) == n, "nothing read")
    finally:
        task.cancel()
    print("preview reads against the pretend Z1 passed")

asyncio.run(main())
