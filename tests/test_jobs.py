"""Small checks for placing and starting jobs from the Pi: run window, start commands, listings,
touch readings, cut areas, placement checks and a plate layout."""
from __future__ import annotations
import json, os, struct
from common import ANCHOR, DISC, SHEET, TAG, check, make_library, profile
from z1screen import gcode, start, plates, touch, platemap as pm  # noqa: E402
from z1screen.jobs_api import _parse_listing, _ls_dir_of  # noqa: E402


print("run window from the file header")
r = gcode.parse_run_window("work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X5 Y5; "
                           "Scan Margin on; Auto Leveling on, 5 x 5, 2 mm.")
check(r == {"anchor": 1, "offset": [0.0, 0.0], "probe": {"rel": "work", "x": 5.0, "y": 5.0}, "margin": True,
            "level": {"i": 5, "j": 5, "h": 2.0}}, "probe X5 Y5, edge scan, leveling 5 x 5 at 2 mm")
r = gcode.parse_run_window("work origin Anchor 1, offsets 0; Z probe relative to Work Origin at X75 Y6; "
                           "Scan Margin on; Auto Leveling off.")
check(r["probe"] == {"rel": "work", "x": 75.0, "y": 6.0} and r["level"] is None, "probe X75 Y6, no leveling")
r = gcode.parse_run_window("work origin Anchor 1, offset X12.5 Y-3; Z probe off; Scan Margin off; Auto Leveling off.")
check(r["offset"] == [12.5, -3.0] and r["probe"] is None, "offsets given as X and Y, Z probe off")
notes = profile(TAG)["notes"]
check(notes["plate_note_dims"] == [200.0, 150.0, 6.0] and notes["plate_note_material"] == "Plywood"
      and notes["run"]["probe"] == {"rel": "work", "x": 104.0, "y": 14.0}, "the sample tag's plate note and run window")
check(gcode._header_notes([b"; Baltic birch plywood sheet 300 x 200 x 6.5 mm, on the spoilboard"])["plate_note_material"]
      == "Baltic birch plywood", "a plate note: material of several words, x or \u00d7")
check(gcode.parse_run_window("work origin Anchor 1, offsets 0; Scan Margin on; Auto Leveling off.") is None,
      "a run window with a part missing is not used")
check(gcode.parse_run_window("work origin Anchor 2, offsets 0; Z probe off; Scan Margin off; Auto Leveling off.")
      ["anchor"] == 2, "another anchor is read as it is (and refused at start)")

print("start commands")
box = [7.674, 82.674, 67.526, 142.526]
run = {"anchor": 1, "offset": [0, 0], "probe": {"rel": "work", "x": 5, "y": 5}, "margin": True,
       "level": {"i": 5, "j": 5, "h": 2}}
check(start.m495_command(box, run) == "M495 X7.674Y82.674C67.526D142.526O-2.674F-77.674A59.852B59.852I5J5H2",
      "M495 in Makera Studio's form: box, edge scan, probe from the box corner, leveling size and points")
check(start.m495_command(box, {"margin": False, "probe": None, "level": None}) == "M495 X7.674Y82.674P1",
      "nothing on: go to the start point first (P1)")
check(start.origin_command(ANCHOR, (0, 0)) == "G10L2P0X-200.000Y-200.000", "work origin at Anchor 1")
check(start.origin_command(ANCHOR, (10, -5)) == "G10L2P0X-190.000Y-205.000", "with offsets")
check(start.play_command("/sd/gcodes/my job?.nc") == "play /sd/gcodes/my\x01job\x02.nc", "spaces and ? coded")
prof = {"path": "/sd/gcodes/a/b.nc", "box": box, "notes": {}}
try:
    start.commands(prof, (0, 0))
    check(False, "a file without a run window must be refused")
except ValueError as exc:
    check("run settings" in str(exc), "a file without a run window is refused: " + str(exc))
check(start.design_key(DISC[3]) == "disc" and start.design_name("disc") == "Disc", "design name from the file name")
check(plates.where(30, 30, 200, 150) == "front left" and plates.where(150, 110, 200, 150) == "back right"
      and plates.where(100, 75, 200, 150) == "middle", "spot names")

print("folder listings")
text = "sample-sheet/ 0 20261003140558\r\nmy\x01job.nc 123456 20261003140558\r\n.hidden 3 0\r\n"
ls = _parse_listing(text)
check(ls == [{"name": "sample-sheet", "dir": True, "size": 0, "date": "20261003140558"},
             {"name": "my job.nc", "dir": False, "size": 123456, "date": "20261003140558"}],
      "names, folders and sizes; hidden files left out")
check(_ls_dir_of("ls -e -s /sd/gcodes/my\x01folder") == "/sd/gcodes/my folder" and _ls_dir_of("play x") == "",
      "the folder of an ls command")

print("touch readings")
fmt = touch.EventParser.FMT


def ev(t, c, v):
    return struct.pack(fmt, 1, 0, t, c, v)


p = touch.EventParser()
out = p.feed(ev(3, 0, 1200) + ev(3, 1, 3000) + ev(1, 0x14A, 1) + ev(0, 0, 0))
out += p.feed(ev(3, 0, 1210) + ev(0, 0, 0))
out += p.feed(ev(1, 0x14A, 0) + ev(0, 0, 0))
check([o[0] for o in out] == ["down", "move", "up"] and out[0][1:3] == (1200, 3000) and out[1][1] == 1210,
      "down, move and up from raw events")
p = touch.EventParser()
data = ev(3, 0, 100) + ev(3, 1, 200) + ev(3, 0x18, 40) + ev(0, 0, 0) + ev(3, 0x18, 0) + ev(0, 0, 0)
out = p.feed(data[:10]) + p.feed(data[10:])
check([o[0] for o in out] == ["down", "up"], "a screen that reports pressure only, read in pieces")
raw = [(3900 - y * 14 + 3, 250 + x * 11 - 2) for x, y in touch.CAL_POINTS]
cal = touch.calibrate(raw)
x, y = touch.apply(cal, 3900 - 120 * 14, 250 + 160 * 11, False)
check(abs(x - 160) < 2 and abs(y - 120) < 2, "the middle of the screen maps to 160, 120 (%.1f, %.1f)" % (x, y))
try:
    touch.calibrate([(1, 1), (2, 2), (3, 3), (4, 4)])
    check(False, "dots in a line must be refused")
except ValueError:
    check(True, "touches that don't line up are refused")

print("cut areas as 0.5 mm grids")
G = pm.Grid
a = G.rect(10, 10, 20, 15)
check(a.bbox_mm() == [10.0, 10.0, 20.0, 15.0] and a.count() == 20 * 10, "a 10 x 5 mm rectangle is 20 x 10 cells")
check(G.from_json(a.to_json()).bbox_mm() == a.bbox_mm(), "saved and read back")
b = G.rect(21, 10, 30, 15)
check(not a.overlaps(b) and not a.dilate(2).overlaps(b) and a.dilate(3).overlaps(b),
      "1 mm apart: 2 bare cells between them, so widening one by 2 cells doesn't reach the other")
check(a.union(b).count() == a.count() + b.count() and not a.both(b), "union and common cells")
st = pm.stamp(a, 0.25, 0)
check(st.bbox_mm() == [10.0, 10.0, 20.5, 15.0], "a move of 0.25 mm covers both neighboring cells")
ras = pm.Raster()
arc = pm.arc_center(10, 0, 10, 0, -10, 0, None, True)
ras.arc(*arc)
fb = pm.footprint({1: ras}, {1: 1.5875}, 1.5875).bbox_mm()
check(fb[0] <= -11.5 and fb[2] >= 11.5 and fb[0] > -13 and fb[3] < 13,
      "a full G2 circle of radius 10 with a 3.175 mm bit covers about 11.6 mm around its center: %s" % fb)
c = pm.arc_center(0, 0, 10, 10, None, None, 10, True)
check(abs(c[0] - 10) < 1e-6 and abs(c[1]) < 1e-6 and abs(c[2] - 10) < 1e-6, "G2 with R10: center at X10 Y0")

print("placement checks")
plate = pm.Plate(150, 150, G.rect(0, 0, 50, 50))
part = G.rect(60, 10, 80, 30)
pb = [61.5, 11.5, 78.5, 28.5]
check(plate.check(part, 0, 0, None, None, pb)["ok"], "clear 10 mm from a cut")
check(plate.check(part, -21, 0, None, None, pb)["code"] == "overlap", "moved 10.5 mm left: overlaps")
check(plate.check(part, -19, 0, None, None, pb)["code"] == "close", "moved 9.5 mm left: closer than 1 mm")
check(plate.check(part, 0, 0, [70, 20], None, pb)["ok"] and plate.check(part, 0, 0, [40, 20], None, pb)["code"] == "probe",
      "Z probe spot on bare plate / on a cut")
check(plate.check(part, 0, 0, [70, 2], None, pb)["code"] == "probe", "Z probe spot under 3 mm from the edge")
check(plate.check(part, 0, 0, None, [45, 10, 80, 30], pb)["code"] == "level", "leveling box over a cut")
check(plate.check(part, 150, 0, None, None, pb)["code"] == "off", "off the plate")
near = plate.check(part, -12, 0, None, None, pb)["near"]
check(near is not None and 3.5 <= near <= 4.5, "nearest cut measured: %s mm" % near)
at = plate.auto_place(G.rect(0, 0, 40, 40), None, None, [1.5, 1.5, 38.5, 38.5])
check(at is not None and plate.check(G.rect(0, 0, 40, 40), at[0], at[1], None, None, [1.5, 1.5, 38.5, 38.5])["ok"],
      "the first clear place for a 40 mm part is found: X%s Y%s" % (at[0] * 0.5, at[1] * 0.5))
check(plate.auto_place(G.rect(0, 0, 140, 140), None, None, [1, 1, 139, 139]) is None, "no room for a 140 mm part")
geom = pm.fit(150, 150, pm.PLACE_BOX)
runs = pm.screen_runs(G.rect(0, 0, 75, 150), geom)
area = sum(w * h for x, y, w, h in runs)
check(abs(area - 95 * 190) < 400, "half the plate is drawn as about half the plate's pixels (%d)" % area)

print("job footprints, run settings and start commands")
src = (";@MKR|BEGIN\n;@MKR|TOOL|number=1|type=Flat End|diameter=2|flutelength=8\n;@MKR|END\nG21\nG90\nM6 T1\n"
       "G0 Z5\nG0 X0 Y0\nG1 Z-1 F100\nG1 X20 F300\nG1 Y10\nG0 Z5\nG0 X100 Y100\nM30\n").encode()
prof = gcode.analyze(src, "/sd/gcodes/parts/plate.nc")
check(prof["path_box"] == [0.0, 0.0, 20.0, 10.0] and prof["box"] == [0.0, 0.0, 100.0, 100.0],
      "the bit's cutting path is 20 x 10 mm; the rapid move to X100 Y100 isn't part of it")
fb = G.from_json(prof["cut"]).bbox_mm()
check(fb == [-1.0, -1.0, 21.5, 11.5], "footprint widened by the 2 mm bit's radius (to the next half mm): %s" % fb)
run, src_ = start.run_for(prof)
check(src_ == "default" and run["probe"] == {"rel": "work", "x": 10.0, "y": 5.0} and run["margin"] and not run["level"],
      "no run window: Z probe in the middle of the cutting path, edge scan on, leveling off")
cmds = start.commands(prof, ANCHOR, run, (12.5, 40))
check(cmds[0] == "G10L2P0X-187.500Y-160.000" and cmds[1] == "buffer M495 X0Y0C100D100O10F5",
      "moved X12.5 Y40: the work origin moves, the box and probe stay in work coordinates: %s" % cmds[:2])
check(start.run_lines(run, (12.5, 40))[:2] == ["Origin: Anchor 1, offset X12.5 Y40", "Z probe: X22.5 Y45 on the plate"],
      "the review screen's lines")
from z1screen.jobs_api import anchor_from_config, _offset  # noqa: E402
import samples  # noqa: E402
check(anchor_from_config(samples.config_txt(-200.5, -195.25).decode()) == [-200.5, -195.25]
      and anchor_from_config("x 1\n") is None, "Anchor 1 from the Z1's config.txt")
check(_offset([12.5, -3]) == (12.5, -3.0) and _offset(None) == (0.0, 0.0), "moves in 0.5 mm steps")
try:
    _offset([0.3, 0])
    check(False, "a 0.3 mm move must be refused")
except ValueError:
    check(True, "a move that isn't a 0.5 mm step is refused")

print("a plate layout: the sample sheet of 4 discs")
sheet = [profile(p) for p in DISC]
foot = [G.from_json(p["cut"]) for p in sheet]
check(foot[0].overlaps(foot[1]) and foot[0].overlaps(foot[2]) and not foot[0].overlaps(foot[3]),
      "neighbouring discs' cut-outs run into each other (planned), the diagonal ones don't")
check(all(p["path_box"][0] >= 0 and p["path_box"][1] >= 0 and p["path_box"][2] <= 200 and p["path_box"][3] <= 150
          for p in sheet), "every part's bit path stays on the 200 x 150 mm plate")
lay = plates.layouts(sheet)[SHEET]
check(sorted(lay["spots"]) == ["30,30", "30,75", "75,30", "75,75"] and lay["designs"] == {"disc": "Disc"}
      and lay["plate"]["w"] == 200 and lay["plate"]["material"] == "Plywood",
      "4 spots from the files' notes, one design, the plate from the plate note")
check(plates.layouts(sheet, folders=["/sd/gcodes/other"]) == {}, "only folders named in the settings are layouts")

print("a job that stopped part way")
import copy, tempfile  # noqa: E402
sq = G.rect(10, 10, 30, 30).to_json()
pl = plates.Plates(tempfile.mkdtemp())
a = pl.add("A", "/sd/gcodes/f/a.nc", sq, [0, 0], "started", "pi")
pl.add("B", "/sd/gcodes/f/b.nc", sq, [50, 0], "cut", "pi")
check(pl.stopped("/sd/gcodes/f/a.nc", [0, 0])["id"] == a, "a stopped run is found by its file and place")
check(pl.stopped("/sd/gcodes/f/a.nc", [0.5, 0]) is None and pl.stopped("/sd/gcodes/f/b.nc", [50, 0]) is None
      and pl.stopped("", [0, 0]) is None, "not at another place, not a finished job, not a job with no file")

print("a kit's starting plate (library/seed/plate.json)")
seed_path = os.path.join(make_library((TAG,), seed=True), "seed", "plate.json")
seed = json.load(open(seed_path))
tag_id = next(c["id"] for c in seed["cuts"] if c["name"] == "Sample tag")
check(next(c for c in seed["cuts"] if c["id"] == tag_id)["state"] == "started" and seed.get("seed"),
      "this kit's plate has the tag stopped part way")
old = copy.deepcopy(seed)
old.pop("seed")
for c in old["cuts"]:
    c["state"] = "cut"                                   # the earlier kit had the tag as cut
st = tempfile.mkdtemp()
json.dump(old, open(os.path.join(st, plates.FILE_NAME), "w"))
pl = plates.Plates(st, seed_path)
check(pl.data["seed"] == seed["seed"] and pl.get(tag_id)["state"] == "started",
      "an earlier kit's plate the Pi never changed is replaced by this kit's")
pl.update(tag_id, state="cut")
check(pl.data["seed"] == "" and plates.Plates(st, seed_path).get(tag_id)["state"] == "cut",
      "once the Pi changes the record, it is the Pi's own and a kit never replaces it")
old2 = copy.deepcopy(old)
old2["cuts"].append({"id": 9, "name": "badge", "file": "/sd/gcodes/b.nc", "state": "cut", "by": "pi",
                     "shift": [70, 0], "shape": sq})
st2 = tempfile.mkdtemp()
json.dump(old2, open(os.path.join(st2, plates.FILE_NAME), "w"))
check([c["id"] for c in plates.Plates(st2, seed_path).cuts] == [c["id"] for c in old2["cuts"]],
      "an earlier record with a job the Pi recorded is kept as it is")

print("NEW PLATE keeps the plate before it; LAST PLATE brings it back")
st3 = tempfile.mkdtemp()
pl = plates.Plates(st3, "", {"w": 200, "h": 150, "t": 6, "material": "Plywood"})
check(pl.previous_info() is None and not pl.restore_previous(), "nothing to bring back at first")
ids = [pl.add("A", "/sd/gcodes/f/a.nc", sq, [0, 0], "cut", "pi"), pl.add("B", "/sd/gcodes/f/b.nc", sq, [50, 0], "cut", "pi")]
pl.new_plate({"w": 300, "h": 200, "t": 6.35, "material": "MDF"})
pv = pl.previous_info()
check(pl.cuts == [] and pl.plate["w"] == 300 and pl.plate["t"] == 6.35 and pv["cuts"] == 2 and pv["w"] == 200
      and pv["names"] == ["A", "B"], "a bare 300 x 200 x 6.35 plate; the 200 x 150 one with A and B is kept")
pl.new_plate()
check(pl.previous_info()["cuts"] == 2, "NEW PLATE on a bare plate keeps the plate kept before (a bare one isn't worth it)")
check(pl.restore_previous() and [c["id"] for c in pl.cuts] == ids and pl.plate["w"] == 200
      and pl.previous_info()["w"] == 300 and pl.previous_info()["cuts"] == 0,
      "LAST PLATE: the 200 x 150 plate and its cut areas are back; the bare one is kept in its place")
c = pl.add("C", "/sd/gcodes/f/c.nc", sq, [90, 0], "cut", "pi")
check(c not in ids and c > max(ids), "ids stay unique across both records")
again = plates.Plates(st3)
check([x["id"] for x in again.cuts] == ids + [c] and again.previous_info()["w"] == 300, "both records are saved")
check(again.restore_previous() and again.plate["w"] == 300 and again.restore_previous() and again.plate["w"] == 200,
      "LAST PLATE again undoes it")
bad = json.load(open(os.path.join(st3, plates.FILE_NAME)))
bad["previous"] = {"plate": "?"}
json.dump(bad, open(os.path.join(st3, plates.FILE_NAME), "w"))
check(plates.Plates(st3).previous_info() is None, "a damaged earlier record is left out")
print("job checks passed")
