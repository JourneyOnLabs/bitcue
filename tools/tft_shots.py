"""Pictures of every small-screen state, drawn by the real renderer (z1screen/tft.py).

  python3 tools/tft_shots.py [out_dir]

Uses the demo recording of the built-in sample job (z1screen/sample-job.json)
for the job screens and made-up screen data for the rest. Writes one 320 x 240 PNG per state, and sheet.png
with all of them at twice the size and a caption under each. It also draws every state at 640 x 480, the way
the Waveshare 3.5inch DPI screen shows it (tft.render(..., scale=2): the same screen with sharp letters), into
dpi35/ in the same folder, with its own dpi35/sheet.png.
"""
import copy
import json
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from z1screen import config, demo, tft  # noqa: E402

MACHINE = "Makera_Z1"
# The pictures show times of day as if taken at 3:30 in the afternoon, whenever they are made.
_t = time.localtime()
CLOCK = time.mktime((_t.tm_year, _t.tm_mon, _t.tm_mday, 15, 30, 0, 0, 0, -1))
SETTINGS = "http://z1screen.local:8080/settings"


def bit(label, used, limit, choices):
    """Made-up bit life data for a stop or the running screen."""
    from z1screen import bitlife
    st = {"label": label, "used": used, "limit": limit, "choices": choices, "job_adds": 0,
          "state": "ok" if not limit or used < 0.8 * limit else "near" if used < limit else "over"}
    st["text"] = bitlife.BitLife("").short_text(st)
    return st


# Made-up bits for the sample sign's three bit numbers: (label, hours cut before this job, expected life in
# hours, bits of that kind), for the demo page and the top picture.
DEMO_BITS = {1: ("B", 1.1, 3.0, 2), 2: ("A", 2.3, 3.0, 3), 3: ("A", 0.4, 2.0, 2)}


def add_bits(v):
    """Made-up bit life on a screen's data: the bit to put in at a stop, the bit cutting now (its time growing
    with the job) and the bit the next stop will ask for."""
    from z1screen import bitlife
    life = bitlife.BitLife("")

    def made(tool, extra=0.0):
        if tool not in DEMO_BITS:
            return None
        label, hours, limit, choices = DEMO_BITS[tool]
        st = bit(label, hours * 3600 + extra, limit * 3600, choices)
        st["used_text"] = bitlife.hm(st["used"])
        return st
    tc = v.get("toolchange")
    if tc and made(tc.get("tool")):
        tc["bit"] = dict(made(tc["tool"]), job_adds=(tc.get("then") or {}).get("in") or 0)
    j = v.get("job") or {}
    if j.get("now") and made(j["now"].get("tool")):
        j["now"]["bit"] = dict(made(j["now"]["tool"], 0.3 * float(j.get("elapsed") or 0)),
                               job_adds=(j.get("next") or {}).get("in") or j.get("left") or 0)
    if j.get("next") and made(j["next"].get("tool")):
        j["next"]["bit"] = made(j["next"]["tool"])
    return v


def hero(path, fonts):
    """The README's top picture: a stop, cutting, and ready after the job, side by side at 640 x 480."""
    from PIL import Image
    shots = {n: v for n, c, v in states()}
    pics = [tft.render(shots[n], fonts, now=CLOCK, scale=2) for n in ("t3", "running2", "done")]
    gap = 16
    w, h = pics[0].size
    out = Image.new("RGB", (3 * w + 4 * gap, h + 2 * gap), "#151412")
    for i, p in enumerate(pics):
        out.paste(p, (gap + i * (w + gap), gap))
    out.save(path, optimize=True)


def states():
    """(name, caption, view) for every screen worth looking at."""
    cfg = copy.deepcopy(config.DEFAULTS)
    tl = demo.build_timeline(cfg, demo.sample_profile(), MACHINE, SETTINGS)
    fr = tl["frames"]
    out = []

    def first(pred):
        return copy.deepcopy(next(f["view"] for f in fr if pred(f)))

    ready = first(lambda f: f["k"] == "ready")
    out.append(("ready", "Ready, Z1 idle", ready))
    out.append(("probe", "Stop: load the probe", first(lambda f: f["k"] == "stop" and f["tool"] == 0)))
    t1 = first(lambda f: f["k"] == "stop" and f["tool"] == 1)
    t1["toolchange"]["bit"] = bit("B", 3900, None, 2)        # made-up bit life: no expected life set yet
    out.append(("t1", "Stop: load T1, bit B of 2", t1))
    run = [f for f in fr if f["k"] == "run" and f["view"]["job"].get("next")]
    out.append(("running", "Cutting with T1, next bit T2", copy.deepcopy(run[min(8, len(run) - 1)]["view"])))
    mid = [f for f in run if f["view"]["job"]["next"]["code"] == "T3"]
    r2 = copy.deepcopy(mid[len(mid) // 2]["view"])
    r2["job"]["now"]["bit"] = dict(bit("A", 4700, 7200, 1), used_text="1 h 18", job_adds=r2["job"]["next"]["in"])
    r2["job"]["next"]["bit"] = bit("C", 9300, 10800, 3)
    out.append(("running2", "Cutting with T2, bit A, next T3", r2))
    t3 = first(lambda f: f["k"] == "stop" and f["tool"] == 3)
    t3_life = copy.deepcopy(t3)
    t3_life["toolchange"]["bit"] = dict(bit("C", 9300, 10800, 3), job_adds=900)
    out.append(("t3", "Stop: load T3, bit C near its life", t3_life))
    last = [f for f in fr if f["k"] == "run" and not f["view"]["job"].get("next")]
    out.append(("lastbit", "Cutting with the last bit", copy.deepcopy(last[len(last) // 3]["view"])))
    done = first(lambda f: f["k"] == "done")
    done["last_job"]["ended"] = CLOCK - 300
    out.append(("done", "Ready, last job finished", done))
    out.append(("alarm_probe", "Stopped: probe not detected", copy.deepcopy(tl["extras"]["probe"])))
    out.append(("alarm_hand", "Stopped by hand", copy.deepcopy(tl["extras"]["hand"])))

    paused = copy.deepcopy(run[min(8, len(run) - 1)]["view"])
    paused["mode"] = "paused"
    out.append(("paused", "Paused", paused))
    unknown = copy.deepcopy(run[min(8, len(run) - 1)]["view"])
    j = unknown["job"]
    j.update({"known": False, "next": None, "segments": [], "changes_total": 0, "changes_left": None,
              "name": "my-new-job-from-another-computer"})
    out.append(("unknown", "Job the screen hasn't read", unknown))
    unset = copy.deepcopy(t3)
    unset["toolchange"].update({"code": "T12", "tool": 12, "label": "6 mm flat end mill", "unset": True,
                                "color": "#5A5650", "text": "#FFFFFF", "sticker": ""})
    out.append(("unset", "Stop: bit with no color set", unset))
    check = demo.build_check(cfg, 7, {"name": "2 mm ball end mill", "color": "#00B8D4", "sticker": "Light blue"},
                             MACHINE, SETTINGS)["frames"][0]["view"]
    check["demo"] = {"kind": "check", "ends_in": 18, "waiting": False, "speed": 60}
    out.append(("check", "Color check from the settings page", check))
    demo_run = copy.deepcopy(run[min(30, len(run) - 1)]["view"])
    demo_run["demo"] = {"kind": "demo", "ends_in": 1500, "waiting": False, "speed": 60}
    out.append(("demo", "Demo playing", demo_run))
    base = {"settings_url": SETTINGS, "machine": {"name": MACHINE}, "relay": {"name": MACHINE + "_screen"}}
    out.append(("searching", "Z1 off or not found", dict(base, mode="searching", machine={"name": ""})))
    out.append(("connecting", "Z1 found, connecting", dict(base, mode="connecting")))
    out.append(("direct", "Studio connected straight to the Z1", dict(base, mode="direct")))
    out.append(("starting", "Pi starting up", {"mode": "_starting"}))
    return out


def sheet(pics, path, fonts, cell=(tft.W * 2, tft.H * 2), cols=3):
    """A contact sheet: (caption, picture) pairs in a grid, each picture fitted to `cell` (sharp-edged when
    that is a whole multiple of its size), the caption under it."""
    from PIL import Image, ImageDraw
    cw, ch = cell
    pad, cap = 24, 30
    rows = (len(pics) + cols - 1) // cols
    out = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + pad + cap) + pad), "#2b2925")
    d = ImageDraw.Draw(out)
    font = fonts.get("b6", 18)
    for i, (caption, img) in enumerate(pics):
        x = pad + (i % cols) * (cw + pad)
        y = pad + (i // cols) * (ch + pad + cap)
        if img.size != (cw, ch):
            whole = cw % img.width == 0 and ch % img.height == 0
            img = img.resize((cw, ch), Image.NEAREST if whole else Image.LANCZOS)
        out.paste(img, (x, y))
        d.text((x, y + ch + 22), caption, font=font, fill="#f4f0e8", anchor="ls")
    out.save(path)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screens")
    os.makedirs(out_dir, exist_ok=True)
    fonts = tft.Fonts()
    shots = states()
    pics = []
    for name, caption, v in shots:
        img = tft.render(v, fonts, now=CLOCK)
        img.save(os.path.join(out_dir, name + ".png"))
        with open(os.path.join(out_dir, name + ".json"), "w") as fh:
            json.dump(v, fh)
        pics.append((caption, img))
    sheet(pics, os.path.join(out_dir, "sheet.png"), fonts)
    big_dir = os.path.join(out_dir, "dpi35")
    os.makedirs(big_dir, exist_ok=True)
    big = []
    for name, caption, v in shots:
        img = tft.render(v, fonts, now=CLOCK, scale=2)
        img.save(os.path.join(big_dir, name + ".png"))
        big.append((caption, img))
    sheet(big, os.path.join(big_dir, "sheet.png"), fonts)
    if len(sys.argv) <= 1:                   # the README's top picture, next to docs/screens
        hero(os.path.join(ROOT, "docs", "screens.png"), fonts)
        print("wrote", os.path.abspath(os.path.join(ROOT, "docs", "screens.png")))
    print("wrote", len(pics), "pictures and sheet.png to", os.path.abspath(out_dir))
    print("wrote", len(big), "640 x 480 pictures and sheet.png to", os.path.abspath(big_dir))


if __name__ == "__main__":
    main()
