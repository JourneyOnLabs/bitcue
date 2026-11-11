"""The warning before a start from the touch screen: the material, size and thickness the job file says it was
made for, on the plate check and again on the review screen; red when the plate on record differs."""
from common import check
from z1screen import tft, touch

FILE = {"w": 150, "h": 150, "t": 3, "material": "Aluminum"}

print("what the job expects, against the plate on record")
ok = touch.expects(FILE, {"w": 150, "h": 150, "t": 3, "material": "Aluminum"})
check(ok == {"text": "This job expects: Aluminum, 150 × 150 × 3 mm.", "detail": "", "differs": False},
      "the same plate: %s" % ok["text"])
for why, plate in (("another material", {"w": 150, "h": 150, "t": 3, "material": "Plywood"}),
                   ("another size", {"w": 200, "h": 150, "t": 3, "material": "Aluminum"}),
                   ("another thickness", {"w": 150, "h": 150, "t": 6, "material": "Aluminum"})):
    ex = touch.expects(FILE, plate)
    check(ex["differs"] and ex["detail"].startswith("The plate on record: ") and "%g" % plate["t"] in ex["detail"],
          "%s: it differs, and says what the plate on record is" % why)
check(not touch.expects(dict(FILE, w=100, h=150), {"w": 150, "h": 100, "t": 3, "material": "aluminum"})["differs"],
      "a plate turned a quarter, or a material in other letters: the same")
check(not touch.expects(dict(FILE, material=""), {"w": 150, "h": 150, "t": 3, "material": "Plywood"})["differs"]
      and "a plate, 150" in touch.expects(dict(FILE, material=""), {})["text"],
      "a file that names no material: only size and thickness count")
none = touch.expects(None, {"w": 150, "h": 150, "t": 3})
check("doesn't say what plate" in none["text"] and not none["differs"], "a file that says nothing: check it yourself")

print("on the screens")
fonts = tft.Fonts()
lines = ["Plywood plate 150 × 150 × 3 mm, pushed into the Anchor 1 corner.", "The deepest cut is 3.45 mm.",
         "The part's area, X78 to 144 and Y5 to 72 mm from the corner, must be bare.", "Cut on this plate: one part."]
bad = touch.expects(FILE, {"w": 150, "h": 150, "t": 3, "material": "Plywood"})
for ex, colour in ((ok, tft.HOLD), (bad, tft.STOP)):
    L = tft.Layout(fonts)
    L._ui_check({"screen": "check", "lines": lines, "has_used": True, "confirm_new": False, "expects": ex})
    box = [o for o in L.ops if o["o"] == "r" and o.get("c") == colour and o["y"] < 40]
    texts = " ".join(o["t"] for o in L.ops if o["o"] == "t")
    check(box and "This job expects: Aluminum" in texts, "check the plate: the warning on top, %s" % (
        "red" if ex["differs"] else "amber"))
    check(all(o["y"] < tft.H - 44 for o in L.ops if o["o"] == "t" and o["t"] in " ".join(lines)),
          "and the plate's lines still above the buttons")
L = tft.Layout(fonts)
L._ui_review({"screen": "review", "title": "Sample badge · spot 2", "preview": "x", "why": "", "note": "", "expects": bad,
              "lines": ["Origin: Anchor 1, offsets 0", "Z probe X78.26 Y5.17 from the work origin",
                        "Leveling 9 x 9 · Scan Margin on", "Probe first, then T1 (Red sticker)", "Run time about 4 h 38 min"]})
t = [o for o in L.ops if o["o"] == "t"]
first = next(o for o in t if o["t"].startswith("This job expects"))
check(first["c"] == tft.STOP, "the review screen says it again, in red, before the run settings")
check(all(o["y"] <= tft.H - 52 for o in t if o["t"] not in ("BACK", "HOLD TO START")),
      "everything fits above the buttons (smaller letters if needed)")
pic = next(o for o in L.ops if o["o"] == "img")
fonts_w = lambda o: fonts.width(o["t"], o["f"], o["z"], o.get("ls", 0) or 0)
beside = [o for o in t if o["y"] - o["z"] < pic["y"] + pic["h"] and o["y"] > pic["y"] and o["x"] < pic["x"]]
check(all(o["x"] + fonts_w(o) <= pic["x"] for o in beside), "and nothing runs under the part's picture")
print("all plate warning checks passed")
