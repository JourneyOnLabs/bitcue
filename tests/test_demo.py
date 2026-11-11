"""Checks for bit colors and the demo that need no network."""
import copy
import json, os, tempfile
from common import DISC, SIGN, TAG, check, make_library
from z1screen import bits, config, demo
from z1screen.hub import Hub, UNSET_COLOR
from z1screen.jobs import JobStore


def raises(fn, text):
    try:
        fn()
    except ValueError as exc:
        return text.lower() in str(exc).lower()
    return False


LIB = make_library([SIGN, TAG] + DISC)
state = tempfile.mkdtemp()
store = JobStore(state, [LIB])
cfg = copy.deepcopy(config.DEFAULTS)

print("bit table checks")
good = bits.clean({"3": {"name": " 30°  V-bit ", "color": "#1565c0", "sticker": "Blue"}, 12: {"color": "#7B1FA2"}})
check(good == {"3": {"name": "30° V-bit", "color": "#1565C0", "sticker": "Blue"},
               "12": {"name": "", "color": "#7B1FA2", "sticker": ""}}, "names tidied, colors upper case, numbers as text")
check(raises(lambda: bits.clean({"3": {"color": "blue"}}), "#1565C0"), "a color that is not #RRGGBB is refused")
check(raises(lambda: bits.clean({"x": {"color": "#FFFFFF"}}), "not a bit number"), "a bit number that is not a number is refused")
check(raises(lambda: bits.clean({"10000": {"color": "#FFFFFF"}}), "0 to 9999"), "bit numbers above 9999 are refused")
check(raises(lambda: bits.clean([1, 2]), "expected form"), "a list instead of a table is refused")
check(raises(lambda: bits.clean({str(i): {"color": "#FFFFFF"} for i in range(61)}), "at most"), "at most 60 bits")

print("saving from the settings page")
hub = Hub(copy.deepcopy(cfg), store)
check(not hub.tools_saved and hub.tool_info(3)["color"] == "#1565C0", "starts with the starting colors")
table = copy.deepcopy(hub.cfg["tools"])
table["12"] = {"name": "6 mm flat end mill", "color": "#7B1FA2", "sticker": "Purple"}
table["3"]["color"] = "#00B8D4"
hub.set_tools(table)
check(os.path.isfile(os.path.join(state, "bits.json")), "saved to bits.json in the state folder")
check(hub.tool_info(12)["color"] == "#7B1FA2" and hub.tool_info(12)["sticker"] == "Purple", "new bit T12 has its color")
check(hub.tool_info(3)["color"] == "#00B8D4", "changed color used straight away")
hub2 = Hub(copy.deepcopy(cfg), store)
check(hub2.tools_saved and hub2.tool_info(12)["color"] == "#7B1FA2", "saved colors come back after a restart")
try:
    hub2.set_tools({"3": {"color": "nope"}})
except ValueError:
    pass
check(hub2.tool_info(3)["color"] == "#00B8D4", "a refused save changes nothing")
hub2.reset_tools()
check(not os.path.isfile(os.path.join(state, "bits.json")) and hub2.tool_info(3)["color"] == "#1565C0",
      "going back to the starting colors deletes the saved table")
check(Hub(copy.deepcopy(cfg), store).tool_info(3)["color"] == "#1565C0", "and stays that way after a restart")
with open(os.path.join(state, "bits.json"), "w") as fh:
    fh.write("{not json")
check(Hub(copy.deepcopy(cfg), store).tool_info(1)["color"] == "#D32F2F", "a damaged bits.json is ignored")
os.remove(os.path.join(state, "bits.json"))

print("a bit with no color")
info = hub2.tool_info(42)
check(info["unset"] and info["color"] == UNSET_COLOR and info["sticker"] == "" and info["code"] == "T42",
      "T42 shows as no sticker color set")
check(not hub2.tool_info(2)["unset"], "T2 has a color")

print("bits seen in job files")
names = store.tool_names()
check(names["3"]["files"] == 6 and names["3"]["names"] == {"60° V-bit, 0.1 mm tip": 6},
      "T3 is in all 6 sample files, named by type and size: 60° V-bit, 0.1 mm tip")
check(names["1"]["names"] == {"3.175 × 12 mm flat end mill": 6} and names["2"]["names"] == {"2 × 8 mm ball end mill": 1},
      "the end mills by diameter and flute length; T2 only in the sign")
check(not any(w in n for t in names.values() for n in t["names"] for w in ("deg", "Flat end mill 3.175 x 12")),
      "names made from the tool lines' type and sizes, not the file's own names")

from z1screen import gcode
desc = gcode.describe_tool
check(desc({"type": "Flat End", "diameter": "6", "flutelength": "20", "name": "6*20mm Flat End(Wood)"})
      == "6 × 20 mm flat end mill", "any flat end mill: diameter and flute length")
check(desc({"type": "Engraving", "angle": "30", "tipdiameter": "0.1", "name": "30° × 0.1 mm V-bit (ACME TOOLS)"})
      == "30° V-bit, 0.1 mm tip", "a V-bit by its angle and tip, without the brand")
check(desc({"type": "Ball End", "diameter": "2", "flutelength": "6"}) == "2 × 6 mm ball end mill", "a ball end mill")
check(desc({"type": "Chamfer", "angle": "60"}) == "60° chamfer bit" and desc({"type": "Drill", "diameter": "1.2"})
      == "1.2 mm drill", "a chamfer bit, a drill")
check(desc({"type": "Some New Type", "name": "My odd cutter"}) == "My odd cutter",
      "a type the screen doesn't know keeps the file's name")
check(all(desc(f) for f in ({"type": "Flat End"}, {"type": "Engraving"}, {"diameter": "4"})),
      "a tool line with sizes missing still gets a name")
prof = store.find(SIGN)
check(prof.get("tool_specs", {}).get("3", {}).get("tipdiameter") == "0.1",
      "the profile keeps each tool line's type and sizes, so names can be made again without the file")
check("5" not in names, "T5 is in no file")
jobs = store.jobs()
check(len(jobs) == 6 and sorted(j["changes"] for j in jobs) == [2, 2, 2, 2, 3, 4], "6 jobs for the demo list")

print("the built-in sample job")
import build_sample  # noqa: E402
sample = demo.sample_profile()
check(sample is not None and sample["path"] == build_sample.SAMPLE_PATH, "z1screen/sample-job.json is there")
fresh = build_sample.build()
check(json.dumps(fresh, sort_keys=True) == json.dumps(json.load(open(demo.SAMPLE_FILE)), sort_keys=True),
      "and up to date with tools/samples.py and the time model (else run tools/build_sample.py)")
hd = Hub(copy.deepcopy(cfg), JobStore(tempfile.mkdtemp(), []))
dj = hd.demo_jobs()
check(dj and dj[0]["path"] == demo.SAMPLE and dj[0]["changes"] == 4 and hd._demo_profile("")["path"] == build_sample.SAMPLE_PATH,
      "a Pi that knows no job files yet can still play the demo (the sample job is first in the list)")
check(len(Hub(copy.deepcopy(cfg), store).demo_jobs()) == 7, "with job files known: the sample, then the 6 files")

print("demo recording of the sample job")
prof = demo.sample_profile()
t_change = {i: c["t"] for i, c in enumerate(prof["changes"])}
tl = demo.build_timeline(copy.deepcopy(cfg), prof, "Makera_Z1", "http://z1screen.local:8080/settings")
fr = tl["frames"]
stops = [f["tool"] for f in fr if f["k"] == "stop"]
check(stops == [0, 1, 2, 3, 1], "stops: probe, T1, T2, T3, T1")
check(fr[0]["k"] == "ready" and fr[0]["view"]["mode"] == "idle" and fr[-1]["k"] == "done"
      and fr[-1]["view"]["last_job"]["done"], "starts on Ready and ends on the finished job")
check(all(f["view"]["mode"] == "toolchange" for f in fr if f["k"] == "stop"), "every stop shows the tool-change screen")
idx = [f["view"]["toolchange"].get("index") for f in fr if f["k"] == "stop"]
check(idx == [None, 1, 2, 3, 4], "tool change numbers 1 to 4 (the probe is not counted)")
problems = []
seg = []
for f in fr:
    if f["k"] == "stop":
        seg = []
        continue
    if f["k"] != "run":
        continue
    j = f["view"]["job"]
    if f["est"] > 110 and abs(j["elapsed"] + j["left"] - tl["total"]) > 30:
        problems.append(("left", f["est"]))
    nx = j["next"]
    if nx:
        if seg and (nx["index"] != seg[-1]["index"] or nx["in"] > seg[-1]["in"] + 1):
            problems.append(("next", f["est"], nx["code"], round(nx["in"])))
        seg.append(nx)
check(not problems, "time left adds up and the next bit counts down between stops (%s)" % problems[:3])
after = [f for f in fr if f["k"] == "run" and t_change[1] < f["est"] < t_change[1] + 200]
check(after and all(f["view"]["job"]["next"]["code"] == "T3" for f in after), "right after the T2 stop the next bit is T3")
check(tl["extras"]["probe"]["alarm"].startswith("Probe not detected") and tl["extras"]["hand"]["alarm"] == "Stopped by hand",
      "what-if screens: probe not detected, stopped by hand")
check(len(json.dumps(tl)) < 900000, "recording size %d bytes" % len(json.dumps(tl)))

print("demo player")
p = demo.DemoPlayer(tl, speed=60, now=1000.0)
p.tick(1001.0)
check(p.frames[p.i]["k"] == "ready" and not p.waiting, "Ready for the first seconds")
p.tick(1003.5)
check(p.waiting and p.status(1003.5)["tool"]["tool"] == 0, "then waits for the probe")
p.tick(1100.0)
check(p.waiting and p.i == 1, "keeps waiting until the front button")
check(p.press(1100.0) and not p.waiting, "front button carries on")
check(not p.press(1100.1), "front button does nothing while cutting")
p.tick(1102.0)
check(p.waiting and p.status(1102.0)["tool"]["code"] == "T1", "after 2 s at 60x it stops for T1")
c0 = p.clock
p.tick(1104.0)
check(abs(p.clock - c0 - 2.0) < 0.01, "at a stop the screen's time of day runs at real speed")
p.press(1104.0)
c0 = p.clock
p.tick(1105.0)
v = p.view(1105.0)
check(v["demo"]["kind"] == "demo" and v["mode"] == "running" and v["job"]["next"]["code"] == "T2", "running toward T2")
check(abs(v["clock"] - c0 - 60) < 0.2, "while cutting the screen's time of day runs at the demo speed")
p.skip(1105.5)
check(not p.waiting and p.tl["frames"][p.i]["est"] >= t_change[1] - 90 - 61, "skip jumps to just before the next stop")
p.tick(1107.0)
check(p.waiting and p.status(1107.0)["tool"]["code"] == "T2", "and reaches the T2 stop")
for code_ in ("T2", "T3", "T1"):
    p.press(p.last)
    for _ in range(400):
        p.tick(p.last + 1.0)
        if p.waiting or p.done_at:
            break
check(p.done_at is not None and p.view(p.last)["last_job"]["done"], "plays to the end and shows the finished job")
check(p.finished(p.done_at + 31) == "done", "ends 30 s after the job finishes")
q = demo.DemoPlayer(tl, now=0.0)
check(q.finished(30 * 60 + 1) == "time", "a demo always ends after 30 minutes")
check(demo.DemoPlayer(tl, speed=5).speed == 60.0 and demo.DemoPlayer(tl, speed="360").speed == 360.0,
      "only the offered speeds are used")

print("color check")
ck = demo.build_check(copy.deepcopy(cfg), 12, {"name": "6 mm flat end mill", "color": "#7B1FA2", "sticker": "Purple"},
                      "Makera_Z1")
v = ck["frames"][0]["view"]
check(v["mode"] == "toolchange" and v["toolchange"]["color"] == "#7B1FA2" and v["toolchange"]["code"] == "T12"
      and v["job"]["name"] == "Color check", "shows T12 in purple, named Color check")
c = demo.DemoPlayer(ck, kind="check", now=0.0)
check(c.finished(19) == "" and c.finished(21) == "time", "a color check lasts 20 seconds")
print("all demo checks passed")
