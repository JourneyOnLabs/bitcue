"""Small checks that need no network."""
import copy, json, os, re, tempfile, time
from common import ANCHOR, SIGN, TAG, check, cfg as test_cfg, make_library, status as st, HERE
from z1screen import config, jobs_api, plates as plates_mod, prefs, quicklz, start, web
from z1screen.hub import Hub
from z1screen.jobs import JobStore
from z1screen.status import parse_status

store = JobStore(tempfile.mkdtemp(), [make_library((SIGN, TAG))])
cfg = copy.deepcopy(config.DEFAULTS)
hub = Hub(cfg, store)

print("quick re-run keeps the new file name")
hub.on_app_command("play " + SIGN)
hub.on_status(st("Run", 5000, 10, 900))
check(hub.job["path"] == SIGN, "job A tracked")
hub.on_status(st("Alarm", 5000, 10, 900, halt=1))      # aborted; firmware still sends P a few times
hub.on_app_command("play " + TAG)                       # Run pressed on B straight away
time.sleep(0.01)
hub.job["seen"] -= 5                                    # P stops for 3+ s
hub.on_status(st("Idle"))
check(hub.job is None and hub.play_path == TAG, "A ended, B's play command kept")
hub.on_status(st("Run", 10, 0, 1))
check(hub.job and hub.job["path"] == TAG and hub.job.get("profile") is not None, "B starts with its name and timing")

print("never compete with an app connected straight to the machine")
hub2 = Hub(cfg, store)
hub2.m_ip, hub2.m_busy, hub2.m_seen = "10.0.0.5", True, time.time()
check(hub2._held_by_other(), "busy machine with no Pi connection counts as held by another app")
hub2.own = {"writer": None}
check(not hub2._held_by_other(), "busy because of the Pi's own connection does not")

print("a mistyped color does not freeze the screen")
cfg3 = copy.deepcopy(config.DEFAULTS); cfg3["tools"]["3"]["color"] = "#1565CO"
hub3 = Hub(cfg3, store)
info = hub3.tool_info(3)
check(info["unset"] and info["color"].startswith("#") and len(info["color"]) == 7, "a mistyped color shows as no color set (%s)" % info["color"])

print("status parsing of firmware output")
s = parse_status("<Tool|MPos:-20.0000,-20.0000,-20.0000,0.0000,0.0000|WPos:180.0000,180.0000,60.0000,0.0000,0.0000|F:0.0,3000.0,100.0|S:0.0,0.0,100.0,0,25.5,30.1,1,1,0,0|T:-1,0.500,0|W:0.00|L:0, 0, 0, 0.0,100.0|P:0,0,12|C:5,0,0,1>")
check(s["state"] == "Tool" and s["target_tool"] == 0 and s["line"] == 0 and s["elapsed"] == 12, "Tool state, probe wanted, progress fields")

print("the release defaults: nothing about one person's machine")
d = config.DEFAULTS
check(d["anchor1"] == "auto" and d["plate"] is None and d["plate_layouts"] == [] and d["start_from_screen"] is False,
      "Anchor 1 read from the machine, no plate size, no layout folders, starting from the screen off")
check(all(not d["tools"][k]["name"] for k in ("1", "2", "3", "4", "5", "6", "7", "8")),
      "bits T1-T8 have colors but no names (names come from the job files)")
h = Hub(copy.deepcopy(d), JobStore(tempfile.mkdtemp(), []))
check(h.anchor1 is None and h.anchor_auto and not h.start_enabled() and not h.plates.is_set,
      "a fresh Pi: Anchor 1 unknown until read, starting off, plate size not set")
check(h.plates.plate["w"] == 200 and h.plates.plate.get("set") is False, "the placeholder plate is 200 x 200 and marked not set")
h.plates.set_size({"w": 120, "h": 80, "t": 6, "material": "Walnut"})
check(h.plates.is_set and h.plates.plate["w"] == 120, "saving a size marks the plate set")
p2 = plates_mod.Plates(tempfile.mkdtemp(), "", {"w": 150, "h": 150, "t": 3, "material": "Aluminium"})
check(p2.is_set, "a plate given in the settings file counts as set")
rec = {"version": 2, "plate": {"w": 100, "h": 100, "t": 3, "material": ""}, "cuts": []}
dd = tempfile.mkdtemp()
with open(os.path.join(dd, "plates.json"), "w") as fh:
    import json; json.dump(rec, fh)
check(plates_mod.Plates(dd, "", None).is_set, "a record from an earlier version (no 'set') counts as set")

print("design names from file names")
start.set_design_names(config.DEFAULTS["design_names"])
check(start.design_key("/sd/gcodes/x/bracket-position-2-front.nc") == "bracket" and start.design_name("bracket") == "Bracket",
      "default pattern: bracket-position-2-front.nc is design Bracket")
check(start.design_key("/sd/gcodes/x/sample-sign.nc") == "sample-sign", "a file without a position keeps its own name")
check(start.design_name("wall-hook-v2") == "Wall Hook v2", "names: words capitalized, words with digits kept")
start.set_design_names([r"^job-(?P<design>[a-z]+)-\d+$", "([bad", r"^(?P<other>x)$"])
check(start.design_key("/sd/gcodes/x/job-lamp-7.nc") == "lamp" and start.design_key("/sd/gcodes/x/x.nc") == "x",
      "own patterns from the settings; a broken pattern or one without a design group is skipped")
start.set_design_names(config.DEFAULTS["design_names"])

print("the start commands")
check(start.origin_command(ANCHOR, [0, 0]) == "G10L2P0X-200.000Y-200.000", "work origin at Anchor 1 with offsets 0")
check(start.m495_command([10, 10, 70, 50], {"margin": True, "probe": {"x": 5, "y": 5}, "level": {"i": 5, "j": 5, "h": 2}})
      == "M495 X10Y10C70D50O-5F-5A60B40I5J5H2", "M495 as in start.py's example")
check(start.m495_command([10, 10, 70, 50], {"margin": False, "probe": None, "level": None}) == "M495 X10Y10P1",
      "everything off: just go to the start point")

print("QuickLZ: files made with the Z1 firmware's own QuickLZ 1.5.0 (level 3)")
qd = os.path.join(HERE, "fixtures", "qlz")
for name, what in (("sign-24k.nc", "6 compressed blocks of G-code"), ("config.txt", "a short settings file"),
                   ("short.bin", "a block under 9 bytes"), ("noise.bin", "data QuickLZ stores uncompressed")):
    plain = open(os.path.join(qd, name), "rb").read()
    lz = open(os.path.join(qd, name + ".lz"), "rb").read()
    check(quicklz.looks_compressed(lz) and quicklz.decompress_container(lz) == plain, "%s: %s" % (name, what))
bad = bytearray(open(os.path.join(qd, "sign-24k.nc.lz"), "rb").read()); bad[-1] ^= 1
try:
    quicklz.decompress_container(bytes(bad)); ok = False
except ValueError:
    ok = True
check(ok, "a wrong checksum is caught")

print("settings page values are checked")
check(prefs.clean_anchor("auto") == "auto" and prefs.clean_anchor(["-200.5", -195.25]) == [-200.5, -195.25], "Anchor 1: auto or two numbers")
for bad_v in (["x", 1], [1], [5000, 0]):
    try:
        prefs.clean_anchor(bad_v); ok = False
    except ValueError:
        ok = True
    check(ok, "Anchor 1 %r refused" % (bad_v,))
check(prefs.clean_run({"probe": None, "margin": 1, "level": {"i": "3", "j": 4, "h": "2.5"}})
      == {"probe": None, "margin": True, "level": {"i": 3, "j": 4, "h": 2.5}}, "run settings tidied")
check(prefs.clean_folders("/sd/gcodes/a\n/sd/gcodes/b/ , /sd/gcodes/a") == ["/sd/gcodes/a", "/sd/gcodes/b"], "layout folders: one per line, no repeats")
for bad_v in ("/etc", "/sd/gcodes/../x"):
    try:
        prefs.clean_folders(bad_v); ok = False
    except ValueError:
        ok = True
    check(ok, "layout folder %r refused" % bad_v)
hsh = prefs.pin_hash("2468")
# Not '"2468" not in hsh': the random hex contains 2468 about once in 900 runs.
check(prefs.pin_ok(hsh, "2468") and not prefs.pin_ok(hsh, "1357") and re.fullmatch(r"[0-9a-f]+:[0-9a-f]{64}", hsh)
      and prefs.pin_hash("2468") != hsh, "the PIN is kept as a salted hash")
check(prefs.pin_ok("1234", "1234") and not prefs.pin_ok("1234", "") and prefs.pin_ok("", ""), "a PIN typed in z1screen.json works too")

print("the web server only answers to local names")
for name in ("z1screen.local", "z1screen", "192.168.1.50", "localhost", "::1", "z1screen.lan", "pi.home.arpa", ""):
    check(web.local_host(name), "%r allowed" % name)
for name in ("evil.example.com", "z1screen.example.org"):
    check(not web.local_host(name), "%r refused (DNS rebinding)" % name)
check(web.local_host("shop.example.com", ["shop.example.com"]), "a name listed in allowed_hosts is allowed")
check(web.host_name("Z1Screen.local:8080") == "z1screen.local" and web.host_name("[::1]:8080") == "::1"
      and web.host_name("http://z1screen.local:8080") == "z1screen.local", "host names from Host and Origin headers")

print("the Pi joins a long job part way, after a restart")
PROG = "file: %s, 40 %% complete, elapsed time: 00:15:00" % SIGN


def joined(stopped_before=True):
    """A hub that starts while the sign runs, 15 min in. Its work origin is Anchor 1 (see common.status)."""
    h = Hub(test_cfg(), JobStore(tempfile.mkdtemp(), [make_library((SIGN,))]))
    old = h.plates.add("sample-sign", SIGN, None, [0, 0], "started", "app") if stopped_before else None
    h.on_status(st("Run", 5000, 40, 900))
    return h, old


h, old = joined()
check(h.job and not h.job["path"] and not h.job.get("cut_id") and len(h.plates.cuts) == 1,
      "file name not known yet: no area added")
h.on_console_line(PROG)
h.on_status(st("Run", 5010, 40, 905))
check(h.job["path"] == SIGN and h.job.get("cut_id") == old and len(h.plates.cuts) == 1,
      "named: the job uses its area from before the restart")
h, old = joined()
h.job["started"] -= 200                                 # no name for over 2 minutes
h.on_status(st("Run", 5010, 40, 905))
cid = h.job.get("cut_id")
check(cid and cid != old and h.plates.get(cid)["name"] == "A job the Pi couldn't name",
      "no name after 2 minutes: an unnamed area")
h.on_console_line(PROG)
h.on_status(st("Run", 5020, 40, 910))
check(h.job["cut_id"] == old and [c["id"] for c in h.plates.cuts] == [old],
      "named later: the unnamed area goes, the job uses its area from before")
h, _ = joined(stopped_before=False)
h.job["started"] -= 200
h.on_status(st("Run", 5010, 40, 905))
h.on_console_line(PROG)
h.on_status(st("Run", 5020, 40, 910))
c = h.plates.get(h.job["cut_id"])
check(len(h.plates.cuts) == 1 and c["name"] == "sample-sign" and c["file"] == SIGN and c["shape"],
      "named later, nothing there before: the area gets the job's name and shape")

print("a touch calibration is used only on the screen it was made on, else the built-in one for its size")
tdir = tempfile.mkdtemp()
saved = {"ax": [0.0, 0.0893, -22.3], "ay": [-0.069, 0.0, 268.97], "flip": False, "t": 1.0}  # as older versions saved it
with open(os.path.join(tdir, "touch.json"), "w", encoding="utf-8") as fh:
    json.dump(saved, fh)


def touch_hub():
    return Hub(copy.deepcopy(config.DEFAULTS), JobStore(tdir, [make_library((SIGN,))]))


th = touch_hub()
check(th.touch_info()["cal"] == saved and th.setup_info()["touch_calibrated"],
      "until the small screen names its screen, the saved calibration is used")
check(th.touch_info("320x240")["cal"] == saved and th.debug_info()["touch_calibration"] == "saved",
      "one saved without its screen was made on the PiTFT (320 x 240)")
check(th.touch_info("640x480")["cal"] == jobs_api.BUILT_IN_TOUCH[(640, 480)] and th.setup_info()["touch_calibrated"]
      and th.debug_info()["touch_calibration"] == "built-in",
      "the 3.5inch DPI screen (640 x 480) gets the built-in one instead, and the settings page counts it as set up")
th.set_touch({"cal": dict(saved, screen=[640, 480])})
check(th.touch["screen"] == [640, 480] and th.touch_info("640x480")["cal"] == th.touch
      and th.debug_info()["touch_calibration"] == "saved", "one made on it is saved with its screen and wins")
th = touch_hub()
check(th.touch_info("640x480")["cal"]["screen"] == [640, 480], "and still after a restart")
check(th.touch_info("320x240")["cal"] is None and not th.setup_info()["touch_calibrated"],
      "a PiTFT with none saved for it gets none (resistive panels vary), so the dots come up")
th.touch_info("640 by 480")
check(th.touch_info()["cal"] is None, "a screen size that can't be read changes nothing")
th.set_touch({"cal": saved})
check(th.touch["screen"] == [320, 240], "a calibration sent without its screen is kept for the screen named last")

print("all unit checks passed")
