"""Bit life (z1screen/bitlife.py): the time each real bit cuts, several bits of one kind (A, B, C), the bit to
put in at a stop, a tap on the stop screen for another, retiring bits, and the expected life that warns only once
it is set. Made-up bits, materials and jobs only."""
import asyncio, copy, json, os, tempfile
from common import check
from z1screen import bitlife, config, notify
from z1screen.hub import Hub
from z1screen.jobs import JobStore
from z1screen.web import Web

FLAT = "1.5 × 6 mm flat end mill"
VBIT = "30° V-bit, 0.2 mm tip"


def view(mode, file="part.nc", elapsed=0, tool=None, label=FLAT, tc=None):
    v = {"mode": mode, "job": None, "toolchange": None}
    if mode in ("running", "paused", "toolchange"):
        v["job"] = {"file": file, "name": "Part", "elapsed": elapsed, "known": True,
                    "now": {"tool": tool, "label": label} if tool is not None else None}
    if mode == "toolchange":
        v["toolchange"] = dict({"tool": 2, "label": FLAT}, **(tc or {}))
    return v


print("counting one bit")
state = tempfile.mkdtemp()
life = bitlife.BitLife(state)
tr = bitlife.Tracker(life)
tr.watch(view("toolchange", elapsed=100, tc={"tool": 2}), "Aluminum")
kind = bitlife.kind_key(FLAT)
check(list(life.d["kinds"]) == [kind] and [b["label"] for b in life.d["bits"].values()] == ["A"],
      "the first time a job uses a kind of bit, it is made with its first bit, A")
check(life.d["slots"]["2"]["bit"] == "b1", "and A is the bit to put in")
for e in (101, 400, 700):
    tr.watch(view("running", elapsed=e, tool=2), "Aluminum")
tr.watch(view("paused", elapsed=760, tool=2), "Aluminum")
tr.watch(view("connecting"), "Aluminum")                # a status dropout doesn't end the count
tr.watch(view("running", elapsed=1000, tool=2), "Aluminum")
tr.watch(view("toolchange", elapsed=1000, tc={"tool": 3, "label": VBIT}), "Aluminum")
a = life.d["bits"]["b1"]
check(a["secs"] == {"aluminum": 900.0}, "counted from the stop to the next stop by the job clock: %s" % a["secs"])
check(a["jobs"] == 1, "one job")
tr.watch(view("running", elapsed=1300, tool=3, label=VBIT), "Aluminum")
tr.watch(view("idle"), "Aluminum")
v = [b for b in life.d["bits"].values() if b["kind"] == bitlife.kind_key(VBIT)][0]
check(v["secs"] == {"aluminum": 300.0}, "the V-bit counted until the job ended")
tr.watch(view("toolchange", file="other.nc", elapsed=0, tc={"tool": 0, "label": ""}), "Aluminum")
tr.watch(view("running", file="other.nc", elapsed=50, tool=0, label=""), "Aluminum")
tr.watch(view("idle"), "Aluminum")
check(len(life.d["kinds"]) == 2, "the probe isn't a bit")

print("several bits of one kind")
b = life.add_bit(kind)
check(life.d["bits"][b]["label"] == "B", "a second bit of the kind is B")
tr.watch(view("toolchange", file="j2.nc", elapsed=0, tc={"tool": 2}), "Aluminum")
check(life.d["slots"]["2"]["bit"] == b, "the least used one is suggested (B, new)")
check(life.next_bit(2) == "b1" and life.next_bit(2) == b, "a tap goes to the next bit of the kind, round")
life.next_bit(2)
tr.watch(view("toolchange", file="j2.nc", elapsed=0, tc={"tool": 2}), "Aluminum")
check(life.d["slots"]["2"]["bit"] == "b1", "the bit picked stays picked while the stop lasts")
tr.watch(view("running", file="j2.nc", elapsed=600, tool=2), "Aluminum")
tr.watch(view("toolchange", file="j2.nc", elapsed=600, tc={"tool": 3, "label": VBIT}), "Aluminum")
check(life.d["bits"]["b1"]["secs"]["aluminum"] == 1500.0, "the picked bit (A) got the time")
tr.watch(view("running", file="j2.nc", elapsed=700, tool=3, label=VBIT), "Aluminum")
tr.watch(view("toolchange", file="j2.nc", elapsed=700, tc={"tool": 2}), "Aluminum")
check(life.d["slots"]["2"]["bit"] == "b1", "back to T2 in the same job: the same bit as before in it")
tr.watch(view("running", file="j2.nc", elapsed=760, tool=2), "Aluminum")
tr.watch(view("idle"), "Aluminum")
check(life.d["bits"]["b1"]["jobs"] == 2, "and one more job for A, not two")
tr.watch(view("running", file="j3.nc", elapsed=10, tool=2), "Plywood")
tr.watch(view("running", file="j3.nc", elapsed=130, tool=2), "Plywood")
tr.watch(view("alarm"), "Plywood")
check(life.d["bits"]["b1"]["secs"].get("plywood") == 120.0,
      "a job begun with the bit already in counts it too; an alarm ends the count; kept per material")
tr.watch(view("running", file="j4.nc", elapsed=10, tool=7, label=""), "")
tr.watch(view("running", file="j4.nc", elapsed=70, tool=7, label=""), "")
tr.watch(view("idle"), "")
check(any(k["label"] == "T7 bit" for k in life.d["kinds"].values()) and
      any(b2["secs"].get("unknown") == 60.0 for b2 in life.d["bits"].values()),
      "a bit the file doesn't describe: 'T7 bit', in material 'unknown'")

print("no expected life: no warnings")
st = life.status("b1", "Aluminum")
check(st["limit"] is None and st["state"] == "ok", "nothing set, nothing near")
check(life.short_text(st) == "Bit A · 26 min used", "the stop screen line: %r" % life.short_text(st))
check(life.plan({2: (FLAT, 99999)}, "Aluminum") == [], "and no warning before a job, however long")

print("with an expected life")
life.set_limit(kind, "Aluminum", 0.5)
st = life.status("b1", "aluminum")
check(st["limit"] == 1800 and st["state"] == "near", "past 80 %%: near (%s of %s)" % (st["used"], st["limit"]))
check(life.short_text(st) == "Bit A · 26 min of 30 min used", "%r" % life.short_text(st))
life.set_limit(kind, "Aluminum", 0.4)
st = life.status("b1", "Aluminum")
check(st["state"] == "over" and life.short_text(st).endswith("past its life"), "past it: over")
check(life.status("b1", "Plywood")["limit"] is None, "the life is set per material")
check(life.plan({2: (FLAT, 600)}, "Aluminum") == [], "a job warns only if even the least used bit (B) would pass it")
w = life.plan({2: (FLAT, 1500)}, "Aluminum")
check(len(w) == 1 and w[0]["label"] == "B" and "this job adds about 25 min" in life.plan_text(w[0]),
      "else it names the kind and the bit: %s" % life.plan_text(w[0]))
for bad in (-1, 0, "x", 5000):
    try:
        life.set_limit(kind, "Aluminum", bad)
        check(False, "accepted %r" % bad)
    except ValueError:
        pass
check(True, "an expected life must be a number of hours")
life.set_limit(kind, "Aluminum", "")
check(life.status("b1", "Aluminum")["limit"] is None, "and can be taken away again (left blank)")

print("bits out of use")
try:
    life.retire("b1", "lost")
    check(False, "retired with no reason")
except ValueError:
    check(True, "a bit is retired with a reason")
life.retire("b1", "worn", "dull edge")
check(life.d["bits"]["b1"]["retired"] and life.suggest(kind) == b, "a retired bit is never suggested")
check(life.add_bit(kind) and sorted(x["label"] for x in life._active(kind)) == ["A", "B"],
      "a new bit takes the free letter")
info = life.info()
k = next(x for x in info["kinds"] if x["key"] == kind)
check(len(k["history"]) == 1 and k["lives"]["aluminum"]["bits"] == 1 and k["lives"]["aluminum"]["suggested_hours"] is None,
      "the history keeps how long it lasted; no suggested life from one bit")
life.d["bits"][b]["secs"]["aluminum"] = 3600.0
life.retire(b, "worn")
k = next(x for x in life.info()["kinds"] if x["key"] == kind)
check(k["lives"]["aluminum"]["suggested_hours"] == round(0.75 * 1560 / 3600.0, 1),
      "two worn bits: a suggested life, 3/4 of the shortest")
broke = life.add_bit(kind)
life.retire(broke, "broken_setup")
check(next(x for x in life.info()["kinds"] if x["key"] == kind)["lives"]["aluminum"]["bits"] == 2,
      "a bit that broke from a setup problem doesn't count as worn out")

print("kept on the Pi")
again = bitlife.BitLife(state)
check(again.d == life.d, "read back as saved (%s)" % bitlife.FILE_NAME)
with open(os.path.join(state, bitlife.FILE_NAME), "w") as fh:
    fh.write("{not json")
check(bitlife.BitLife(state).d["bits"] == {} and any(f.startswith(bitlife.FILE_NAME + ".bad-") for f in os.listdir(state)),
      "a broken file is kept aside (not written over), nothing crashes")
check(bitlife.hm(45 * 60) == "45 min" and bitlife.hm(7800) == "2 h 10" and bitlife.hm(10800) == "3 h", "times read short")

print("the hub and the phone")
cfg = copy.deepcopy(config.DEFAULTS)
cfg["web_port"] = 47691
hub = Hub(cfg, JobStore(tempfile.mkdtemp(), []))
v = view("toolchange", elapsed=0, tc={"tool": 2, "code": "T2", "label": FLAT, "then": {"in": 1200}})
hub._bits(v)
check(v["toolchange"]["bit"]["label"] == "A" and v["toolchange"]["bit"]["text"] == "Bit A · 0 min used",
      "the stop's screen data names the bit to put in")
check(v["toolchange"]["bit"]["job_adds"] == 1200, "and how long it cuts until the next stop")
msg = notify.stop_message(v)
check("Bit A · 0 min used" in msg["message"], "the stop notification names it too")
hub.bitlife.set_limit(bitlife.kind_key(FLAT), "unknown", 0.3)
v = view("toolchange", elapsed=0, tc={"tool": 2, "code": "T2", "label": FLAT, "then": {"in": 1200}})
hub._bits(v)
check(v["toolchange"]["bit"]["state"] == "near", "a bit this stretch would take past its life: amber already")
hub.bit_tracker.watch(view("toolchange", file="r.nc", elapsed=0, tc={"tool": 2, "label": FLAT}), "")
rv2 = view("running", file="r.nc", elapsed=300, tool=2)
rv2["job"]["next"] = {"tool": 3, "code": "T3", "label": VBIT, "in": 600}
hub._bits(rv2)
check(rv2["job"]["now"]["bit"]["used"] - hub.bitlife.status(rv2["job"]["now"]["bit"]["id"], "")["used"] == 300,
      "while it cuts, the running screen's time includes this stretch so far")
check(rv2["job"]["next"]["bit"]["label"] == "A" and rv2["job"]["next"]["bit"]["kind_label"] == VBIT,
      "and names the bit the next stop will ask for")
hub.bit_tracker.watch(view("idle"), "")
probe = view("toolchange", elapsed=0, tc={"tool": 0, "code": "Probe", "label": ""})
hub._bits(probe)
check("bit" not in probe["toolchange"], "no bit line for the probe")
import samples
from z1screen import gcode
sign = gcode.analyze(samples.sign(), "/sd/gcodes/samples/sample-sign.nc")
needs = hub.bit_needs(gcode.Profile(sign))
used = [t for t in sign["used_tools"] if t != 0]
check(sorted(needs) == sorted(used) and all(n[1] > 0 for n in needs.values()),
      "how long each bit of a job cuts (the probe left out): %s" % {k: round(n[1]) for k, n in needs.items()})
check(hub.bit_warnings(sign) == [], "no expected life for its bits: nothing to warn about before the start")
k1 = hub.bitlife.ensure_kind(needs[used[0]][0])
hub.bitlife.set_limit(k1, sign.get("material") or "unknown", 0.1)
w = hub.bit_warnings(sign)
check(len(w) == 1 and w[0].startswith("This job takes T%d past its expected life" % used[0]),
      "one set, and the job would take it past: named before the start (%s)" % (w[:1],))
m = notify.bits_message(["T2 flat: bit A has 2 h of 3 h used; this job adds about 1 h 10."], "Part")
check(m["title"].startswith("Z1: bits") and "Job: Part" in m["message"], "a warning before a job, for the phone")


async def http(method, path, body=None):
    r, w = await asyncio.open_connection("127.0.0.1", 47691)
    data = b"" if body is None else json.dumps(body).encode()
    head = "%s %s HTTP/1.1\r\nHost: z1screen.local\r\n" % (method, path)
    if method == "POST":
        head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(data)
    w.write((head + "\r\n").encode() + data)
    await w.drain()
    raw = await r.read()
    w.close()
    h, _, payload = raw.partition(b"\r\n\r\n")
    return int(h.split()[1]), json.loads(payload)


async def api_checks():
    task = asyncio.create_task(Web(hub, 47691).serve())
    await asyncio.sleep(0.3)
    try:
        code, info = await http("GET", "/api/bitlife")
        k = info["kinds"][0]
        check(code == 200 and k["label"] == FLAT and [x["label"] for x in k["bits"]] == ["A"], "the settings page reads it")
        code, info = await http("POST", "/api/bitlife", {"action": "add", "kind": k["key"]})
        check(code == 200 and [x["label"] for x in info["kinds"][0]["bits"]] == ["A", "B"], "adds a bit")
        code, info = await http("POST", "/api/bitlife", {"action": "next", "slot": 2})
        check(code == 200 and info["slots"]["2"]["label"] == "B", "the screen's tap: the next bit")
        code, info = await http("POST", "/api/bitlife", {"action": "limit", "kind": k["key"], "material": "Bronze",
                                                         "hours": 2.5})
        check(info["kinds"][0]["limits"] == {"unknown": 0.3, "bronze": 2.5}, "sets an expected life")
        code, err = await http("POST", "/api/bitlife", {"action": "retire", "bit": "b1", "reason": ""})
        check(code == 400 and "error" in err, "says what's wrong")
    finally:
        task.cancel()

asyncio.run(api_checks())

print("on the small screen")
try:
    from z1screen import tft
    have_pil = tft.Image is not None
except Exception:
    have_pil = False
if not have_pil:
    print("  skipped - the small screen needs Pillow")
else:
    fonts = tft.Fonts()
    base = {"mode": "toolchange", "job": {"name": "Part", "known": True}, "toolchange": {
        "tool": 2, "code": "T2", "label": FLAT, "color": "#3A7BD5", "text": "#FFFFFF", "sticker": "Blue",
        "index": 2, "total": 4, "then": {"tool": 3, "code": "T3", "label": VBIT, "in": 900}}}

    def lay(bit):
        v = copy.deepcopy(base)
        if bit is not None:
            v["toolchange"]["bit"] = bit
        p = tft.Layout(fonts)
        p.build(v)
        return p, [o["t"] for o in p.ops if o["o"] == "t"]

    p, texts = lay({"label": "B", "text": "Bit B · 2 h 10 used", "state": "ok", "choices": 2})
    check("Bit B · 2 h 10 used" in texts and "TAP: ANOTHER" in texts, "the stop screen names the bit")
    check("Put bit B in," in " ".join(texts), "and says to put bit B in")
    check([h[4] for h in p.hits] == [("next_bit", 2)], "a tap on that line picks another (the only touch target)")
    shot = tft.paint(p.ops, fonts)
    check(shot.size == (tft.W, tft.H), "painted")
    p1, texts1 = lay({"label": "A", "text": "Bit A · 5 min used", "state": "ok", "choices": 1})
    check("TAP: ANOTHER" not in texts1 and not p1.hits, "only one bit of the kind: nothing to tap")
    gw = tft.W - 36

    def fills(p):
        return [o for o in p.ops if o["o"] == "r" and o.get("h") == 9 and o.get("c") != "#4A4640"]
    check(not fills(p), "no expected life set: the gauge is empty")
    pg, _ = lay({"label": "A", "text": "Bit A · 18 min of 3 h used", "used": 1080, "limit": 10800, "job_adds": 900,
                 "state": "ok", "choices": 2})
    f = fills(pg)
    check(f[-1]["c"] == tft.LIFE_GREEN and abs(f[-1]["w"] - 0.1 * gw) <= 1,
          "a tenth used: a tenth of the gauge filled, green")
    check(len(f) == 2 and abs(f[0]["w"] - (1080 + 900) / 10800.0 * gw) <= 1, "and a lighter part for this stretch")
    pn, _ = lay({"label": "A", "text": "Bit A · 2 h 35 of 3 h used", "used": 9300, "limit": 10800, "state": "near",
                 "choices": 2})
    c = fills(pn)[-1]["c"]
    check(c not in (tft.LIFE_GREEN, tft.HOLD, tft.STOP) and c == tft.life_color(9300 / 10800.0),
          "86 %%: between yellow and red (%s)" % c)
    po, _ = lay({"label": "A", "text": "Bit A · 3 h 10 of 3 h used: past its life", "used": 11400, "limit": 10800,
                 "state": "over", "choices": 2})
    check(fills(po)[-1]["c"] == tft.STOP and fills(po)[-1]["w"] == gw, "past it: full and red")
    check(tft.life_color(0.2) == tft.LIFE_GREEN and tft.life_color(0.8) == tft.HOLD and tft.life_color(1.2) == tft.STOP,
          "green, yellow at 80 %, red at 100 %")
    p0, texts0 = lay(None)
    check(not p0.hits and any("this bit" in t for t in texts0), "no bit data: the screen as before")
    pr = tft.Layout(fonts)
    pr.build(dict(base, toolchange={"tool": 0, "code": "Probe", "label": "",
                                    "bit": {"label": "A", "text": "x", "choices": 3}}))
    check(not pr.hits, "never a touch target on a probe stop")

    seg = {"tool": 2, "code": "T2", "start": 0, "end": 1, "color": "#3A7BD5", "unset": False}
    run = {"mode": "running", "job": {"name": "Part", "file": "p.nc", "elapsed": 900, "known": True, "percent": 40,
           "left": 1300, "changes_total": 4, "changes_left": 2, "segments": [seg],
           "now": {"tool": 2, "code": "T2", "label": FLAT, "color": "#3A7BD5", "text": "#FFFFFF", "sticker": "Blue",
                   "bit": {"label": "A", "used": 8100, "used_text": "2 h 15", "limit": 10800, "job_adds": 420}},
           "next": {"tool": 3, "code": "T3", "label": VBIT, "color": "#2E9E5B", "text": "#FFFFFF", "sticker": "Green",
                    "in": 420, "index": 3, "total": 4,
                    "bit": {"label": "B", "used": 3900, "limit": 10800, "text": "Bit B · 1 h 05 of 3 h used"}}}}
    pr = tft.Layout(fonts)
    pr.build(run)
    texts = [o["t"] for o in pr.ops if o["o"] == "t"]
    check("BIT A" in texts and "Bit A · 2 h 15 of 3 h used" in texts,
          "the running screen's big card: the bit cutting now and its time")
    check(any(t.endswith("· bit B") for t in texts), "the strip names the bit the next stop will ask for")
    check(not any("sticker" in t.lower() for t in texts), "no sticker words on the colored card")
    gauges = [o for o in pr.ops if o["o"] == "r" and o.get("c") == tft.GAUGE_TRACK]
    check(len(gauges) == 1 and gauges[0]["w"] > 250, "a wide life gauge for the bit cutting now")
    plain = copy.deepcopy(run)
    plain["job"]["now"]["bit"] = {"label": "A", "used": 7800, "used_text": "2 h 10", "limit": None}
    plain["job"]["next"]["bit"] = {"label": "B", "used": 3900, "limit": None, "text": "Bit B · 1 h 05 used"}
    pp = tft.Layout(fonts)
    pp.build(plain)
    texts = [o["t"] for o in pp.ops if o["o"] == "t"]
    check("Bit A · 2 h 10 used" in texts and
          not any(o["o"] == "r" and o.get("c") == tft.GAUGE_TRACK for o in pp.ops),
          "no expected life set: the hours, no gauge")

    rv = tft.Layout(fonts)
    warn = "This job takes T2 past its expected life: bit B has 2 h 50 of 3 h used, the job adds about 40 min."
    rv._ui_review({"screen": "review", "title": "Part", "lines": ["Probe first, then T1", "Run time about 1 h"],
                   "why": "", "note": "", "back": ("back_check",), "bit_warnings": [warn]})
    words = " ".join(o["t"] for o in rv.ops if o["o"] == "t" and o.get("c") == tft.HOLD)
    check("past its expected life" in words, "the review screen before a start warns about it, in amber")

    from z1screen import touch
    posted = []

    class FakeApi:
        def post(self, path, data, timeout=20):
            posted.append((path, data))
            return {"ok": True}
    panel = touch.Panel(FakeApi())
    panel.do(("next_bit", 2))
    panel.work["thread"].join(2)
    panel.tick(0)
    check(posted == [("/api/bitlife", {"action": "next", "slot": 2})] and panel.ui is None,
          "the tap asks the Pi's service for the next bit, and opens no screen")

page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "z1screen", "web", "index.html"),
            encoding="utf-8").read()
check('id="chgBitUsed"' in page and "function lifeColor" in page, "the phone page's stop shows the same gauge")
check('id="cutGauge"' in page and 'class="strip" id="next"' in page and "Cutting now" in page,
      "the phone page's running view: the bit cutting now with its gauge, the next bit as a strip")

print("all bit life checks passed")
