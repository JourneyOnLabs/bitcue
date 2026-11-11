"""Checks for the Pi's small screen (z1screen/tft.py) that need no network or display.

Every screen state, plus versions with very long names and odd values, is laid
out and checked: all text inside the 320 x 240 screen and no two pieces of text
on top of each other. Then the picture itself, the RGB565 packing the PiTFT
takes, finding the PiTFT among the framebuffers, and writing to it.
"""
import copy
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from PIL import Image  # noqa: E402
from z1screen import tft  # noqa: E402
import tft_shots  # noqa: E402


def check(c, msg):
    if not c:
        raise AssertionError(msg)
    print("  ok -", msg)


fonts = tft.Fonts()


def boxes(ops):
    """Where each piece of text ends up: (x0, top, x1, bottom, text)."""
    out = []
    for op in ops:
        if op["o"] != "t":
            continue
        text = op["t"]
        if op.get("k"):
            text = op["k"]["fmt"] % "12:59 PM"          # the widest time of day
        f = fonts.get(op["f"], op["z"])
        w = fonts.width(text, op["f"], op["z"], op.get("ls", 0))
        x0 = op["x"] - (w if op.get("a") == "r" else w / 2 if op.get("a") == "m" else 0)
        b = f.getbbox(text, anchor="ls")
        out.append((x0, op["y"] + b[1], x0 + w, op["y"] + b[3], text))
    return out


def problems(ops):
    bad = []
    bx = boxes(ops)
    for x0, top, x1, bottom, text in bx:
        if x0 < -1 or x1 > tft.W + 1 or top < 0 or bottom > tft.H:
            bad.append("outside: %r (%d,%d)-(%d,%d)" % (text, x0, top, x1, bottom))
    for i in range(len(bx)):
        for j in range(i + 1, len(bx)):
            a, b = bx[i], bx[j]
            if min(a[2], b[2]) - max(a[0], b[0]) > 1 and min(a[3], b[3]) - max(a[1], b[1]) > 1:
                bad.append("overlap: %r / %r" % (a[4], b[4]))
    return bad


print("every screen state")
states = tft_shots.states()
check(len(states) >= 19, "%d states from the demo recording and made-up data" % len(states))

LONG = "6 mm flat end mill, long reach, for the deep pockets on the back plate"
variants = []
for name, caption, v in states:
    variants.append((name, v))
    w = copy.deepcopy(v)
    j = w.get("job") or {}
    if j:
        j["name"] = "x" * 90
        if j.get("now"):
            j["now"]["label"] = LONG
        if j.get("next"):
            j["next"].update({"label": LONG, "sticker": "Light blue with a white stripe", "code": "T9999",
                              "in": 99 * 3600 + 59 * 60})
        j["left"] = 123 * 3600.0
        j["elapsed"] = 88 * 3600.0
    tc = w.get("toolchange")
    if tc and tc.get("tool") != 0:
        tc.update({"label": LONG, "sticker": "Light blue with a white stripe", "code": "3D probe"})
        if tc.get("then"):
            tc["then"]["label"] = LONG
    if w.get("alarm"):
        w["alarm"] = "Hard limit triggered on the X axis while moving to the tool change position (alarm 1)"
    if w.get("last_job"):
        w["last_job"].update({"name": "y" * 90, "done": False, "reason": "Stopped before the end by the front button"})
    w.setdefault("machine", {})["name"] = "Makera_Z1_0000000000000000000000000000000"
    w.setdefault("relay", {})["name"] = "Makera_Z1_0000000000000000000000000000000_screen"
    variants.append((name + "+long", w))
    e = copy.deepcopy(v)
    if e.get("job"):
        e["job"].update({"left": None, "elapsed": None, "percent": 250})
        if e["job"].get("next"):
            e["job"]["next"]["in"] = None
    variants.append((name + "+empty", e))
variants += [("none", None), ("blank", {}), ("lost", {"mode": "_lost"}), ("stopped", {"mode": "_stopped"}),
             ("odd", {"mode": "running", "job": {}}), ("odd2", {"mode": "toolchange", "toolchange": {"tool": 5}})]

all_bad = []
for name, v in variants:
    try:
        ops = tft.layout(v, fonts)
        img = tft.paint(ops, fonts, time.time())
    except Exception as exc:
        all_bad.append("%s: %r" % (name, exc))
        continue
    if img.size != (tft.W, tft.H):
        all_bad.append("%s: size %s" % (name, img.size))
    all_bad += ["%s: %s" % (name, p) for p in problems(ops)]
check(not all_bad, "%d versions laid out: all text on the screen, nothing on top of anything else %s"
      % (len(variants), all_bad[:6]))

print("the splash while the screen service starts")
ops = tft.layout({"mode": "_starting"}, fonts)
check([op["t"] for op in ops if op["o"] == "t"] == ["BitCue", "Starting…"]
      and sum(1 for op in ops if op["o"] == "r" and op["c"] in tft.SPLASH_BITS) == 4,
      "the name over four bars in the bits' sticker colors, then Starting…")
lg = next(op for op in ops if op["o"] == "logo")
check(os.path.isfile(tft.LOGO_FILE) and lg["y"] + lg["h"] <= tft.H - 10, "and the maker's logo at the bottom")
for scale in (1, 2):
    box = tft.render({"mode": "_starting"}, fonts, scale=scale).crop(
        [int(lg[k] * scale) for k in ("x", "y")] + [int((lg["x"] + lg["w"]) * scale), int((lg["y"] + lg["h"]) * scale)])
    gold = sum(1 for p in box.getdata() if p[0] > 200 and 150 < p[1] < 200 and p[2] < 90)
    white = sum(1 for p in box.getdata() if min(p) > 230)
    check(gold > 100 * scale * scale and white > 800 * scale * scale,
          "the logo is drawn at %dx: %d gold and %d white pixels" % (scale, gold, white))
check(tft._uptime() > 0, "the time since the Pi started is read (very large where it can't be: no splash is held)")

byname = {n: v for n, c, v in states}
ops = tft.layout(byname["t1"], fonts)
check(any(op["o"] == "t" and op["t"] == "T1" and op["z"] >= 100 for op in ops), "a stop shows the bit number at 100 px or more")
check(not any(op["o"] == "t" and "sticker" in op["t"].lower() for op in ops),
      "no sticker color in words: the screen is that color")
img = tft.render(byname["t1"], fonts)
top = max(img.getcolors(tft.W * tft.H))
check(top[1] == (0xD3, 0x2F, 0x2F) and top[0] > tft.W * tft.H * 0.6, "a T1 stop fills the screen with T1's red")
blink_on, blink_dim = tft.render(byname["t1"], fonts), tft.render(byname["t1"], fonts, blink_dim=True)
check(blink_on.tobytes() != blink_dim.tobytes(), "LOAD blinks at a stop")
t_fixed = time.time()                       # both pictures at the same time of day (the screen shows clock times)
flipped = tft.render(byname["running"], fonts, now=t_fixed, flip=True)
check(flipped.tobytes() == tft.render(byname["running"], fonts, now=t_fixed).transpose(Image.ROTATE_180).tobytes(),
      "turned upside down when asked")
for name, fb, scale in (("PiTFT", tft.Framebuffer("/dev/null", 320, 240, 16), 1),
                        ("3.5inch DPI", tft.Framebuffer("/dev/null", 640, 480, 32), 2)):
    up = tft.render(byname["running"], fonts, now=t_fixed, scale=scale)
    down = tft.render(byname["running"], fonts, now=t_fixed, flip=True, scale=scale)
    check(down.size == (fb.width, fb.height) and down.tobytes() == up.transpose(Image.ROTATE_180).tobytes()
          and fb.frame(down) == fb.frame(up.transpose(Image.ROTATE_180)) != fb.frame(up),
          "%s mounted upside down (installer --flip): the picture written to its framebuffer is turned 180 degrees"
          % name)


def code_of(info):
    return tft.code_text(info)


run_ops = tft.layout(byname["running"], fonts)
texts = [op["t"] for op in run_ops if op["o"] == "t"]
now_bit = byname["running"]["job"]["now"]
check("CUTTING NOW" in texts and code_of(now_bit) in texts and "NEXT" in texts and "T2" in texts and
      any(t.startswith("in ") for t in texts) and "ELAPSED" in texts and "TIME LEFT" in texts,
      "cutting screen: the bit cutting now, the next bit and the time to its stop, elapsed, time left")
card = next(op for op in run_ops if op["o"] in ("r", "s") and op.get("w") == 308)
check(card.get("c") == now_bit["color"], "the big card is the color of the bit cutting now")
check(any(op["o"] == "bar" and len(op["segs"]) >= 4 for op in run_ops), "progress bar split into the bits' colors")
clock_ops = [op for op in run_ops if op.get("k")]
check(clock_ops and all("in" in op["k"] for op in clock_ops), "times of day are worked out when drawn")
v = copy.deepcopy(byname["running"])
v["job"]["name"] = "x" * 90
big = tft.render(v, fonts, scale=2)
lab_end = 2 * (23 + fonts.width("RUNNING", "b6", 11, 1))
gap = big.crop((int(lab_end) + 3, 4, int(lab_end) + 18, 40))
check(len(set(gap.getdata())) == 1, "drawn at 640 x 480, a long job name still keeps clear of RUNNING")
demo_ops = tft.layout(byname["demo"], fonts)
check(any(op["o"] == "t" and op["t"] == "DEMO" for op in demo_ops)
      and not any(op["o"] == "t" and op["t"] == "RUNNING" for op in demo_ops), "demo strip in place of the top line")
unset_ops = tft.layout(byname["unset"], fonts)
check(any(op["o"] == "s" for op in unset_ops), "a bit with no sticker color is striped")


def chip(v):
    """The text of the box for the bit coming out, and the color of the box under it."""
    ops = tft.layout(v, fonts)
    for i, op in enumerate(ops):
        if op["o"] == "t" and op["f"] == "b7" and 27 <= op["y"] <= 49 and op["x"] > 60:   # right of LOAD
            box = next((o for o in reversed(ops[:i]) if o["o"] in ("r", "s")), {})
            return op["t"], box.get("c")
    return None, None


print("the bit coming out at a stop")
check(chip(byname["probe"]) == ("T1 BACK IN RED", "#D32F2F"),
      "at the probe stop: T1 BACK IN RED, in T1's red (the bit left in from the last run)")
check(chip(byname["t1"])[0] == "PROBE OUT", "at the T1 stop: PROBE OUT")
check(chip(byname["t3"]) == ("T2 BACK IN YELLOW", "#FFD600") and byname["t3"]["toolchange"]["out"]["color"] == "#FFD600",
      "at the T3 stop: T2 BACK IN YELLOW, in T2's yellow")
v = copy.deepcopy(byname["probe"])
v["toolchange"]["out"].update({"sticker": "Light blue with a white stripe", "color": "#00B8D4"})
words, color = chip(v)
check(words and words.startswith("LIGHT BLUE") and color == "#00B8D4",
      "a very long sticker name next to LOAD THE PROBE is shortened, in its color: " + str(words))
v = copy.deepcopy(byname["t3"])
v["toolchange"]["out"].update({"unset": True, "sticker": ""})
check(chip(v)[0] == "T2 OUT, NO COLOR SET", "an old bit with no color set says so")
v = copy.deepcopy(byname["t3"])
v["toolchange"].pop("out")
check(chip(v) == (None, None), "no box when the machine doesn't say which bit is in")

print("times")
check(tft.dur(None) == "-" and tft.dur(30) == "under 1 min" and tft.dur(300) == "5 min" and tft.dur(3900) == "1 h 05 min",
      "durations written like the web screen")
t = time.mktime((2026, 10, 3, 14, 5, 0, 0, 0, -1))
check(tft.clock_text(t) == "2:05 PM" and tft.clock_text(t, True) == "14:05", "2:05 PM, or 14:05 if asked")
t0 = time.mktime((2026, 10, 3, 0, 30, 0, 0, 0, -1))
check(tft.clock_text(t0) == "12:30 AM", "half past midnight is 12:30 AM")

print("RGB565 for the PiTFT")
pic = Image.new("RGB", (5, 1))
for i, c in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0x12, 0x34, 0x56)]):
    pic.putpixel((i, 0), c)
vals = struct.unpack("<5H", tft.pack565(pic))
check(vals == (0xF800, 0x07E0, 0x001F, 0xFFFF, 0x11AA), "red, green, blue, white and a mixed color packed right")

print("finding the PiTFT")
root = tempfile.mkdtemp(prefix="z1tft-")
sysfs, devdir = os.path.join(root, "sys"), os.path.join(root, "dev")
os.makedirs(devdir)


def fake_fb(n, name, w, h, bpp, stride):
    d = os.path.join(sysfs, "fb%d" % n)
    os.makedirs(d, exist_ok=True)
    for k, val in (("name", name), ("virtual_size", "%d,%d" % (w, h)), ("bits_per_pixel", str(bpp)),
                   ("stride", str(stride))):
        with open(os.path.join(d, k), "w") as fh:
            fh.write(val + "\n")
    open(os.path.join(devdir, "fb%d" % n), "wb").close()


fake_fb(0, "vc4drmfb", 1920, 1080, 32, 7680)
check(tft.Framebuffer.find(sysfs=sysfs, devdir=devdir) is None, "a monitor's framebuffer is not taken for the PiTFT")
fake_fb(1, "fb_ili9340", 320, 240, 16, 640)
fb = tft.Framebuffer.find(sysfs=sysfs, devdir=devdir)
check(fb is not None and fb.dev.endswith("fb1") and fb.name == "fb_ili9340", "PiTFT found as fb1: %r" % fb)
frame = fb.frame(tft.render(byname["t1"], fonts))
check(len(frame) == 320 * 240 * 2, "one picture is 153,600 bytes")
red565 = struct.pack("<H", ((0xD3 >> 3) << 11) | ((0x2F >> 2) << 5) | (0x2F >> 3))
check(frame.count(red565) > 320 * 240 * 0.6 / 2, "mostly T1's red in RGB565")
fb.write(frame)
fb.write(frame)
fb.close()
check(os.path.getsize(fb.dev) == len(frame), "written at the start of the device each time")
fake_fb(1, "fb_ili9340", 240, 320, 16, 512)
fb = tft.Framebuffer.find(sysfs=sysfs, devdir=devdir)
fr2 = fb.frame(tft.render(byname["t1"], fonts))
check(len(fr2) == 512 * 320, "a portrait framebuffer gets the picture turned, with its row padding")
fake_fb(1, "fb_ili9340", 320, 240, 16, 640)
fake_fb(2, "fb_other", 480, 320, 32, 1920)
check(tft.Framebuffer.find(sysfs=sysfs, devdir=devdir).dev.endswith("fb1"), "320 x 240 preferred over other small screens")
check(tft.Framebuffer.find("/dev/fb7", sysfs=sysfs, devdir=devdir) is None
      and tft.Framebuffer.find(os.path.join(devdir, "fb2"), sysfs=sysfs, devdir=devdir).width == 480,
      "a named device is used as named")
big = tft.Framebuffer(os.path.join(devdir, "fb2"), 480, 320, 32, 1920)
check(len(big.frame(tft.render(byname["t1"], fonts))) == 1920 * 320, "a bigger screen gets the picture scaled to fit")

print("a 640 x 480 screen (the Waveshare 3.5 inch DPI LCD)")
root2 = tempfile.mkdtemp(prefix="z1tft-dpi-")
sysfs, devdir = os.path.join(root2, "sys"), os.path.join(root2, "dev")
os.makedirs(devdir)
fake_fb(0, "vc4drmfb", 640, 480, 32, 2560)
dpi = tft.Framebuffer.find(sysfs=sysfs, devdir=devdir)
check(dpi is not None and dpi.width == 640 and dpi.scale() == 2, "the 640 x 480 screen is found, drawn 2x: %r" % dpi)
fake_fb(1, "fb_other", 480, 320, 32, 1920)
check(tft.Framebuffer.find(sysfs=sysfs, devdir=devdir).dev.endswith("fb0"), "640 x 480 preferred over other sizes")
fake_fb(2, "vc4drmfb", 1280, 720, 32, 5120)
check(tft.Framebuffer.find(sysfs=sysfs, devdir=devdir).dev.endswith("fb0"), "a monitor is still not taken")
two = tft.render(byname["t1"], fonts, scale=2)
check(two.size == (640, 480), "drawn at 640 x 480")
check(len(dpi.frame(two)) == 2560 * 480, "one 640 x 480 picture is %d bytes" % (2560 * 480))
from PIL import Image  # noqa: E402
blown = tft.render(byname["t1"], fonts).resize((640, 480), Image.NEAREST)
check(two.tobytes() != blown.tobytes(), "drawn at size, not blown up (sharper letters)")
check(len(dpi.frame(tft.render(byname["t1"], fonts))) == 2560 * 480, "a 320 x 240 picture is still doubled to fit")
bad = []
for name, view in byname.items():
    try:
        img2, hits2 = tft.render_hits(view, fonts, scale=2)
        img1, hits1 = tft.render_hits(view, fonts)
        if img2.size != (640, 480) or hits2 != hits1:
            bad.append(name)
    except Exception as exc:
        bad.append("%s (%s)" % (name, exc))
check(not bad, "every screen draws at 2x with the same touch targets (%d screens) %s" % (len(byname), bad))

print("the text console kept off the small screen")


class PretendConsole:
    """The kernel's side: which framebuffer each console draws on, each console's mode, and hang-ups (systemd
    hangs up a console's open files when the login prompt starts on it, and switches it back to text)."""

    def __init__(self, fb_of_tty1, locked=False):
        self.fb_of_tty1, self.mode, self.hung, self.opens = fb_of_tty1, tft.KD_TEXT, False, 0
        self.locked = locked           # Raspberry Pi OS 13: only root may open a console

    def open(self, path, flags, mode=0o777):
        if path.endswith("tty1"):
            if self.locked:
                raise PermissionError(13, "Permission denied", path)
            self.opens += 1
            self.hung = False          # a new open file isn't hung up
        return real_open(path, flags, mode)

    def ioctl(self, fd, req, arg=0):
        if req == tft.FBIOGET_CON2FBMAP:
            return struct.pack("II", struct.unpack("II", arg)[0], self.fb_of_tty1)
        if self.hung:
            raise OSError(5, "Input/output error")
        if req == tft.KDGETMODE:
            return struct.pack("i", self.mode)
        if req == tft.KDSETMODE:
            self.mode = arg
            return 0
        raise OSError(25, "Inappropriate ioctl for device")

    def login_prompt(self):
        self.hung, self.mode = True, tft.KD_TEXT


import fcntl  # noqa: E402

real_open = os.open

cdir = tempfile.mkdtemp(prefix="z1con-")
open(os.path.join(cdir, "tty1"), "w").close()
with open(os.path.join(cdir, "active"), "w") as fh:
    fh.write("tty1\n")
saved = (tft.DEVDIR, tft.TTY_ACTIVE, fcntl.ioctl)
tft.DEVDIR, tft.TTY_ACTIVE = cdir, os.path.join(cdir, "active")
try:
    for fb_of_tty1, why in ((0, "the console on the screen's framebuffer"),
                            (2 ** 32 - 1, "a console not on any framebuffer yet")):
        kern = PretendConsole(fb_of_tty1)
        fcntl.ioctl, os.open = kern.ioctl, kern.open
        con = tft.Console(tft.Framebuffer(os.path.join(cdir, "tty1"), 640, 480, 32, index=0))
        con.fb.open()
        check(con.check(100.0) and kern.mode == tft.KD_GRAPHICS,
              "%s: switched to graphics mode, and the picture is drawn again" % why)
        check(not con.check(100.2) and not con.check(100.5), "then left alone while it stays so")
        kern.login_prompt()
        check(con.check(101.0) and kern.mode == tft.KD_GRAPHICS and kern.opens == 2 and not con.warned,
              "the login prompt switching it back to text and hanging it up: opened again and switched back "
              "at the next check")
        con.fb.close()
        con.release()
        check(kern.mode == tft.KD_TEXT, "and back to text when the screen program stops")
    kern = PretendConsole(1)
    fcntl.ioctl, os.open = kern.ioctl, kern.open
    con = tft.Console(tft.Framebuffer(os.path.join(cdir, "tty1"), 640, 480, 32, index=0))
    con.fb.open()
    check(not con.check(100.0) and kern.mode == tft.KD_TEXT and kern.opens == 0,
          "a console drawing on another framebuffer (a monitor) is left alone")
    con.fb.close()
    kern = PretendConsole(0, locked=True)
    fcntl.ioctl, os.open = kern.ioctl, kern.open
    con = tft.Console(tft.Framebuffer(os.path.join(cdir, "tty1"), 640, 480, 32, index=0))
    con.fb.open()
    check(not con.check(100.0) and kern.mode == tft.KD_TEXT and con.warned,
          "only root may open the console and systemd passed nothing: it can't be switched, and that's said once")
    con.fb.close()
    systemd_fd = real_open(os.path.join(cdir, "tty1"), os.O_RDWR)
    con = tft.Console(tft.Framebuffer(os.path.join(cdir, "tty1"), 640, 480, 32, index=0), {"tty1": systemd_fd})
    con.fb.open()
    check(con.check(100.0) and kern.mode == tft.KD_GRAPHICS and kern.opens == 0 and not con.warned,
          "with tty1 opened by systemd: switched to graphics mode without opening it by name")
    con.release()
    try:
        os.fstat(systemd_fd)
        kept = True
    except OSError:
        kept = False
    check(kern.mode == tft.KD_TEXT and kept,
          "back to text when the screen program stops, and systemd's open file is kept for another start")
    con.fb.close()
    os.close(systemd_fd)
finally:
    tft.DEVDIR, tft.TTY_ACTIVE, fcntl.ioctl = saved
    os.open = real_open

print("files systemd opened for the screen program")
me = str(os.getpid())
check(tft.passed_files({"LISTEN_PID": me, "LISTEN_FDS": "1", "LISTEN_FDNAMES": "tty1"}) == {"tty1": 3},
      "tty1, from OpenFile= in the service's unit, is file 3")
check(tft.passed_files({"LISTEN_PID": str(os.getpid() + 1), "LISTEN_FDS": "1", "LISTEN_FDNAMES": "tty1"}) == {},
      "files meant for another program are left alone")
check(tft.passed_files({}) == {} and tft.passed_files({"LISTEN_PID": "x", "LISTEN_FDS": "1"}) == {},
      "nothing passed, or nonsense: none")

print("picture files")
png = tft.png_bytes(tft.render(byname["done"], fonts))
check(png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) < 40000, "PNG for the settings page preview, %d bytes" % len(png))
print("all small-screen checks passed")
