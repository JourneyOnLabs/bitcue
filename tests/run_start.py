"""Placing and starting jobs from the Pi, against the pretend Z1.

Checks: the job list from the Z1's own folder listing; the plate record (it
starts from a kit's record of the plate on the Z1); reading a file the Pi
hasn't seen from the Z1 (compressed, as Makera's controllers can receive it);
Anchor 1 read from the Z1's config.txt; a clear place found for a new job; the
start refused where the job would overlap a cut, run off the plate or come too
close, refused from anywhere but the Pi, and refused while starting from the
screen is off; the three start commands with the move added to the work origin;
every job's area recorded where it was cut, including a job started from Makera
Studio with its own origin and a job the Pi had not read; the stopped tag run
again from the start from Makera Studio in the same place (recorded on its own
area, marked cut at the end); a plate layout's spots, which may share their
cut-outs. All jobs are the made-up samples (tools/samples.py)."""
from __future__ import annotations
import asyncio, json, time
import urllib.parse
from common import ANCHOR, DISC, SHEET, SIGN, TAG, check, cfg as test_cfg, make_library, sample_files  # noqa
import samples  # noqa
from sim_machine import SimMachine  # noqa
from sim_app import SimApp  # noqa
from z1screen import start, platemap as pm, plates as plates_mod  # noqa
from z1screen.hub import Hub  # noqa
from z1screen.jobs import JobStore  # noqa
from z1screen.web import Web  # noqa

DISC1, DISC3 = DISC[0], DISC[2]
BADGE = "/sd/gcodes/parts/badge 30x20.nc"          # a file the Pi has never seen, with a space in its name
COASTER = "/sd/gcodes/parts/coaster.nc"            # another, started from Makera Studio before the Pi reads it
BADGE_AT = (120.0, 30.0)                           # where the badge file puts itself: on the tag's area
WEB = 45080


def badge_file():
    return samples.part(30, 20, r=6, at=BADGE_AT)


def coaster_file():
    return samples.part(24, 24)


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
    return int(head.split(b" ")[1]), json.loads(payload)


async def wait_for(cond, timeout, what):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        await asyncio.sleep(0.1)
    raise AssertionError("timed out waiting for " + what)


def q(path):
    return urllib.parse.quote(path)


async def run_job_to_end(sim, timeout=60):
    t0 = time.time()
    while sim.playing and time.time() - t0 < timeout:
        if sim.waiting:
            sim.confirm()
        await asyncio.sleep(0.05)


def card_files() -> dict:
    files = sample_files()
    out = {p: files[p] for p in (SIGN, TAG, DISC1, DISC3)}
    out[BADGE] = badge_file()
    out[COASTER] = coaster_file()
    out["/sd/config.txt"] = samples.config_txt(*ANCHOR)
    return out


async def main():
    cfg = test_cfg(anchor1="auto", plate=None)
    cfg.update({"discovery_port": 45333, "relay_port": 45224, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "announce_port": 45334, "forward_ports": [], "poll_interval": 0.5})
    sim = SimMachine(port=45222, announce_to=("127.0.0.1", 45333), speed=600, compress_downloads=True)
    sim.files.update(card_files())
    await sim.start()
    import tempfile
    state = tempfile.mkdtemp()
    lib = make_library([SIGN, TAG] + DISC, seed=True)
    hub = Hub(cfg, JobStore(state, [lib]))
    task = asyncio.create_task(hub.run(Web(hub, WEB)))
    await wait_for(lambda: hub.own is not None and hub.status is not None, 10, "the Pi's own connection")

    print("1. the plate on the Z1 starts from the kit's record; the Z1's config.txt gives Anchor 1")
    code, pl = await http("GET", "/api/plate")
    check(code == 200 and [(c["name"], c["state"]) for c in pl["cuts"]] == [("Sample cut", "cut"),
                                                                            ("Sample tag", "started")]
          and pl["plate"]["w"] == 200 and pl["plate"]["t"] == 6,
          "plate 200 x 150 x 6 with a cut area and the tag stopped part way")
    await wait_for(lambda: hub.machine_cfg.get("anchor1"), 15, "config.txt to be read")
    check(hub.machine_cfg["anchor1"] == ANCHOR and hub.anchor1 == ANCHOR and hub.anchor_auto,
          "config.txt read from the Z1 (compressed transfer): Anchor 1 X-200 Y-200")
    check(not any(c.startswith(("G", "M", "buffer", "play")) for c in sim.log), "reading it sent nothing that moves the Z1")

    print("2. the job list: the layout folder lists designs, the others list files")
    code, jobs = await http("GET", "/api/jobs?refresh=1")
    check(jobs["start_enabled"] and jobs["plate_set"], "starting from the screen is on and the plate is set")
    f = {x["name"]: x for x in jobs["folders"]}
    check(set(f) == {"parts", "sample-sheet", "samples"}, "folders on the Z1: %s" % sorted(f))
    check(f["sample-sheet"]["layout"] and [(i["kind"], i["name"], i["files"]) for i in f["sample-sheet"]["items"]]
          == [("design", "Disc", 2)], "the sample sheet is a layout: one design (Disc) with 2 of its 4 files on the card")
    fp = {i["name"]: i for i in f["samples"]["items"]}
    check(set(fp) == {"sample-sign", "sample-tag"} and all(i["kind"] == "file" and i["read"] for i in fp.values()),
          "samples: plain files the Pi knows")
    pa = {i["file"]: i for i in f["parts"]["items"]}
    check(set(pa) == {"badge 30x20.nc", "coaster.nc"} and not pa["badge 30x20.nc"]["read"], "parts: two files not read yet")

    print("3. placing: the sign fits where its file puts it, the tag is partly cut")
    code, dv = await http("GET", "/api/place?path=" + q(SIGN))
    check(code == 200 and dv["read"] and dv["why_not"] == "" and dv["start_at"] == [0.0, 0.0],
          "sign: its own place is clear (start at offset 0)")
    check(dv["run_source"] == "file" and dv["run"]["level"] == {"i": 3, "j": 3, "h": 2.0}, "it runs with its own run window")
    code, tv = await http("GET", "/api/place?path=" + q(TAG))
    plate = pm.Plate(200, 150, pm.Grid.from_json(tv["occ"]))
    r = plate.check(pm.Grid.from_json(tv["foot"]), 0, 0, tv["probe"], tv["level"], tv["path_box"])
    check(r["code"] == "overlap", "tag at its own place overlaps its own earlier cut")

    print("4. reading a file the Pi hasn't seen, from the Z1")
    code, rd = await http("POST", "/api/read", {"path": BADGE})
    check(code == 200 and rd["state"] in ("waiting", "reading", "profiling", "done"), "reading started: %s" % rd)
    await wait_for(lambda: hub.store.exact(BADGE) is not None, 20, "the badge to be read")
    code, rd = await http("GET", "/api/read?path=" + q(BADGE))
    check(rd["state"] == "done", "read and checked")
    code, bv = await http("GET", "/api/place?path=" + q(BADGE))
    check(bv["run_source"] == "default" and bv["run"]["margin"] and bv["run"]["level"] is None
          and bv["run"]["probe"] == {"rel": "work", "x": 135.0, "y": 40.0},
          "no run window in the file: edge scan on, Z probe in the middle (X135 Y40), leveling off")
    at = bv["start_at"]
    check(at is not None and at != [0.0, 0.0], "its own place (on the tag) isn't clear; the Pi suggests a move of X%s Y%s" % tuple(at))
    foot = pm.Grid.from_json(bv["foot"])
    plate = pm.Plate(200, 150, pm.Grid.from_json(bv["occ"]))
    ok = plate.check(foot, int(at[0] / 0.5), int(at[1] / 0.5), bv["probe"], bv["level"], bv["path_box"])
    check(ok["ok"], "the suggested place passes the check (nearest cut %s mm)" % ok["near"])

    print("5. starts that must be refused")
    code, res = await http("POST", "/api/start", {"path": BADGE, "plate_checked": True, "offset": [0, 0]})
    check(code == 400 and "overlaps" in res["error"] and "Nothing was started" in res["error"], "on the tag: " + res["error"])
    code, res = await http("POST", "/api/start", {"path": BADGE, "plate_checked": True, "offset": [60, 20]})
    check(code == 400 and "off the plate" in res["error"], "off the plate: " + res["error"])
    code, res = await http("POST", "/api/start", {"path": BADGE, "plate_checked": True, "offset": [at[0] + 0.3, at[1]]})
    check(code == 400 and "0.5 mm steps" in res["error"], "not a 0.5 mm step: " + res["error"])
    code, res = await http("POST", "/api/start", {"path": BADGE, "offset": at})
    check(code == 400 and "Check the plate" in res["error"], "plate not checked")
    code, res = await http("POST", "/api/start", {"path": TAG, "plate_checked": True})
    check(code == 400 and "overlaps" in res["error"], "the tag again: " + res["error"])
    hub.cfg["start_from_screen"] = False
    code, res = await http("POST", "/api/start", {"path": BADGE, "plate_checked": True, "offset": at})
    check(code == 400 and "turned off" in res["error"], "starting from the screen turned off: " + res["error"])
    hub.cfg["start_from_screen"] = True
    check(not any(c.startswith(("G10", "buffer", "play")) for c in sim.log), "nothing reached the Z1")

    print("6. start the badge where the Pi suggested")
    code, res = await http("POST", "/api/start", {"path": BADGE, "plate_checked": True, "offset": at})
    check(code == 200 and res.get("ok"), "started: %s" % res)
    sent = [c for c in sim.log if c.startswith(("G10", "buffer", "play"))]
    prof = hub.store.exact(BADGE)
    run, _ = start.run_for(prof)
    want = start.commands(prof, ANCHOR, run, tuple(at))
    check(sent == want, "the Z1 got: %s" % sent)
    check(sent[0] == "G10L2P0X%.3fY%.3f" % (ANCHOR[0] + at[0], ANCHOR[1] + at[1]), "work origin = Anchor 1 + the move")
    check(sent[1].startswith("buffer M495 X") and "C" in sent[1] and "O" in sent[1] and "A" not in sent[1],
          "edge scan and Z probe, no leveling: " + sent[1])
    check(sent[2] == "play /sd/gcodes/parts/badge\x0130x20.nc", "the space in the name is coded")
    await wait_for(lambda: hub.job is not None and hub.job.get("profile"), 5, "the screen to follow the job")
    sim.confirm()                                                    # the probe
    await wait_for(lambda: sim.waiting and sim.target == 1, 10, "the T1 stop")
    sim.confirm()
    await wait_for(lambda: any(c["name"] == "badge 30x20" for c in hub.plates.cuts), 10, "its area to be recorded")
    rec = next(c for c in hub.plates.cuts if c["name"] == "badge 30x20")
    check(rec["state"] == "started" and rec["by"] == "pi" and rec["shift"] == at, "recorded once cutting began, at %s" % rec["shift"])
    await run_job_to_end(sim)
    await wait_for(lambda: rec["state"] == "cut", 10, "the area to be marked cut")
    check(True, "marked cut when the job finished")
    code, bv2 = await http("GET", "/api/place?path=" + q(BADGE))
    check(bv2["start_at"] not in (None, at), "the next badge goes somewhere else: %s" % bv2["start_at"])

    print("7. a job started from Makera Studio, with its own origin, on a file the Pi hasn't read")
    app = await SimApp().connect("127.0.0.1", 45224)
    await wait_for(lambda: bool(hub.clients) and hub.status is not None and not hub.status.get("playing"), 10,
                   "Makera Studio through the Pi")
    code, res = await http("POST", "/api/read", {"path": COASTER})
    check(res["state"] == "error" and "Makera Studio is connected" in res["error"],
          "the Pi doesn't read files while Makera Studio is connected through it")
    origin = [ANCHOR[0] + 100.0, ANCHOR[1] + 100.0]
    await app.command("G10L2P0X%.3fY%.3f" % tuple(origin))
    await app.command("buffer M495 X0Y0P1")
    await app.command("play " + COASTER)
    await wait_for(lambda: hub.job is not None, 10, "the job to show")
    sim.confirm()
    await wait_for(lambda: sim.waiting and sim.target == 1, 10, "T1")
    sim.confirm()
    await wait_for(lambda: any(c["file"] == COASTER for c in hub.plates.cuts), 15, "its area to be recorded")
    rec = next(c for c in hub.plates.cuts if c["file"] == COASTER)
    check(rec["by"] == "app" and rec["shift"] == [100.0, 100.0] and rec["shape"] is None,
          "recorded at X100 Y100 from the work origin, shape not known yet")
    check(hub.app_origin and hub.app_origin["x"] == origin[0], "the work origin Makera Studio set is noted")
    await run_job_to_end(sim)
    await wait_for(lambda: hub.job is None, 15, "the job to end")
    code, res = await http("POST", "/api/start", {"path": SIGN, "plate_checked": True})
    check(code == 400 and "needs to read coaster" in res["error"],
          "no start while a cut area's shape is unknown: " + res["error"])

    print("7b. the stopped tag run again from the start, from Makera Studio, in the same place")
    tag = next(c for c in hub.plates.cuts if c["name"] == "Sample tag")
    n_cuts = len(hub.plates.cuts)
    await app.command("G10L2P0X%.3fY%.3f" % tuple(ANCHOR))
    await app.command("buffer M495 X110Y20C170D60O-6F-6")
    await app.command("play " + TAG)
    await wait_for(lambda: hub.job is not None and hub.job.get("profile"), 10, "the tag job to show")
    sim.confirm()                                                    # the probe
    await wait_for(lambda: sim.waiting and sim.target == 1, 10, "T1")
    sim.confirm()
    await wait_for(lambda: (hub.job or {}).get("cut_id") == tag["id"], 15, "the run to use the tag's area")
    check(len(hub.plates.cuts) == n_cuts and tag["state"] == "started",
          "no new area: the run is recorded on the tag's own stopped area")
    await run_job_to_end(sim, timeout=120)
    await wait_for(lambda: tag["state"] == "cut", 15, "the tag to be marked cut")
    check(True, "the tag is marked cut when the run finishes")
    await wait_for(lambda: hub.job is None, 15, "the tag job to end")
    await app.close()
    await wait_for(lambda: rec.get("shape") is not None, 30, "the Pi to read the coaster file once Makera Studio left")
    g = plates_mod.Plates.area(rec)
    check(g.bbox_mm()[0] >= 97 and g.bbox_mm()[1] >= 97, "its area sits at X%s Y%s" % tuple(g.bbox_mm()[:2]))

    print("8. the sample sheet: a new plate, then spots that share their cut-outs")
    code, before = await http("GET", "/api/plate")
    code, pl = await http("POST", "/api/plate", {"action": "new"})
    check(code == 200 and pl["cuts"] == [], "NEW PLATE: nothing cut")
    check(pl["previous"]["cuts"] == len(before["cuts"]) > 0 and pl["previous"]["w"] == before["plate"]["w"],
          "the plate before it is kept: %d cut areas" % pl["previous"]["cuts"])
    code, back = await http("POST", "/api/plate", {"action": "previous"})
    check(code == 200 and [c["id"] for c in back["cuts"]] == [c["id"] for c in before["cuts"]]
          and back["previous"]["cuts"] == 0, "LAST PLATE brings it back, and keeps the bare plate in its place")
    code, pl = await http("POST", "/api/plate", {"action": "new", "w": 250, "h": 180, "t": 6.35, "material": "Plywood"})
    check(code == 200 and pl["cuts"] == [] and (pl["plate"]["w"], pl["plate"]["h"], pl["plate"]["t"]) == (250, 180, 6.35)
          and pl["previous"]["cuts"] == len(before["cuts"]), "NEW PLATE with its own size, 250 x 180 x 6.35 mm")
    code, pl = await http("POST", "/api/plate", {"action": "size", "w": before["plate"]["w"], "h": before["plate"]["h"],
                                                 "t": before["plate"]["t"], "material": before["plate"]["material"]})
    check(code == 200 and pl["plate"]["w"] == before["plate"]["w"], "the sample sheet's size again")
    code, dv = await http("GET", "/api/design?folder=%s&design=disc" % SHEET)
    by_n = {s["n"]: s for s in dv["spots"]}
    check(by_n[1]["state"] == "free" and not by_n[1]["why"] and by_n[3]["state"] == "free", "spots 1 and 3 free")
    check("no longer on the Z1" in by_n[2]["why"] and "no longer on the Z1" in by_n[4]["why"],
          "spots 2 and 4: the Pi knows their files, but they aren't on the card: %s" % by_n[2]["why"])
    check(dv["files"][by_n[1]["key"]]["run"][1] == "Z probe: X30 Y30 on the plate", "Z probe at X30 Y30 on the plate")
    code, res = await http("POST", "/api/plate", {"action": "spot", "folder": SHEET,
                                                  "key": by_n[1]["key"], "state": "cut", "design": "disc"})
    check(code == 200 and res["cuts"][0]["spot"] == by_n[1]["key"], "spot 1 marked cut by hand")
    code, dv = await http("GET", "/api/design?folder=%s&design=disc" % SHEET)
    by_n = {s["n"]: s for s in dv["spots"]}
    check(by_n[1]["state"] == "cut" and by_n[3]["state"] == "free" and not by_n[3]["why"],
          "spot 3 is still free: the layout's parts share their cut-outs")
    sim.log.clear()
    wco = hub._wco()
    at_anchor = wco is not None and abs(wco[0] - ANCHOR[0]) < 0.011 and abs(wco[1] - ANCHOR[1]) < 0.011
    code, res = await http("POST", "/api/start", {"path": DISC3, "plate_checked": True})
    check(code == 200, "spot 3 started: %s" % res)
    sent = [c for c in sim.log if c.startswith(("G10", "buffer", "play"))]
    want = start.commands(hub.store.exact(DISC3), ANCHOR)
    check(sent == (want[1:] if at_anchor else want),
          "its own run settings, at Anchor 1 (%s): %s" % ("already the work origin" if at_anchor else "set first", sent))
    await run_job_to_end(sim)
    await wait_for(lambda: hub.plates.spot_cut(SHEET, by_n[3]["key"]) is not None, 10, "spot 3 record")
    code, bv3 = await http("GET", "/api/place?path=" + q(BADGE))
    plate = pm.Plate(200, 150, pm.Grid.from_json(bv3["occ"]))
    r = plate.check(pm.Grid.from_json(bv3["foot"]), int(-90 / 0.5), int(45 / 0.5), None, None, bv3["path_box"])
    check(r["code"] in ("overlap", "close"), "any other job is kept off the layout's cut areas")
    code, res = await http("POST", "/api/start", {"path": DISC1, "plate_checked": True, "offset": [10, 0]})
    check(code == 400 and "own spot" in res["error"], "a layout's part can't be moved off its spot")

    class FakeWriter:
        def __init__(self, peer):
            self.peer = peer

        def get_extra_info(self, k):
            return self.peer
    check(Web._local(FakeWriter(("127.0.0.1", 5))) and not Web._local(FakeWriter(("192.168.0.20", 5))),
          "only requests from the Pi itself count as the small screen")
    check(set(c.split(" ")[0] for c in sim.log) <= {"echo", "ls", "G10L2P0X-200.000Y-200.000", "buffer", "play",
                                                  "progress"}, "nothing else was sent: %s" % sorted(set(c.split(" ")[0] for c in sim.log)))
    task.cancel()
    print("start checks passed")

if __name__ == "__main__":
    asyncio.run(main())
