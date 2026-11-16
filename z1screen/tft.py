"""The Pi's own small screen: Adafruit PiTFT Plus 2.8" (320 x 240).

The screen service (python3 -m z1screen) works out what to show. This program
draws it on the small screen. It runs as a separate service, z1screen-tft, so a
problem with the display can never get in the way of the relay Makera Studio
connects through.

Twice a second it reads the screen data from the screen service on this Pi
(http://127.0.0.1:8080/state). layout() turns that data into a short list of
drawing steps (boxes, text, the progress bar), paint() draws them with Pillow,
and the picture is written straight into the display's framebuffer. No desktop
or browser is needed. The demo page draws the same steps in a browser, so it
shows exactly what the small screen shows. Until the screen service answers,
it shows the splash: the name over a strip in the bits' sticker colors, and
the maker's logo (Journey On Labs); just after a power-on, for at least 3 s.
It reads the settings page's choices for this screen (screen.json in the state
folder) when it starts, so the splash is already turned for a screen mounted
upside down.

  python3 -m z1screen.tft                 run on the Pi (the z1screen-tft service)
  python3 -m z1screen.tft --png out.png   draw what the screen shows now into a picture
  python3 -m z1screen.tft --list          list this Pi's framebuffers

It never talks to the machine itself. The touch layer (touch.py) lists the
jobs on the Z1 and asks the screen service to start one, through the same
service on this Pi.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import os
import signal
import struct
import sys
import time
import urllib.request

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError:  # the screen service imports this module only for the preview picture
    Image = None

from . import prefs as prefs_mod
from .bitlife import hm as bit_time

log = logging.getLogger("z1screen.tft")

W, H = 320, 240
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "web", "fonts")
SYSFS = "/sys/class/graphics"                 # where the framebuffers are listed
DEVDIR = "/dev"
TTY_ACTIVE = "/sys/class/tty/tty0/active"     # which text console is on screen

# Same colors as the web screen (index.html).
GROUND = "#141311"
PANEL = "#1E1C19"
LINE = "#36322C"
INK = "#F4F0E8"
MUTED = "#AAA397"
DIM = "#6E685E"
STOP = "#FF5B4F"
GO = "#58C26B"
HOLD = "#E8B23A"
UNSET = "#5A5650"
PLATE = "#5C4E2E"          # the plate on the placing screens
PLATE_EDGE = "#B39B5E"
CUT_AREA = "#7E2C24"       # areas already cut
CUT_STARTED = "#8A6420"    # areas of a job that stopped part way
SPLASH_BITS = ("#D32F2F", "#FFD600", "#7CB342", "#00B8D4")   # red, yellow, light green, light blue stickers (bits.py)
LOGO_FILE = os.path.join(HERE, "web", "brand", "journeyonlabs-logo.png")
LOGO_W, LOGO_H = 192, 64   # the logo on the splash (the file is 767 x 256: sharp up to 4 times this)
SPLASH_BOOT_S = 3.0        # just after a power-on the splash stays at least this long
PLACE_WORDS = {"ok": "CLEAR", "overlap": "OVERLAPS A CUT", "close": "TOO CLOSE TO A CUT", "off": "OFF THE PLATE",
               "probe": "PROBE SPOT NOT BARE", "level": "LEVELING NOT CLEAR", "empty": "NOTHING TO CUT",
               "unknown": "PLATE NOT KNOWN"}
PLACE_HELP = {"overlap": "Move it off the red area.", "close": "Keep 1 mm of bare plate around it.",
              "off": "Keep the part on the plate.", "probe": "The cross is the Z probe spot.",
              "level": "The leveling box must be bare.", "empty": "No cuts found in this file.",
              "unknown": "The Pi doesn't know one cut area."}

# Font keys used in the drawing steps: d = Barlow Condensed, b = Barlow; the digit is the weight.
FONT_FILES = {
    "d7": "barlow-condensed-latin-700-normal.ttf",
    "d6": "barlow-condensed-latin-600-normal.ttf",
    "d5": "barlow-condensed-latin-500-normal.ttf",
    "b7": "barlow-latin-700-normal.ttf",
    "b6": "barlow-latin-600-normal.ttf",
    "b5": "barlow-latin-500-normal.ttf",
    "b4": "barlow-latin-400-normal.ttf",
}
FONT_CSS = {k: ("Barlow Condensed" if k[0] == "d" else "Barlow", int(k[1]) * 100) for k in FONT_FILES}

PROBE_HELP = ("Check that the probe is in the spindle and its cable is plugged into the side of the head. "
              "Then unlock the machine in Makera Studio and start the job again.")
HAND_HELP = "The job was stopped from the front button or from Makera Studio. Unlock the machine in Makera Studio to carry on."
ALARM_HELP = "Check the machine, then unlock it in Makera Studio."


# ---------------------------------------------------------------- small helpers
def _rgb(c: str):
    c = (c or "#000000").lstrip("#")
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)


def mix(a: str, b: str, t: float) -> str:
    """Color a at strength t over color b (what CSS opacity does on a solid background)."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02X%02X%02X" % tuple(int(round(x * t + y * (1 - t))) for x, y in zip(ra, rb))


BIT_LINE = "#1D1B18"                        # the bit line on the stop screen: dark on any sticker color
GAUGE_TRACK = "#4A4640"
LIFE_GREEN, LIFE_YELLOW = "#3FB950", HOLD


def life_color(f: float) -> str:
    """The color of a bit's life gauge at a share f of its expected life: green, to yellow at 80 %, to red at
    100 %."""
    if f <= 0.5:
        return LIFE_GREEN
    if f < 0.8:
        return mix(LIFE_YELLOW, LIFE_GREEN, (f - 0.5) / 0.3)
    if f < 1.0:
        return mix(STOP, LIFE_YELLOW, (f - 0.8) / 0.2)
    return STOP


def text_on(bg: str) -> str:
    """Dark or white text for a background (the same rule as the hub and the web page)."""
    lin = []
    for v in _rgb(bg):
        c = v / 255.0
        lin.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return "#111111" if 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] > 0.3 else "#FFFFFF"


def dur(s) -> str:
    """Minutes, as the web screen writes them: 'under 1 min', '12 min', '1 h 05 min'."""
    if s is None:
        return "-"
    try:
        s = max(0, int(round(float(s))))
    except (TypeError, ValueError):
        return "-"
    if s < 60:
        return "under 1 min"
    h, m = divmod(s // 60, 60)
    return "%d min" % m if h == 0 else "%d h %02d min" % (h, m)


def _mm(v) -> str:
    """A length in mm with at most one decimal: 12, 12.5, -3."""
    try:
        s = ("%.1f" % float(v)).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "-"
    return "0" if s in ("-0", "") else s


def clock_text(epoch: float, h24: bool = False) -> str:
    t = time.localtime(epoch)
    if h24:
        return "%d:%02d" % (t.tm_hour, t.tm_min)
    return "%d:%02d %s" % (t.tm_hour % 12 or 12, t.tm_min, "AM" if t.tm_hour < 12 else "PM")


def code_text(info) -> str:
    if not info:
        return "-"
    return "Probe" if info.get("tool") == 0 else (info.get("code") or "-")


def sticker_text(info) -> str:
    if not info:
        return ""
    if info.get("unset"):
        return "No sticker color set"
    return (info.get("sticker") + " sticker") if info.get("sticker") else ""


def out_words(out) -> list:
    """What the box for the bit coming out of the spindle says, longest wording first."""
    code = code_text(out).upper()
    if out.get("tool") in (0, 8888, 9999):
        return ["%s OUT" % code]
    if out.get("unset") or not out.get("sticker"):
        return ["%s OUT, NO COLOR SET" % code, "%s OUT" % code]
    sticker = str(out["sticker"]).upper()
    return ["%s BACK IN %s" % (code, sticker), "%s IN %s" % (code, sticker), sticker]


def short_url(u: str) -> str:
    u = u or ""
    for p in ("http://", "https://"):
        if u.startswith(p):
            return u[len(p):]
    return u


# ---------------------------------------------------------------- fonts
class Fonts:
    """The kit's Barlow fonts at any size, with simple (unkerned) text measuring."""

    def __init__(self, folder: str = FONT_DIR) -> None:
        if Image is None:
            raise RuntimeError("Pillow is not installed (sudo apt install python3-pil)")
        self.folder = folder
        self.cache = {}
        layout = getattr(getattr(ImageFont, "Layout", None), "BASIC", None)
        self.engine = layout if layout is not None else getattr(ImageFont, "LAYOUT_BASIC", 0)

    def get(self, key: str, size: int):
        k = (key, int(size))
        f = self.cache.get(k)
        if f is None:
            f = ImageFont.truetype(os.path.join(self.folder, FONT_FILES[key]), int(size), layout_engine=self.engine)
            self.cache[k] = f
        return f

    def width(self, text: str, key: str, size: int, ls: float = 0.0) -> float:
        if not text:
            return 0.0
        f = self.get(key, size)
        if ls:
            return sum(f.getlength(ch) for ch in text) + ls * (len(text) - 1)
        return f.getlength(text)

    def cap(self, key: str, size: int) -> int:
        """Height of a capital letter above the baseline."""
        box = self.get(key, size).getbbox("H", anchor="ls")
        return -box[1]


# ---------------------------------------------------------------- layout
class Layout:
    """Turns the screen data into drawing steps for a 320 x 240 screen.

    Every step is a small dict, so the same steps can be drawn by paint() here
    and by the demo page in a browser canvas:
      {"o": "r", x, y, w, h, c, r}        box (r = corner radius)
      {"o": "s", x, y, w, h, c, c1, c2, p, r}  striped box (a bit with no sticker color)
      {"o": "c", x, y, r, c}              dot
      {"o": "t", x, y, t, f, z, c, a, ls, mw, bl, k}
          text at baseline y; f font key, z size in px, a = l/r/m alignment,
          ls letter spacing, mw widest it may be, bl color when blinking dim,
          k = {"in": seconds or "at": time, "fmt": "around %s"} for a time of day
      {"o": "bar", ...}                   the job's progress bar in the bits' colors
      {"o": "img", x, y, w, h, src}       the picture of a job's part, src = its md5 (see Previews)
      {"o": "logo", x, y, w, h}           the maker's logo (web/brand/journeyonlabs-logo.png), fitted to the box
    """

    def __init__(self, fonts: Fonts) -> None:
        self.fonts = fonts
        self.ops = []
        self.hits = []        # touch targets drawn on this screen: (x, y, w, h, action)

    # -------------------------------------------------------- drawing steps
    def rect(self, x, y, w, h, c, r=0):
        op = {"o": "r", "x": x, "y": y, "w": w, "h": h, "c": c}
        if r:
            op["r"] = r
        self.ops.append(op)

    def stripes(self, x, y, w, h, c, r=0, p=24):
        op = {"o": "s", "x": x, "y": y, "w": w, "h": h, "c": c, "c1": mix("#FFFFFF", c, 0.08),
              "c2": mix("#000000", c, 0.12), "p": p}
        if r:
            op["r"] = r
        self.ops.append(op)

    def fill(self, x, y, w, h, info_or_color, r=0, p=24):
        """A box in a bit's color, striped if the bit has no sticker color."""
        if isinstance(info_or_color, dict):
            if info_or_color.get("unset"):
                self.stripes(x, y, w, h, info_or_color.get("color") or UNSET, r, p)
                return
            info_or_color = info_or_color.get("color") or UNSET
        self.rect(x, y, w, h, info_or_color, r)

    def dot(self, x, y, r, c):
        self.ops.append({"o": "c", "x": x, "y": y, "r": r, "c": c})

    def picture(self, x, y, w, h, src):
        """The picture of a job's part (preview.py), named by its md5; nothing is drawn while there is none."""
        if src:
            self.ops.append({"o": "img", "x": x, "y": y, "w": w, "h": h, "src": src})

    def logo(self, x, y, w, h):
        self.ops.append({"o": "logo", "x": x, "y": y, "w": w, "h": h})

    def poly(self, pts, c):
        """A filled polygon (the arrows on the placing screen)."""
        self.ops.append({"o": "p", "pts": [[round(x, 1), round(y, 1)] for x, y in pts], "c": c})

    def text(self, x, y, t, f, z, c, a="l", ls=0, mw=None, bl=None, k=None):
        if not t:
            return
        op = {"o": "t", "x": round(x, 1), "y": y, "t": t, "f": f, "z": z, "c": c, "a": a}
        if ls:
            op["ls"] = ls
        op["mw"] = round(mw if mw is not None else self.width(t, f, z, ls) + 1, 1)
        if bl:
            op["bl"] = bl
        if k:
            op["k"] = k
        self.ops.append(op)

    # -------------------------------------------------------- text fitting
    def width(self, t, f, z, ls=0):
        return self.fonts.width(t, f, z, ls)

    def ellipsize(self, t, f, z, mw, ls=0):
        t = t or ""
        if self.width(t, f, z, ls) <= mw:
            return t
        while t and self.width(t.rstrip() + "…", f, z, ls) > mw:
            t = t[:-1]
        return (t.rstrip(" -_·,") + "…") if t else ""

    def fit(self, t, f, sizes, mw, ls=0):
        """The biggest size at which t fits on one line; the smallest size, shortened, if none."""
        for z in sizes:
            if self.width(t, f, z, ls) <= mw:
                return z, t
        z = sizes[-1]
        return z, self.ellipsize(t, f, z, mw, ls)

    @staticmethod
    def _tokens(t):
        """Words, keeping a break chance after spaces and after - _ / in long names.

        A size on a bit's label stays in one piece: no break inside a fraction
        such as 1/8", and "3.175mm * 12mm" is kept together.
        """
        out, cur = [], ""
        n = len(t)
        for i, ch in enumerate(t):
            cur += ch
            if ch in " -_" or (ch == "/" and not (i + 1 < n and t[i + 1].isdigit())):
                out.append(cur)
                cur = ""
        if cur:
            out.append(cur)
        merged, i = [], 0
        while i < len(out):
            tok = out[i]
            if tok.strip() in ("*", "\u00d7") and merged and i + 1 < len(out):
                merged[-1] = merged[-1] + tok + out[i + 1]
                i += 2
                continue
            merged.append(tok)
            i += 1
        return merged

    def _wrap_raw(self, t, f, z, mw):
        """Greedy line breaking; each line keeps its own text exactly as written."""
        lines, cur = [], ""
        for w in self._tokens(" ".join((t or "").split())):
            if self.width((cur + w).rstrip(), f, z) <= mw:
                cur += w
                continue
            if cur:
                lines.append(cur)
                cur = ""
            while self.width(w.rstrip(), f, z) > mw:      # one word wider than the line
                n = len(w)
                while n > 1 and self.width(w[:n], f, z) > mw:
                    n -= 1
                cut = max(w.rfind(ch, 0, n) for ch in ".:/-_")   # break a web address after . : or /
                if cut > 0:
                    n = cut + 1
                lines.append(w[:n])
                w = w[n:]
            cur = w
        if cur.strip():
            lines.append(cur)
        return lines

    def wrap(self, t, f, z, mw, max_lines, balance=False):
        """Lines of at most mw; the last one shortened with … if the text is too long.

        balance: make the lines about the same length (like CSS text-wrap: balance),
        so a sentence doesn't end with one word on its own line.
        """
        lines = self._wrap_raw(t, f, z, mw)
        if len(lines) > max_lines:
            rest = "".join(lines[max_lines - 1:])
            lines = lines[:max_lines - 1] + [self.ellipsize(rest.strip(), f, z, mw)]
        elif balance and len(lines) > 1:
            n, narrow = len(lines), mw
            while narrow > mw * 0.5:
                trial = self._wrap_raw(t, f, z, narrow - 4)
                if len(trial) != n:
                    break
                lines, narrow = trial, narrow - 4
        return [ln.rstrip() for ln in lines]

    def wrap_fit(self, t, f, sizes, mw, max_lines, balance=False):
        """The biggest size at which t wraps into max_lines without being cut."""
        for z in sizes:
            if len(self._wrap_raw(t, f, z, mw)) <= max_lines:
                return z, self.wrap(t, f, z, mw, max_lines, balance)
        z = sizes[-1]
        return z, self.wrap(t, f, z, mw, max_lines, balance)

    @staticmethod
    def shortened(t, lines) -> bool:
        """True if the lines lost part of t (it was cut and ended with …)."""
        return "".join(lines).replace(" ", "") != " ".join((t or "").split()).replace(" ", "")

    def lines(self, x, y, lines, f, z, c, lh, a="l", mw=None):
        for i, ln in enumerate(lines):
            self.text(x, y + i * lh, ln, f, z, c, a=a, mw=mw)

    # -------------------------------------------------------- screens
    def build(self, v) -> list:
        self.ops = []
        self.hits = []
        v = v if isinstance(v, dict) else {}
        self.demo = bool(v.get("demo"))     # the demo strip takes the top line's place
        mode = v.get("mode")
        if v.get("ui"):
            self._ui(v["ui"], v)
            return self.ops
        if mode == "toolchange" and v.get("toolchange"):
            self._change(v)
        elif mode == "alarm":
            self._alarm(v)
        elif mode in ("running", "paused") and v.get("job"):
            self._run(v)
        elif mode in ("running", "paused", "idle"):
            self._idle(v)
        elif mode == "direct":
            relay = (v.get("relay") or {}).get("name") or "the machine name ending in _screen"
            self._message(v, "Makera Studio is connected straight to the Z1",
                          "This screen can't see the job while it is. In Makera Studio, connect to " + relay +
                          " instead, or disconnect Makera Studio after starting the job and this screen picks "
                          "the job up.")
        elif mode == "connecting":
            name = (v.get("machine") or {}).get("name") or "the machine"
            self._message(v, "Connecting to the Z1", "Found " + name + ". Waiting for it to answer.")
        elif mode == "searching":
            self._message(v, "Looking for the Z1", "Make sure the Z1 is on and on the same Wi-Fi as this screen.")
        elif mode == "_lost":
            self._message(v, "No screen data",
                          "The screen service on this Pi isn't answering. It restarts by itself. If this stays "
                          "for more than a minute, unplug the Pi for 10 seconds and plug it back in.")
        elif mode == "_stopped":
            self._message(v, "Screen stopped", "The Pi is restarting or shutting down.")
        else:
            self._splash()
        if v.get("demo"):
            self._demo_strip(v["demo"])
        return self.ops

    def _header(self, left, right="", color=MUTED, rcolor=None, x=10):
        """The top line: a label on the left, a name on the right."""
        if self.demo:
            return
        self.text(x, 15, left, "b6", 11, color, ls=1)
        if right:
            used = x + self.width(left, "b6", 11, 1) + 12
            self.text(W - 10, 15, self.ellipsize(right, "b5", 11, W - 10 - used), "b5", 11, rcolor or color, a="r")

    # running / paused
    def _run(self, v):
        """The big card is the bit cutting now (its color, and its life filling up), the strip under it the next
        bit, in its color, with the time to that stop."""
        j = v["job"]
        paused = v.get("mode") == "paused"
        self.rect(0, 0, W, H, GROUND)
        if not self.demo:
            self.dot(14, 11, 4, HOLD if paused else GO)
            lab = "PAUSED" if paused else "RUNNING"
            self.text(23, 15, lab, "b6", 11, INK, ls=1)
            x = 23 + self.width(lab, "b6", 11, 1) + 12
            self.text(W - 10, 15, self.ellipsize(j.get("name") or "Job", "b5", 11, W - 10 - x), "b5", 11, MUTED,
                      a="r")

        self._now_card(j, paused, 6, 24, 308, 104)
        self._next_strip(j, 6, 134, 308, 34)

        pct = max(0.0, min(100.0, float(j.get("percent") or 0)))
        self._bar(v, j, pct, 6, 176, 266, 14)
        self.text(W - 6, 189, "%d%%" % int(math.floor(pct)), "d6", 17, INK, a="r")

        known = bool(j.get("known"))
        left = j.get("left")
        cols = [
            (8, "ELAPSED", dur(j.get("elapsed")), "", None),
            (113, "TIME LEFT", dur(left), "done around %s" if left is not None else "", left),
            (218, "CHANGES LEFT", str(j.get("changes_left")) if known and j.get("changes_left") is not None else "-",
             ("of %d in this job" % j.get("changes_total", 0)) if known else "", None),
        ]
        for cx, label, value, sub, secs in cols:
            self.text(cx, 206, label, "b6", 9, MUTED, ls=1)
            z, val = self.fit(value, "d6", [19, 18, 17], 96)
            self.text(cx, 224, val, "d6", z, INK)
            if sub and secs is not None:
                self.text(cx, 236, sub % clock_text(time.time() + secs), "b5", 10, MUTED,
                          mw=96, k={"in": secs, "fmt": sub})
            elif sub:
                self.text(cx, 236, self.ellipsize(sub, "b5", 10, 96), "b5", 10, MUTED)

    def _now_card(self, j, paused, x, y, w, h):
        """The bit in the spindle: its number, its name, which of the bits of its kind it is and how long that bit
        has cut, over a gauge of its expected life (when one is set)."""
        now = j.get("now")
        left, right = x + 10, x + w - 10
        if now:
            bg = now.get("color") or UNSET
            fg = now.get("text") or text_on(bg)
            self.fill(x, y, w, h, now, r=8)
        else:
            bg, fg = PANEL, INK
            self.rect(x, y, w, h, LINE, r=8)
            self.rect(x + 1, y + 1, w - 2, h - 2, PANEL, r=7)
        soft = mix(fg, bg, 0.78)
        probe = bool(now) and now.get("tool") == 0
        bit = (now or {}).get("bit") or {}
        self.text(left, y + 17, ("PAUSED WITH" if paused else "CUTTING NOW") if not probe else "PROBING", "b6", 10,
                  soft, ls=1)
        if bit:
            self.text(right, y + 17, "BIT %s" % bit["label"], "b6", 10, soft, a="r", ls=1)
        if now:
            code = code_text(now)
            name = "Wired probe" if probe else (now.get("label") or "")
            if now.get("unset"):
                sub = "No sticker color set"
            elif bit:
                sub = ("Bit %s · %s of %s used" % (bit["label"], bit.get("used_text", ""), bit_time(bit["limit"]))
                       if bit.get("limit") else "Bit %s · %s used" % (bit["label"], bit.get("used_text", "")))
            else:
                sub = ""
        else:
            code, name, sub = "", "The Z1 hasn't said which bit is in", ""
        gauge = bool(bit.get("limit")) and not (now or {}).get("unset")
        base = y + (70 if gauge else 78)                   # baseline of the big code
        zc, code = self.fit(code, "d7", [58, 52, 46, 40, 34], w * 0.45)
        self.text(left + 2, base, code, "d7", zc, fg)
        tx = (left + 2 + self.width(code, "d7", zc) + 12) if code else left
        tw = right - tx
        zn, nlines = self.wrap_fit(name, "b6", [15, 14, 13], tw, 2, balance=True)
        zs, slines = self.wrap_fit(sub, "b5", [12, 11], tw, 1)
        if self.shortened(name, nlines) and not slines:
            zn, nlines = self.wrap_fit(name, "b6", [14, 13, 12], tw, 3, balance=True)
        lh_n, lh_s = zn + 3, zs + 3
        block = len(nlines) * lh_n + (len(slines) * lh_s + 2 if slines else 0)
        first = max(base - block + lh_n, y + 22 + zn)      # the block's last line on the code's baseline
        self.lines(tx, first, nlines, "b6", zn, fg, lh_n)
        self.lines(tx, first + (len(nlines) - 1) * lh_n + 2 + lh_s, slines, "b5", zs, soft, lh_s)
        if gauge:
            self.rect(left - 1, y + h - 21, right - left + 2, 12, BIT_LINE, r=5)
            self._gauge(bit, left, y + h - 20, right - left, 10)

    def _next_strip(self, j, x, y, w, h):
        """The next bit, in its color: NEXT T3 30° V-bit · bit C ... in 6 min. Else the last bit, or why the next
        one isn't known."""
        nx = j.get("next")
        known = bool(j.get("known"))
        left, right = x + 10, x + w - 10
        if nx:
            bg = nx.get("color") or UNSET
            fg = nx.get("text") or text_on(bg)
            self.fill(x, y, w, h, nx, r=7)
        else:
            bg, fg = PANEL, INK
            self.rect(x, y, w, h, LINE, r=7)
            self.rect(x + 1, y + 1, w - 2, h - 2, PANEL, r=6)
        soft = mix(fg, bg, 0.78)
        mid = y + h / 2.0
        if not nx and not known:
            text = ("Reading the job file. One moment." if j.get("loading") else
                    "Next bit not known for this file. Press Select on it in Makera Studio once.")
            zt, tl = self.wrap_fit(text, "b5", [12, 11, 10], w - 20, 2, balance=True)
            self.lines(left, mid + zt * 0.36 - (len(tl) - 1) * (zt + 2) / 2.0, tl, "b5", zt, INK, zt + 2)
            return
        word = "NEXT" if nx else "LAST BIT"
        secs = nx.get("in") if nx else j.get("left")
        when = ("in " if nx else "done in ") + dur(secs)
        self.text(left, mid + 9 * 0.36, word, "b6", 9, soft, ls=1)
        lx = left + self.width(word, "b6", 9, 1) + 8
        zw, when = self.fit(when, "d7", [18, 16, 14], 110)
        ww = self.width(when, "d7", zw)
        self.text(right, mid + self.fonts.cap("d7", zw) / 2.0, when, "d7", zw, fg, a="r")
        if not nx:
            self.text(lx, mid + 12 * 0.36, self.ellipsize("No more tool changes", "b6", 12, right - lx - ww - 12),
                      "b6", 12, fg)
            return
        code = code_text(nx)
        zc, code = self.fit(code, "d7", [18, 16, 14], 70)
        self.text(lx, mid + self.fonts.cap("d7", zc) / 2.0, code, "d7", zc, fg)
        lx += self.width(code, "d7", zc) + 10
        nb = nx.get("bit")
        what = "No sticker color set" if nx.get("unset") else \
            (nx.get("label") or "") + (" · bit %s" % nb["label"] if nb else "")
        if nx.get("unset") and nx.get("label"):
            what = nx["label"] + " · no color set"
        self.text(lx, mid + 12 * 0.36, self.ellipsize(what, "b6", 12, right - lx - ww - 12), "b6", 12, fg)

    def _bar(self, v, j, pct, x, y, w, h):
        segs = []
        nx = j.get("next")
        for s in j.get("segments") or []:
            c = s.get("color") or UNSET
            rest = mix(c, LINE, 0.32)
            label = ("P" if s.get("tool") == 0 else s.get("code") or "") if (s["end"] - s["start"]) * w >= 18 else ""
            if label and self.width(label, "d7", 11) + 6 > (s["end"] - s["start"]) * w:
                label = ""
            lc = (nx.get("text") if nx and nx.get("tool") == s.get("tool") else None) or text_on(c)
            segs.append({"a": round(s["start"], 4), "b": round(s["end"], 4), "c": c, "q": rest, "l": label,
                         "lc": lc, "lq": mix(text_on(rest), rest, 0.7), "u": bool(s.get("unset"))})
        self.ops.append({"o": "bar", "x": x, "y": y, "w": w, "h": h, "r": 4, "track": LINE, "segs": segs,
                         "p": round(pct, 2), "plain": MUTED, "m": INK, "g": GROUND, "f": "d7", "z": 11})

    # tool change
    def _change(self, v):
        tc = v["toolchange"]
        bg = tc.get("color") or UNSET
        fg = tc.get("text") or text_on(bg)
        self.fill(0, 0, W, H, tc, p=32)
        soft = mix(fg, bg, 0.8)
        probe = tc.get("tool") == 0
        count = "TOOL CHANGE %d OF %d" % (tc["index"], tc["total"]) if tc.get("index") and tc.get("total") else "TOOL CHANGE"
        job = (v.get("job") or {}).get("name") or ""
        self._header(count, job, color=soft)

        bit = None if probe else tc.get("bit")
        if probe:
            press = "Put the probe in, plug its cable into the port on the side of the head, then press the front button once."
        elif bit and bit.get("label"):
            press = "Put bit %s in, then press the front button once." % bit["label"]
        else:
            press = "Put this bit in, then press the front button once."
        after = tc.get("then")
        if after:
            after_text = "After this: " + code_text(after) + (" " + after["label"] if after.get("label") else "")
            if after.get("in") is not None:
                after_text += " in about " + dur(after["in"])
        elif v.get("job") and v["job"].get("known"):
            after_text = "Last bit of the job"
        else:
            after_text = ""

        # the next bit's name: one line if it fits, else two, else a shorter wording
        za, alines = 12, []
        if after_text:
            za, alines = self.wrap_fit(after_text, "b5", [12, 11], W - 20, 1)
            if self.shortened(after_text, alines):
                za, alines = self.wrap_fit(after_text, "b5", [12, 11, 10], W - 20, 2, balance=True)
            if self.shortened(after_text, alines) and after and after.get("in") is not None:
                short = "Next: " + code_text(after) + (" " + after["label"] if after.get("label") else "") + \
                        ", in " + dur(after["in"])
                za, alines = self.wrap_fit(short, "b5", [12, 11, 10], W - 20, 2, balance=True)
        la = za + 3
        extra = (len(alines) - 1) * la if alines else 0
        zp, plines = self.wrap_fit(press, "b7", [15, 14, 13], W - 20, 3, balance=True)
        lh = zp + 4
        last = (H - 26 - extra) if alines else (H - 12)
        first = last - (len(plines) - 1) * lh
        self.lines(10, first, plines, "b7", zp, fg, lh)
        self.lines(10, H - 9 - extra, alines, "b5", za, soft, la)
        div = first - zp - 9
        self.rect(10, div, W - 20, 2, fg)

        load_word = "LOAD THE PROBE" if probe else "LOAD"
        self.text(10, 47, load_word, "d7", 24, fg, ls=2, bl=mix(fg, bg, 0.45))
        if tc.get("out"):
            self._out_chip(tc["out"], 10 + self.width(load_word, "d7", 24, 2) + 14, fg)
        base = div - 11
        if bit:
            base = self._bit_line(bit, tc["tool"], div - 40, fg, bg) - 11
        top = 47 + 9
        code = "Probe" if probe else (tc.get("code") or "-")
        name = "Wired probe" if probe else (tc.get("label") or "")
        if probe:
            sub = ""
        elif tc.get("unset"):
            url = short_url(v.get("settings_url") or "")
            sub = "No sticker color set. Choose one at " + url if url else "No sticker color set"
        else:
            sub = ""                                     # the screen is the sticker's color
        cap_room = base - top
        sizes = [z for z in (120, 112, 104, 96, 88, 80, 72, 64, 56) if self.fonts.cap("d7", z) <= cap_room] or [56]
        room_for_text = 190 if len(sub) > 30 else 110   # a long note next to the code needs a wider column
        if self.width(code, "d7", sizes[-1]) > W - 20 - room_for_text:
            room_for_text = 100                          # a word such as "3D probe" gets more of the line
            sizes = sizes + [z for z in (48, 40) if z < sizes[-1]]
        zc, code = self.fit(code, "d7", sizes, W - 20 - room_for_text)
        self.text(10, base, code, "d7", zc, fg)
        tx = 10 + self.width(code, "d7", zc) + 14
        tw = W - 10 - tx
        zn, nlines = self.wrap_fit(name, "b6", [18, 17, 16, 15, 14], tw, 3, balance=True)
        zs, slines = self.wrap_fit(sub, "b5", [14, 13, 12, 11], tw, 4, balance=True)
        lh_n, lh_s = zn + 3, zs + 3
        block = len(nlines) * lh_n + (len(slines) * lh_s + 3 if slines else 0)
        first_n = base - block + lh_n
        if first_n < top + zn:
            first_n = top + zn
        self.lines(tx, first_n, nlines, "b6", zn, fg, lh_n)
        self.lines(tx, first_n + (len(nlines) - 1) * lh_n + 3 + lh_s, slines, "b5", zs, fg, lh_s)

    def _bit_line(self, bit, slot, y, fg, bg):
        """Which of the bits of this kind to put in and how long it has cut ("Bit B · 2 h 10 used"), over a gauge
        of its expected life that fills green, then yellow, then red; the lighter part is what this stretch of
        the job adds. No expected life set: an empty gauge. A tap picks the next bit of the kind (the record
        only; the Z1 is not touched). Returns the top of the line."""
        h, gh = 32, 9
        self.rect(10, y, W - 20, h, mix(fg, bg, 0.6), r=6)
        self.rect(11, y + 1, W - 22, h - 2, BIT_LINE, r=5)
        tap = "TAP: ANOTHER" if (bit.get("choices") or 1) > 1 else ""
        tw = self.width(tap, "b7", 9, 0.6) if tap else 0
        z, t = self.fit(bit.get("text") or "", "b6", [13, 12, 11, 10], W - 40 - tw - (10 if tap else 0))
        ty = y + 4 + z
        self.text(18, ty, t, "b6", z, "#FFFFFF")
        if tap:
            self.text(W - 18, ty - 1, tap, "b7", 9, "#D8D4CC", a="r", ls=0.6)
            self.hits.append((10, y, W - 20, h, ("next_bit", slot)))
        self._gauge(bit, 18, y + h - gh - 4, W - 36, gh)
        return y

    def _gauge(self, bit, x, y, w, h):
        """A bit's expected life: filled green, then yellow, then red; lighter, what this stretch of the job adds.
        Empty while no expected life is set."""
        self.rect(x, y, w, h, GAUGE_TRACK, r=h // 3)
        limit = bit.get("limit")
        if limit:
            used = float(bit.get("used") or 0) / limit
            then = used + float(bit.get("job_adds") or 0) / limit
            if then > used and used < 1:
                self.rect(x, y, max(h, int(w * min(1.0, then))), h, mix(life_color(then), BIT_LINE, 0.45), r=h // 3)
            self.rect(x, y, max(h, int(w * min(1.0, used))), h, life_color(used), r=h // 3)

    def _out_chip(self, out, x_min, edge):
        """The bit coming out, right of LOAD: a box in the color of the container it goes back into,
        for example T1 BACK IN RED. edge outlines it so it stands out on any screen color."""
        pad, h, y = 8, 22, 27
        room = W - 10 - x_min
        words = out_words(out)
        pick = None
        for text in words:
            z = next((z for z in (13, 12, 11, 10) if self.width(text, "b7", z, 0.5) + 2 * pad <= room), None)
            if z is not None:
                pick = (text, z)
                break
        if pick is None:                    # a very long sticker name: shortened, the box still shows the color
            pick = (self.ellipsize(words[-1], "b7", 10, room - 2 * pad, 0.5), 10)
        text, z = pick
        w = int(self.width(text, "b7", z, 0.5) + 2 * pad + 1)
        x = W - 10 - w
        self.rect(x - 2, y - 2, w + 4, h + 4, edge, r=7)
        self.fill(x, y, w, h, out, r=5, p=12)
        self.text(x + pad, y + h / 2.0 + z * 0.36, text, "b7", z,
                  out.get("text") or text_on(out.get("color") or UNSET), ls=0.5)

    # machine stopped
    def _alarm(self, v):
        self.rect(0, 0, W, H, STOP)
        self.rect(6, 6, W - 12, H - 12, GROUND)
        why = v.get("alarm") or "Machine stopped"
        low = why.lower()
        helptext = PROBE_HELP if "probe" in low else HAND_HELP if "by hand" in low else ALARM_HELP
        self.text(16, 27, "BITCUE", "b6", 10, MUTED, ls=1)
        self.text(16, 68, "Machine stopped", "d7", 40, STOP)
        zw, wl = self.wrap_fit(why, "b6", [19, 17, 15], W - 32, 2, balance=True)
        y = 68 + 8 + zw
        self.lines(16, y, wl, "b6", zw, INK, zw + 4)
        y += (len(wl) - 1) * (zw + 4) + 8
        room = (H - 14) - y
        for zh in (13, 12, 11):
            n = max(1, int(room // (zh + 4)))
            hl = self.wrap(helptext, "b5", zh, W - 32, 99)
            if len(hl) <= n:
                break
        hl = self.wrap(helptext, "b5", zh, W - 32, max(1, int(room // (zh + 4))))
        self.lines(16, y + zh + 4, hl, "b5", zh, MUTED, zh + 4)

    # ready
    def _idle(self, v):
        self.rect(0, 0, W, H, GROUND)
        self._header("BITCUE", (v.get("machine") or {}).get("name") or "")
        lj = v.get("last_job")
        if lj:
            self.text(9, 80, "Ready", "d7", 62, INK)
            self.rect(6, 92, 308, 58, PANEL, r=8)
            self.text(16, 107, "LAST JOB FINISHED" if lj.get("done") else "LAST JOB STOPPED", "b6", 9, MUTED, ls=1)
            self.text(16, 125, self.ellipsize(lj.get("name") or "Job", "b6", 14, 288), "b6", 14, INK)
            first = ("Took " + dur(lj.get("elapsed"))) if lj.get("done") else (lj.get("reason") or "Stopped")
            first = self.ellipsize(first, "b5", 11, 288 - self.width(" · ended at 12:59 PM", "b5", 11))
            if lj.get("ended"):
                fmt = first.replace("%", "%%") + " · ended at %s"
                self.text(16, 142, fmt % clock_text(lj["ended"]), "b5", 11, MUTED, mw=288,
                          k={"at": lj["ended"], "fmt": fmt, "tag": "ended"})
            else:
                self.text(16, 142, first, "b5", 11, MUTED)
            y = 172
        else:
            # Ready, the Studio name and the settings address, as one block in the middle
            relay_, url_ = (v.get("relay") or {}).get("name"), v.get("settings_url")
            cap = self.fonts.cap("d7", 72)
            height = cap + (40 if relay_ else 0) + (40 if url_ else 0) + 4
            base = int(22 + (H - 22 - height) / 2.0 + cap)
            self.text(8, base, "Ready", "d7", 72, INK)
            y = base + 34
        touch = v.get("touch_home")
        tw = (W - 20 - 112) if touch else (W - 20)       # leave room for the JOBS button
        relay = (v.get("relay") or {}).get("name")
        if relay:
            self.text(10, y, "In Makera Studio, connect to", "b5", 11, MUTED)
            self.text(10, y + 17, self.ellipsize(relay, "b6", 13, tw if y + 17 > 186 else W - 20), "b6", 13, INK)
            y += 40
        url = short_url(v.get("settings_url") or "")
        if url:
            self.text(10, y, "Bit colors and a practice run", "b5", 11, MUTED)
            self.text(10, y + 17, self.ellipsize(url, "b6", 13, tw if y + 17 > 186 else W - 20), "b6", 13, INK)
        if touch == "ready":
            self.button(W - 6 - 104, H - 6 - 40, 104, 40, "JOBS", ("jobs",), "primary")
        elif touch == "setup":
            self.button(W - 6 - 104, H - 6 - 40, 104, 40, "SET UP TOUCH", None, "normal", size=11)

    # messages
    def _splash(self):
        """Starting: the name over a strip in the bits' sticker colors, "Starting…", and the maker's logo."""
        self.rect(0, 0, W, H, GROUND)
        z, base = 58, 92
        tw = self.width("BitCue", "d7", z)
        self.text(W / 2.0, base, "BitCue", "d7", z, INK, a="m")
        seg = tw / len(SPLASH_BITS)
        for i, c in enumerate(SPLASH_BITS):
            self.rect(int((W - tw) / 2.0 + i * seg + 2), base + 9, int(seg - 4), 4, c, r=2)
        self.text(W / 2.0, base + 34, "Starting…", "b5", 12, MUTED, a="m")
        self.logo((W - LOGO_W) / 2.0, H - 14 - LOGO_H, LOGO_W, LOGO_H)

    def _message(self, v, title, text):
        self.rect(0, 0, W, H, GROUND)
        self._header("BITCUE")
        url = short_url(v.get("settings_url") or "")
        foot = H - 10
        bottom = foot - (20 if url else 4)
        zt, tl = self.wrap_fit(title, "d7", [36, 32, 28, 26, 24], W - 20, 3, balance=True)
        lt = int(zt * 1.02)
        cap = self.fonts.cap("d7", zt)
        title_h = cap + (len(tl) - 1) * lt
        room = bottom - (30 + title_h + 10)
        for zb in (14, 13, 12, 11):
            bl = self.wrap(text, "b5", zb, W - 20, 99)
            if len(bl) * (zb + 5) <= room:
                break
        bl = self.wrap(text, "b5", zb, W - 20, max(1, int(room // (zb + 5))))
        height = title_h + 10 + len(bl) * (zb + 5) + 3
        top = max(30, int(24 + (bottom - 24 - height) / 2.0))     # the block in the middle
        y = top + cap
        self.lines(10, y, tl, "d7", zt, INK, lt)
        y += (len(tl) - 1) * lt + 10
        self.lines(10, y + zb + 5, bl, "b5", zb, MUTED, zb + 5)
        if url:
            self.text(10, foot, self.ellipsize("Settings: " + url, "b5", 10, W - 20), "b5", 10, DIM)

    # ------------------------------------------------------------ touch screens
    def button(self, x, y, w, h, label, action, kind="normal", size=13, fill=None):
        """A button; registered as a touch target unless it is disabled."""
        bg = {"primary": GO, "danger": STOP, "disabled": "#2A2724"}.get(kind, PANEL)
        fg = {"primary": "#0D1A10", "danger": "#1A0705", "disabled": DIM}.get(kind, INK)
        if kind == "normal":
            self.rect(x, y, w, h, LINE, r=7)
            self.rect(x + 1, y + 1, w - 2, h - 2, bg, r=6)
        else:
            self.rect(x, y, w, h, bg, r=7)
        if fill:
            self.rect(x, y, max(14, int(w * fill)), h, "#9BE3A6", r=7)
        z, t = self.fit(label, "b7", [size, size - 1, size - 2, 10], w - 12, 0.5)
        self.text(x + w / 2.0, y + h / 2.0 + self.fonts.cap("b7", z) / 2.0, t, "b7", z, fg, a="m", ls=0.5)
        if action and kind != "disabled":
            self.hits.append((x, y, w, h, action))

    def _ui(self, ui, v):
        self.rect(0, 0, W, H, GROUND)
        scr = ui.get("screen")
        if scr == "list":
            self._ui_list(ui)
        elif scr == "plate":
            self._ui_plate(ui)
        elif scr == "place":
            self._ui_place(ui)
        elif scr == "reading":
            self._ui_reading(ui)
        elif scr == "check":
            self._ui_check(ui)
        elif scr == "newplate":
            self._ui_newplate(ui)
        elif scr == "review":
            self._ui_review(ui)
        elif scr == "message":
            self._ui_message(ui)
        elif scr == "calibrate":
            self._ui_calibrate(ui)
        else:   # busy, starting
            text = ui.get("text") or "One moment"
            z, lines = self.wrap_fit(text, "d7", [30, 26, 22], W - 24, 3, balance=True)
            cap = self.fonts.cap("d7", z)
            y = int(H / 2.0 - (len(lines) - 1) * (z + 4) / 2.0 + cap / 2.0) - 8
            self.lines(12, y, lines, "d7", z, INK, z + 4)
            self.text(12, y + (len(lines) - 1) * (z + 4) + 26, "One moment.", "b5", 13, MUTED)

    def _ui_title(self, text, right=""):
        self.text(10, 17, text, "b6", 11, MUTED, ls=1)
        if right:
            self.text(W - 10, 17, right, "b5", 11, MUTED, a="r")

    def _ui_list(self, ui):
        items = ui.get("items") or []
        pages = max(1, (len(items) + 3) // 4)
        page = min(ui.get("page", 0), pages - 1)
        ui["page"] = page
        self._ui_title(self.ellipsize(ui.get("title", ""), "b6", 11, W - 80, 1),
                       ("%d / %d" % (page + 1, pages)) if pages > 1 else "")
        if not items:
            z, lines = self.wrap_fit(ui.get("empty", ""), "b5", [14, 13], W - 20, 5, balance=True)
            self.lines(10, 60, lines, "b5", z, MUTED, z + 5)
        pics = any(it.get("preview") for it in items)
        for i, it in enumerate(items[page * 4: page * 4 + 4]):
            y0 = 26 + i * 42
            self.rect(6, y0, W - 12, 38, PANEL, r=7)
            tx = 16
            if pics:                                  # a column of part pictures, so names line up
                self.rect(10, y0 + 3, 32, 32, GROUND, r=5)
                self.picture(10, y0 + 3, 32, 32, it.get("preview"))
                tx = 50
            self.text(tx, y0 + 17, self.ellipsize(it.get("name", ""), "b6", 14, W - 36 - tx), "b6", 14, INK)
            if it.get("sub"):
                self.text(tx, y0 + 32, self.ellipsize(it["sub"], "b5", 11, W - 36 - tx), "b5", 11, MUTED)
            self.text(W - 18, y0 + 26, "\u203a", "b6", 22, MUTED, a="r")
            self.hits.append((6, y0, W - 12, 38, it.get("action")))
        self.button(6, H - 44, 96, 38, "BACK", ui.get("back") or ("home",))
        if page > 0:
            self.button(W - 6 - 108, H - 44, 52, 38, "UP", ("page", -1))
        if page < pages - 1:
            self.button(W - 6 - 52, H - 44, 52, 38, "DOWN", ("page", 1))

    def _plate_base(self, geom, occ_runs=(), started_runs=()):
        """The plate to scale, with the areas already cut on it. Returns its left, top, width and height."""
        gx, gy, s, pw, ph = geom
        wpx, hpx = int(round(pw * s)), int(round(ph * s))
        x0, y0 = int(round(gx)), int(round(gy))
        self.rect(x0 - 2, y0 - 2, wpx + 4, hpx + 4, PLATE_EDGE, r=3)
        self.rect(x0, y0, wpx, hpx, PLATE)
        for x, y, w, h in occ_runs or []:
            self.rect(x, y, w, h, CUT_AREA)
        for x, y, w, h in started_runs or []:
            self.rect(x, y, w, h, CUT_STARTED)
        return x0, y0, wpx, hpx

    def _ui_plate(self, ui):
        d = ui.get("data") or {}
        plate = d.get("plate") or {"w": 200.0, "h": 200.0}
        pw, ph = float(plate["w"]), float(plate["h"])
        self._ui_title("PICK A SPOT ON THE PLATE")
        geom = d.get("geom") or [10.0, 26.0, min(184.0 / pw, 184.0 / ph), pw, ph]
        scale = geom[2]
        x0, y0, wpx, hpx = self._plate_base(geom, d.get("occ_runs"), d.get("started_runs"))
        self.text(x0 + wpx / 2.0, y0 + hpx + 13, "FRONT", "b6", 9, MUTED, a="m", ls=1.5)
        self.text(x0 - 1, y0 + hpx + 13, "A1", "b7", 9, GO)                # the Anchor 1 corner
        sel = ui.get("selected")
        for sp in d.get("spots") or []:
            cx = geom[0] + float(sp["cx"]) * scale
            cy = geom[1] + (ph - float(sp["cy"])) * scale
            r = int(max(14, min(40, float(sp["d"]) * scale / 2.0 * 0.8)))   # a little smaller than the part
            state = sp.get("state", "free")
            if sp["key"] == sel:
                ring, body, ink = "#D8F5DD", GO, "#0D1A10"
            elif state == "cut":
                ring, body, ink = STOP, "#4A2622", "#FFB3AC"
            elif state in ("started", "taken"):
                ring, body, ink = HOLD, "#4A3A1E", "#F3D58A"
            elif sp.get("mine") and not sp.get("why"):
                ring, body, ink = INK, "#2B2925", INK
            else:
                ring, body, ink = "#7A6A45", "#4A3F25", "#A8986C"
            self.dot(cx, cy, r, ring)
            self.dot(cx, cy, r - 2, body)
            self.text(cx, cy + 6, str(sp["n"]), "d7", 22, ink, a="m")
            if state != "free" and sp["key"] != sel:
                word = {"cut": "CUT", "started": "PART", "taken": "IN USE"}.get(state, "")
                self.text(cx, cy + 18, word, "b7", 8, ink, a="m", ls=1)
            self.hits.append((cx - r, cy - r, 2 * r, 2 * r, ("spot", sp["key"])))
        rx = 206
        rw = W - 6 - rx
        z, nl = self.wrap_fit(d.get("name", ""), "b6", [15, 14, 13], rw, 2, balance=True)
        self.lines(rx, 40, nl, "b6", z, INK, z + 3)
        y = 40 + (len(nl) - 1) * (z + 3) + 18
        spot = next((s for s in d.get("spots") or [] if s["key"] == sel), None)
        if spot:
            hint = "Spot %d, %s." % (spot["n"], spot["where"])
            color = GO
        else:
            hint = ui.get("note") or "Touch a free spot. Hold a spot 2 s to mark it cut or free."
            color = HOLD if ui.get("note") else MUTED
        zh, hl = self.wrap_fit(hint, "b5", [12, 11], rw, 6)
        self.lines(rx, y, hl, "b5", zh, color, zh + 4)
        self.button(rx, H - 86, rw, 36, "BACK", ui.get("back") or ("back_folders",))
        self.button(rx, H - 44, rw, 38, "NEXT", ("to_check",) if spot else None, "primary" if spot else "disabled")

    def _arrow(self, x, y, w, h, way, action):
        self.button(x, y, w, h, "", action)
        cx, cy = x + w / 2.0, y + h / 2.0
        pts = {"up": [(cx, cy - 7), (cx - 8, cy + 5), (cx + 8, cy + 5)],
               "down": [(cx, cy + 7), (cx - 8, cy - 5), (cx + 8, cy - 5)],
               "left": [(cx - 7, cy), (cx + 5, cy - 8), (cx + 5, cy + 8)],
               "right": [(cx + 7, cy), (cx - 5, cy - 8), (cx - 5, cy + 8)]}[way]
        self.poly(pts, INK)

    def _ui_place(self, ui):
        """Placing a part: the plate, the cut areas (dark red), the part (green when clear), arrows, NEXT."""
        geom = ui.get("geom") or [6.0, 25.0, 190 / 200.0, 200.0, 200.0]
        self._ui_title("PLACE THE PART")
        x0, y0, wpx, hpx = self._plate_base(geom, ui.get("occ_runs"), ui.get("started_runs"))
        ok = bool(ui.get("ok"))
        for x, y, w, h in ui.get("part_runs") or []:
            self.rect(x, y, w, h, GO if ok else HOLD)
        for x, y, w, h in ui.get("bad_runs") or []:
            self.rect(x, y, w, h, STOP)
        lv = ui.get("level_px")
        if lv:
            (ax, ay), (bx, by) = lv
            lx, ly, lw, lh = int(min(ax, bx)), int(min(ay, by)), int(abs(bx - ax)), int(abs(by - ay))
            for rx, ry, rw, rh in ((lx, ly, lw, 1), (lx, ly + lh, lw + 1, 1), (lx, ly, 1, lh), (lx + lw, ly, 1, lh)):
                self.rect(rx, ry, rw, rh, INK)
        pp = ui.get("probe_px")
        if pp:
            pc = STOP if ui.get("code") == "probe" else INK
            px, py = int(round(pp[0])), int(round(pp[1]))
            self.rect(px - 6, py, 13, 1, pc)
            self.rect(px, py - 6, 1, 13, pc)
            self.dot(px + 0.5, py + 0.5, 2, pc)
        # the plate is one touch target: touch or drag to move the part's middle there
        self.hits.append((x0, y0, wpx, hpx, ("plate_area",)))
        base = y0 + hpx + 14
        self.text(x0 - 1, base, "A1", "b7", 9, GO)
        self.text(x0 + wpx / 2.0, base, "FRONT", "b6", 9, MUTED, a="m", ls=1.5)
        off = ui.get("offset") or [0, 0]
        self.text(x0 + wpx, base, "X%s Y%s" % (_mm(off[0]), _mm(off[1])), "b6", 10, INK, a="r")
        # right column: name, arrows, status, BACK and NEXT
        rx, rw = 202, W - 6 - 202
        self.text(rx, 38, self.ellipsize(ui.get("title", ""), "b6", 13, rw), "b6", 13, INK)
        cw, ch, gap = 36, 34, 2
        ys = [44, 44 + ch + gap, 44 + 2 * (ch + gap)]
        xs = [rx, rx + cw + gap, rx + 2 * (cw + gap)]
        self._arrow(xs[1], ys[0], cw, ch, "up", ("nudge", 0, 1))
        self._arrow(xs[0], ys[1], cw, ch, "left", ("nudge", -1, 0))
        self.button(xs[1], ys[1], cw, ch, "", ("step",))         # the step: tap for 0.5, 1 or 5 mm
        step = str(ui.get("step", "1 mm")).replace(" mm", "")
        self.text(xs[1] + cw / 2.0, ys[1] + 18, step, "b7", 14 if len(step) < 3 else 12, INK, a="m")
        self.text(xs[1] + cw / 2.0, ys[1] + 28, "MM", "b6", 8, MUTED, a="m", ls=1)
        self._arrow(xs[2], ys[1], cw, ch, "right", ("nudge", 1, 0))
        self._arrow(xs[1], ys[2], cw, ch, "down", ("nudge", 0, -1))
        code = ui.get("code") or ""
        word = PLACE_WORDS.get(code, "CHECK")
        self.text(rx, 165, self.ellipsize(word, "b7", 12, rw, 0.5), "b7", 12, GO if ok else STOP, ls=0.5)
        if ui.get("note"):
            sub = ui["note"]
        elif ok:
            near = ui.get("near")
            sub = ("Nearest cut %s mm" % _mm(near)) if near is not None else "Nothing cut within 6 mm"
        else:
            sub = PLACE_HELP.get(code, ui.get("text", ""))
        zs, sl = self.wrap_fit(sub, "b5", [11, 10], rw, 2)
        self.lines(rx, 179, sl, "b5", zs, MUTED, zs + 2)
        self.button(rx, H - 36, 52, 32, "BACK", ui.get("back") or ("back_items",), size=11)
        self.button(rx + 56, H - 36, rw - 56, 32, "NEXT", ("to_check",) if ok else None,
                    "primary" if ok else "disabled", size=12)

    def _ui_reading(self, ui):
        self._ui_title("READING FROM THE Z1")
        z, tl = self.wrap_fit(ui.get("title", ""), "d7", [30, 26, 22], W - 20, 2, balance=True)
        self.lines(10, 62, tl, "d7", z, INK, z + 2)
        y = 62 + (len(tl) - 1) * (z + 2) + 26
        pct = max(0, min(100, int(ui.get("pct") or 0)))
        self.rect(10, y, W - 20, 14, PANEL, r=7)
        if pct:
            self.rect(10, y, max(14, int((W - 20) * pct / 100.0)), 14, GO, r=7)
        text = ("Checking the file's cuts" if ui.get("profiling") else "%d%%" % pct)
        self.text(10, y + 34, text, "b6", 14, INK)
        zb, bl = self.wrap_fit("The Pi reads the file once to find where it cuts. Larger files take a minute.",
                               "b5", [12, 11], W - 20, 3)
        self.lines(10, y + 56, bl, "b5", zb, MUTED, zb + 4)
        self.button(6, H - 44, 96, 38, "BACK", ui.get("back") or ("back_items",))

    def _expects_box(self, ex, y0, w=None, sizes=(13, 12, 11)):
        """The warning with what the job expects (material, size, thickness): amber, red when the plate on record
        differs. Returns the y below it."""
        w = w or W - 12
        lines_in = [ex.get("text") or ""] + ([ex["detail"]] if ex.get("detail") else [])
        for z in sizes:
            wrapped = [self.wrap(t, "b6", z, w - 20, 3) for t in lines_in]
            if sum(len(o) for o in wrapped) <= 4:
                break
        n = sum(len(o) for o in wrapped)
        hdr = "CHECK: THE PLATE ON RECORD DIFFERS" if ex.get("differs") else "CHECK THE PLATE"
        h = 14 + n * (z + 3) + 8
        c = STOP if ex.get("differs") else HOLD
        self.rect(6, y0, w, h, c, r=7)
        self.text(16, y0 + 12, hdr, "b7", 9, "#151412", ls=0.6)
        y = y0 + 14 + z
        for o in wrapped:
            self.lines(16, y, o, "b6", z, "#151412", z + 3)
            y += len(o) * (z + 3)
        return y0 + h

    def _ui_check(self, ui):
        self._ui_title("BEFORE YOU START")
        lines = ui.get("lines") or []
        if ui.get("expects"):
            top = self._expects_box(ui["expects"], 26) + 6
        else:
            self.text(10, 46, "Check the plate", "d7", 26, INK)
            top = 52
        room = H - top - 60
        for z in (13, 12, 11, 10):
            out = []
            for t in lines:
                out.append(self.wrap(t, "b5", z, W - 20, 6))
            n = sum(len(o) for o in out)
            if n * (z + 4) + (len(out) - 1) * 4 <= room:
                break
        y = top + z + 4
        for o in out:
            self.lines(10, y, o, "b5", z, INK if o is out[0] else MUTED, z + 4)
            y += len(o) * (z + 4) + 4
        self.button(6, H - 44, 76, 38, "BACK", ui.get("back") or ("back_plate",))
        self.button(88, H - 44, 104, 38, "NEW PLATE", ("new_plate",), size=11)
        self.button(W - 6 - 116, H - 44, 116, 38, "PLATE CHECKED", ("checked",), "primary", size=12)

    def _ui_newplate(self, ui):
        """NEW PLATE: the size and thickness boxes (tap one, type on the pad), START NEW PLATE, and LAST PLATE when
        there is an earlier record to bring back."""
        self._ui_title("NEW PLATE")
        vals, labels = ui.get("vals") or {}, ui.get("labels") or {}
        for i, f in enumerate(("w", "h", "t")):
            x, y, w, h = 6, 26 + i * 38, 152, 34
            on = ui.get("field") == f
            self.rect(x, y, w, h, GO if on else LINE, r=7)
            self.rect(x + (2 if on else 1), y + (2 if on else 1), w - (4 if on else 2), h - (4 if on else 2), PANEL, r=6)
            self.text(x + 9, y + 13, labels.get(f, f.upper()), "b6", 9, MUTED, ls=1)
            v = vals.get(f) or ""
            col = GO if (on and ui.get("fresh")) else INK
            self.text(x + w - 30, y + 28, v, "d7", 22, col, a="r")
            if on and not ui.get("fresh"):
                self.rect(x + w - 28, y + 9, 2, 21, INK)             # the cursor after the number typed so far
            self.text(x + w - 9, y + 28, "MM", "b6", 9, MUTED, a="r", ls=1)
            self.hits.append((x, y, w, h, ("np_field", f)))
        zn, nl = self.wrap_fit(ui.get("note") or "", "b5", [11, 10], 150, 4)
        self.lines(8, 140 + zn, nl, "b5", zn, HOLD if ui.get("warn") else MUTED, zn + 3)
        for r, row in enumerate((("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3"), (".", "0", "del"))):
            for c, k in enumerate(row):
                self.button(166 + c * 51, 26 + r * 42, 46, 38, "DEL" if k == "del" else k, ("np_key", k),
                            size=12 if k == "del" else 17)
        self.button(6, H - 44, 70, 38, "BACK", ui.get("back") or ("back_check",))
        if ui.get("last"):
            self.button(82, H - 44, 110, 38, "LAST PLATE", ("last_plate",), size=12)
        self.button(W - 6 - 116, H - 44, 116, 38, "START NEW PLATE", ("np_start",), "primary", size=11)

    def _ui_review(self, ui):
        self._ui_title("READY TO START")
        tw = W - 20
        if ui.get("preview"):                         # the part, so it is easy to see it is the right job
            self.rect(W - 98, 26, 92, 92, PANEL, r=8)
            self.picture(W - 96, 28, 88, 88, ui["preview"])
            tw = W - 116
        z, tl = self.wrap_fit(ui.get("title", ""), "b6", [16, 15, 14], tw, 2, balance=True)
        self.lines(10, 40, tl, "b6", z, INK, z + 3)
        y = 40 + (len(tl) - 1) * (z + 3) + 20
        ex = ui.get("expects")
        rows = []                                      # (text, color): the plate the job expects first, again
        if ex and ex.get("text"):
            rows.append((ex["text"] + ((" " + ex["detail"]) if ex.get("detail") else ""),
                         STOP if ex.get("differs") else HOLD, "b6"))
        rows += [(t, HOLD, "b6") for t in ui.get("bit_warnings") or []]
        rows += [(t, MUTED, "b5") for t in ui.get("lines") or []]
        bottom = H - 52 - (24 if (ui.get("why") or ui.get("note")) else 0)
        for zl in (12, 11, 10):                         # smaller letters rather than text over the buttons
            laid, yy = [], y
            for t, c, f in rows:
                ll = self.wrap(t, f, zl, tw if yy - zl < 120 else W - 20, 3)   # beside the picture, or below it
                laid.append((ll, c, f, yy))
                yy += len(ll) * (zl + 3) + 2
            if yy - zl <= bottom:
                break
        for ll, c, f, yy in laid:
            if yy > bottom:
                break
            self.lines(10, yy, ll, f, zl, c, zl + 3)
        why = ui.get("why")
        note = ui.get("note")
        if why or note:
            zw, wl = self.wrap_fit(why or note, "b6", [12, 11], W - 20, 2)
            self.lines(10, H - 52 - (len(wl) - 1) * (zw + 3), wl, "b6", zw, STOP if why else HOLD, zw + 3)
        self.button(6, H - 44, 86, 38, "BACK", ui.get("back") or ("back_check",))
        hold = float(ui.get("hold") or 0)
        if why:
            self.button(98, H - 44, W - 104, 38, "CAN'T START", None, "disabled")
        else:
            self.button(98, H - 44, W - 104, 38, "KEEP HOLDING" if hold > 0 else "HOLD TO START",
                        ("hold_start",), "primary", size=14, fill=hold if hold > 0 else None)

    def _ui_message(self, ui):
        z, tl = self.wrap_fit(ui.get("title", ""), "d7", [30, 26, 22], W - 20, 2, balance=True)
        self.lines(10, 44, tl, "d7", z, INK, z + 2)
        y = 44 + (len(tl) - 1) * (z + 2) + 24
        zb, bl = self.wrap_fit(ui.get("text", ""), "b5", [14, 13, 12], W - 20, 6, balance=True)
        self.lines(10, y, bl, "b5", zb, MUTED, zb + 5)
        self.button(W / 2.0 - 60, H - 46, 120, 40, ui.get("button") or "OK", ("ok",), "primary")

    def _ui_calibrate(self, ui):
        tx, ty = ui.get("target") or (40, 40)
        self.dot(tx, ty, 14, "#3A1512")
        self.rect(tx - 13, ty, 27, 1, STOP)
        self.rect(tx, ty - 13, 1, 27, STOP)
        self.dot(tx, ty, 4, STOP)
        z, lines = self.wrap_fit(ui.get("text", ""), "b6", [15, 14, 13], W - 120, 4, balance=True)
        self.text(W / 2.0, 98, "TOUCH SCREEN SET UP", "b6", 10, MUTED, a="m", ls=1)
        for i, ln in enumerate(lines):
            self.text(W / 2.0, 122 + i * (z + 4), ln, "b6", z, INK, a="m")

    def _demo_strip(self, d):
        """Across the top of every screen while the demo or a color check plays."""
        self.rect(0, 0, W, 22, "#000000")
        check = d.get("kind") == "check"
        badge = "COLOR CHECK" if check else "DEMO"
        bw = self.width(badge, "b7", 10, 1.5) + 10
        self.rect(6, 4, int(math.ceil(bw)), 14, "#FFD600", r=3)
        self.text(11, 15, badge, "b7", 10, "#000000", ls=1.5)
        ends = ("Ends in " + dur(d["ends_in"])) if d.get("ends_in") is not None else ""
        if ends:
            self.text(W - 8, 15, ends, "b5", 10, "#C9C3B8", a="r")
        x = 6 + bw + 8
        room = W - 8 - x - (self.width(ends, "b5", 10) + 10 if ends else 0)
        self.text(x, 15, self.ellipsize("Not a real job" if check else "Practice run, not the Z1", "b6", 10, room),
                  "b6", 10, "#FFFFFF")


def layout(view, fonts: Fonts) -> list:
    return Layout(fonts).build(view)


def layout_hits(view, fonts: Fonts):
    """The drawing steps and the touch targets of one screen."""
    lay = Layout(fonts)
    ops = lay.build(view)
    return ops, lay.hits


# ---------------------------------------------------------------- painting
def _aa_mask(w, h, r, scale=4):
    """A rounded-corner mask with smooth edges."""
    m = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], radius=r * scale, fill=255)
    return m.resize((w, h), Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.ANTIALIAS)


def _stripe_tile(w, h, c, c1, c2, p):
    tile = Image.new("RGB", (w, h), c)
    d = ImageDraw.Draw(tile)
    half = p / 2.0
    k = -2
    while k * p < w + h + p:
        a = k * p
        d.polygon([(a, 0), (a + half, 0), (a + half - h, h), (a - h, h)], fill=c1)
        d.polygon([(a + half, 0), (a + p, 0), (a + p - h, h), (a + half - h, h)], fill=c2)
        k += 1
    return tile


def _paste_box(img, tile, x, y, r):
    if r:
        img.paste(tile, (int(x), int(y)), _aa_mask(tile.width, tile.height, int(r)))
    else:
        img.paste(tile, (int(x), int(y)))


def _draw_text(d, fonts, op, text, color):
    x, y = op["x"], op["y"]
    a = op.get("a", "l")
    ls = op.get("ls", 0)
    z, mw = op["z"], op.get("mw")
    # drawn bigger (the 3.5" screen) letters come out a little wider than measured at 320 x 240, which adds up
    # over a long line: a size smaller until it fits the room it was laid out in
    while mw and z > 6 and fonts.width(text, op["f"], z, ls) > mw * 1.02 + 1:
        z -= 1
    f = fonts.get(op["f"], z)
    if ls:
        total = fonts.width(text, op["f"], z, ls)
        if a == "r":
            x -= total
        elif a == "m":
            x -= total / 2.0
        for ch in text:
            d.text((x, y), ch, font=f, fill=color, anchor="ls")
            x += f.getlength(ch) + ls
        return
    d.text((x, y), text, font=f, fill=color, anchor={"l": "ls", "r": "rs", "m": "ms"}[a])


def _paint_bar(img, fonts, op, k: int = 1):
    x, y, w, h = int(op["x"]), int(op["y"]), int(op["w"]), int(op["h"])
    px = int(round(w * op["p"] / 100.0))
    segs = op["segs"]
    rest = Image.new("RGB", (w, h), op["track"])
    done = Image.new("RGB", (w, h), op["track"])
    font = fonts.get(op["f"], op["z"])
    if segs:
        for layer, key, lkey in ((rest, "q", "lq"), (done, "c", "lc")):
            d = ImageDraw.Draw(layer)
            for s in segs:
                x0, x1 = int(round(s["a"] * w)), int(round(s["b"] * w))
                if x1 <= x0:
                    continue
                if s.get("u"):
                    layer.paste(_stripe_tile(x1 - x0, h, s[key], mix("#FFFFFF", s[key], 0.10),
                                             mix("#000000", s[key], 0.14), 8 * k), (x0, 0))
                else:
                    d.rectangle([x0, 0, x1 - 1, h - 1], fill=s[key])
                d.rectangle([x1 - 2 * k, 0, x1 - 1, h - 1], fill=op["g"])
                if s.get("l"):
                    d.text((x0 + 4 * k, h // 2 + int(op["z"] * 0.36)), s["l"], font=font, fill=s[lkey],
                           anchor="ls")
    else:
        ImageDraw.Draw(done).rectangle([0, 0, w - 1, h - 1], fill=op["plain"])
    if px > 0:
        rest.paste(done.crop((0, 0, px, h)), (0, 0))
    _paste_box(img, rest, x, y, op.get("r", 0))
    d = ImageDraw.Draw(img)
    mx = x + px
    d.rectangle([mx - 3 * k, y - 5 * k, mx + 3 * k - 1, y + h + 5 * k - 1], fill=op["g"])
    d.rectangle([mx - k, y - 3 * k, mx + k - 1, y + h + 3 * k - 1], fill=op["m"])


_SCALED = {"r": ("x", "y", "w", "h", "r"), "s": ("x", "y", "w", "h", "r", "p"), "c": ("x", "y", "r"),
           "t": ("x", "y", "z", "ls", "mw"), "bar": ("x", "y", "w", "h", "z", "r"), "img": ("x", "y", "w", "h"),
           "logo": ("x", "y", "w", "h")}


def _scaled(op, k: int):
    """A drawing step k times larger (for a screen with k times the pixels each way)."""
    o = dict(op)
    for key in _SCALED.get(op["o"], ()):
        if o.get(key):
            o[key] = o[key] * k
    if op["o"] == "p":
        o["pts"] = [(float(x) * k, float(y) * k) for x, y in op["pts"]]
    return o


def paint(ops, fonts: Fonts, clock: float | None = None, h24: bool = False, blink_dim: bool = False,
          scale: int = 1):
    """Draw the steps from layout() into a 320 x 240 picture (or scale times that, drawn at that size, for a
    640 x 480 screen: the same screen with sharp letters)."""
    clock = time.time() if clock is None else clock
    zoom = max(1, int(scale or 1))
    img = Image.new("RGB", (W * zoom, H * zoom), GROUND)
    d = ImageDraw.Draw(img)
    for op in ops:
        if zoom > 1:
            op = _scaled(op, zoom)
        o = op["o"]
        if o == "r":
            x, y, w, h = int(op["x"]), int(op["y"]), int(op["w"]), int(op["h"])
            if op.get("r"):
                _paste_box(img, Image.new("RGB", (w, h), op["c"]), x, y, op["r"])
            else:
                d.rectangle([x, y, x + w - 1, y + h - 1], fill=op["c"])
        elif o == "s":
            tile = _stripe_tile(int(op["w"]), int(op["h"]), op["c"], op["c1"], op["c2"], op.get("p", 24))
            _paste_box(img, tile, op["x"], op["y"], op.get("r", 0))
        elif o == "c":
            r = op["r"]
            m = _aa_mask(2 * r, 2 * r, r)
            img.paste(Image.new("RGB", (2 * r, 2 * r), op["c"]), (int(op["x"] - r), int(op["y"] - r)), m)
        elif o == "p":
            d.polygon([(float(x), float(y)) for x, y in op["pts"]], fill=op["c"])
        elif o == "t":
            text = op["t"]
            k = op.get("k")
            if k:
                when = (clock + k["in"]) if "in" in k else k.get("at", clock)
                text = k["fmt"] % clock_text(when, h24)
            color = op["bl"] if (blink_dim and op.get("bl")) else op["c"]
            _draw_text(d, fonts, op, text, color)
        elif o == "bar":
            _paint_bar(img, fonts, op, zoom)
        elif o == "img" and PREVIEWS is not None:
            pic = PREVIEWS.get(op["src"], int(op["w"]), int(op["h"]))
            if pic is not None:
                img.paste(pic, (int(op["x"] + (op["w"] - pic.width) / 2), int(op["y"] + (op["h"] - pic.height) / 2)), pic)
        elif o == "logo":
            pic = _logo(int(op["w"]), int(op["h"]))
            if pic is not None:
                img.paste(pic, (int(op["x"] + (op["w"] - pic.width) / 2), int(op["y"] + (op["h"] - pic.height) / 2)), pic)
    return img


_LOGOS = {}


def _logo(w: int, h: int):
    """The maker's logo fitted to w x h (kept once made), or None if the file is missing."""
    if (w, h) not in _LOGOS:
        try:
            pic = Image.open(LOGO_FILE).convert("RGBA")
            pic.thumbnail((max(1, w), max(1, h)), Image.LANCZOS)
        except Exception:
            log.warning("no logo for the splash (%s)", LOGO_FILE)
            pic = None
        _LOGOS[(w, h)] = pic
    return _LOGOS[(w, h)]


def _uptime() -> float:
    """Seconds since the Pi started (very large where that can't be read, so no splash is held)."""
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 1e9


class Previews:
    """Pictures of jobs' parts, kept once fetched. fetch(md5) gives the PNG bytes or None: in the screen program
    from the service's /preview/<md5>.png, in the service itself from the profile store. A picture that isn't
    there yet is asked for again after a while (it is drawn in the background after a file is read)."""

    RETRY_S = 20.0

    def __init__(self, fetch) -> None:
        self.fetch = fetch
        self.pics = {}         # md5 -> RGBA picture
        self.missing = {}      # md5 -> when it was last asked for in vain
        self.sized = {}        # (md5, w, h) -> picture fitted to that box

    def get(self, md5: str, w: int, h: int):
        if not md5:
            return None
        key = (md5, w, h)
        if key in self.sized:
            return self.sized[key]
        pic = self.pics.get(md5)
        if pic is None:
            if time.time() - self.missing.get(md5, 0.0) < self.RETRY_S:
                return None
            try:
                import io
                data = self.fetch(md5)
                pic = Image.open(io.BytesIO(data)).convert("RGBA") if data else None
            except Exception:
                pic = None
            if pic is None:
                self.missing[md5] = time.time()
                return None
            if len(self.pics) > 200:
                self.pics.clear()
                self.sized.clear()
            self.pics[md5] = pic
        fit = pic.copy()
        fit.thumbnail((max(1, w), max(1, h)), Image.LANCZOS)
        self.sized[key] = fit
        return fit


PREVIEWS = None              # set by run() (the screen program) and by the service for its own pictures


def previews_from(base: str) -> Previews:
    def fetch(md5):
        try:
            with urllib.request.urlopen("%s/preview/%s.png" % (base, md5), timeout=2) as r:
                return r.read()
        except Exception:
            return None
    return Previews(fetch)


def render(view, fonts: Fonts, now: float | None = None, h24: bool | None = None, flip: bool | None = None,
           blink_dim: bool = False, scale: int = 1):
    """The picture for one moment: layout, paint, and turn it round if the screen is mounted upside down."""
    return render_hits(view, fonts, now, h24, flip, blink_dim, scale)[0]


def render_hits(view, fonts: Fonts, now: float | None = None, h24: bool | None = None, flip: bool | None = None,
                blink_dim: bool = False, scale: int = 1):
    """The picture and the touch targets on it (targets in 320 x 240 upright coordinates at any scale)."""
    view = view if isinstance(view, dict) else {}
    scr = view.get("screen") or {}
    h24 = bool(scr.get("clock_24h")) if h24 is None else h24
    flip = bool(scr.get("flip")) if flip is None else flip
    clock = view.get("clock") or (time.time() if now is None else now)
    ops, hits = layout_hits(view, fonts)
    img = paint(ops, fonts, clock, h24, blink_dim, scale)
    if flip:
        img = img.transpose(Image.ROTATE_180)
    return img, hits


def png_bytes(img) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- framebuffer
def pack565(img) -> bytes:
    """RGB picture to 16-bit RGB565, low byte first (what the PiTFT's framebuffer takes)."""
    r, g, b = img.convert("RGB").split()
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: (v << 3) & 0xE0), b.point(lambda v: v >> 3))
    return Image.merge("LA", (lo, hi)).tobytes()


def _read(path: str) -> str:
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except OSError:
        return ""


class Framebuffer:
    """One /dev/fbN, found by its size: the PiTFT is 320 x 240, a 3.5 inch DPI screen 640 x 480
    (a monitor's is bigger)."""

    def __init__(self, dev: str, width: int, height: int, bpp: int, stride: int = 0, name: str = "",
                 index: int | None = None) -> None:
        self.dev = dev
        self.width = width
        self.height = height
        self.bpp = bpp
        self.stride = stride or width * max(1, bpp // 8)
        self.name = name
        self.index = index
        self.fh = None

    def __repr__(self) -> str:
        return "%s (%s, %dx%d, %d bit)" % (self.dev, self.name or "?", self.width, self.height, self.bpp)

    @staticmethod
    def list_all(sysfs: str = "", devdir: str = "") -> list:
        sysfs, devdir = sysfs or SYSFS, devdir or DEVDIR
        found = []
        for p in sorted(glob.glob(os.path.join(sysfs, "fb[0-9]*")), key=lambda s: int(s.rsplit("fb", 1)[1])):
            n = int(p.rsplit("fb", 1)[1])
            size = _read(os.path.join(p, "virtual_size")) or "0,0"
            try:
                w, h = (int(x) for x in size.split(",")[:2])
                bpp = int(_read(os.path.join(p, "bits_per_pixel")) or 0)
                stride = int(_read(os.path.join(p, "stride")) or 0)
            except ValueError:
                continue
            found.append(Framebuffer(os.path.join(devdir, "fb%d" % n), w, h, bpp, stride,
                                     _read(os.path.join(p, "name")), n))
        return found

    @classmethod
    def find(cls, device: str = "", sysfs: str = "", devdir: str = ""):
        fbs = cls.list_all(sysfs, devdir)
        if device:
            for fb in fbs:
                if fb.dev == device:
                    return fb
            return None
        small = [fb for fb in fbs if 0 < max(fb.width, fb.height) <= 800 and fb.bpp in (16, 24, 32)]
        small.sort(key=lambda fb: (0 if sorted((fb.width, fb.height)) == [240, 320] else 1 if fb.scale() > 1 else 2,
                                   fb.index))
        return small[0] if small else None

    def scale(self) -> int:
        """2 for a 640 x 480 screen and 800x480 screens (3, 4 for 960 x 720, 1280 x 960): the screens are drawn that much larger
        to fill it exactly. 1 for anything else."""
        big, small = max(self.width, self.height), min(self.width, self.height)
        
        if (big, small) == (800, 480):
            return 2

        for k in (4, 3, 2):
            if big == W * k and small == H * k:
                return k
        return 1

    def open(self) -> None:
        self.fh = open(self.dev, "r+b", buffering=0)

    def close(self) -> None:
        if self.fh is not None:
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None

    def frame(self, img) -> bytes:
        """The picture as this framebuffer's bytes: turned to fit, scaled if needed, packed."""
        fw, fh = self.width, self.height
        if (fw < fh) != (img.width < img.height):
            img = img.transpose(Image.ROTATE_90)
        if img.size != (fw, fh):
            scale = min(fw / img.width, fh / img.height)
            sw, sh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
            whole = abs(scale - round(scale)) < 1e-9 and scale >= 1
            how = Image.NEAREST if whole else (Image.LANCZOS if hasattr(Image, "LANCZOS") else Image.BICUBIC)
            canvas = Image.new("RGB", (fw, fh), "#000000")
            canvas.paste(img.resize((sw, sh), how), ((fw - sw) // 2, (fh - sh) // 2))
            img = canvas
        if self.bpp == 16:
            data = pack565(img)
        elif self.bpp == 32:
            data = img.convert("RGB").tobytes("raw", "BGRX")
        else:
            data = img.convert("RGB").tobytes("raw", "BGR")
        row = fw * (self.bpp // 8)
        if self.stride > row:
            pad = b"\0" * (self.stride - row)
            data = b"".join(data[i:i + row] + pad for i in range(0, len(data), row))
        return data

    def write(self, data: bytes) -> None:
        if self.fh is None:
            self.open()
        self.fh.seek(0)
        self.fh.write(data)


# ---------------------------------------------------------------- the text console
KDGETMODE = 0x4B3B
KDSETMODE = 0x4B3A
KD_TEXT, KD_GRAPHICS = 0, 1
FBIOGET_CON2FBMAP = 0x460F
FB_MAX = 32                # framebuffer numbers go up to this; the kernel answers 2^32 - 1 for "none yet"
CONSOLE_CHECK_S = 0.4      # how often the text console is checked: a reset (systemd's, for one) puts it back to text
LISTEN_FDS_START = 3       # the first file systemd passes to a program


def passed_files(environ=None) -> dict:
    """The files systemd opened for this program (OpenFile= in its unit), by name: {"tty1": 3}."""
    env = os.environ if environ is None else environ
    try:
        if int(env.get("LISTEN_PID") or 0) != os.getpid():
            return {}              # meant for another program
        count = int(env.get("LISTEN_FDS") or 0)
    except ValueError:
        return {}
    names = (env.get("LISTEN_FDNAMES") or "").split(":")
    return {(names[i] if i < len(names) else "") or "fd%d" % (LISTEN_FDS_START + i): LISTEN_FDS_START + i
            for i in range(count)}


class Console:
    """Keeps the Pi's text console (boot messages, the login prompt, cursor) off the small screen.

    Needed when the console draws on the small screen's framebuffer: the 3.5inch
    DPI screen's always, the PiTFT's when no monitor is plugged in (it can then
    become fb0). The console on screen is switched to graphics mode, which stops
    it drawing, and back to text mode when this program stops. A console not on
    any framebuffer yet is switched too: the kernel can wait for the first text
    before it takes one. Raspberry Pi OS 13 lets only root open a console, so
    the service has systemd open tty1, the one the Pi starts on, for this
    program (`passed`: {"tty1": fd}); other consoles are opened by name.
    """

    def __init__(self, fb: Framebuffer, passed=None) -> None:
        self.fb = fb
        self.passed = dict(passed or {})
        self.fd = None
        self.tty = ""
        self.checked = 0.0
        self.warned = False

    def _mapped(self, vt: int):
        try:
            import fcntl
            res = fcntl.ioctl(self.fb.fh.fileno(), FBIOGET_CON2FBMAP, struct.pack("II", vt, 0))
            return struct.unpack("II", res)[1]
        except Exception:
            return None

    def check(self, now: float) -> bool:
        """Twice a second: make sure the console isn't drawing on the screen. True if it was (the picture is then
        drawn again over its text)."""
        if now - self.checked < CONSOLE_CHECK_S:
            return False
        self.checked = now
        try:
            import fcntl
        except ImportError:
            return False
        active = _read(TTY_ACTIVE)
        if not active.startswith("tty") or not active[3:].isdigit():
            return False
        mapped = self._mapped(int(active[3:]))
        if mapped is not None and mapped < FB_MAX and mapped != self.fb.index:
            self.release()             # it draws on a monitor
            return False
        for attempt in (1, 2):
            try:
                if self.tty != active:
                    self.release()
                    if active in self.passed:
                        self.fd = os.dup(self.passed[active])    # a copy: closing it keeps systemd's for later
                    else:
                        self.fd = os.open(os.path.join(DEVDIR, active), os.O_WRONLY | os.O_NOCTTY)
                    self.tty = active
                mode = struct.unpack("i", fcntl.ioctl(self.fd, KDGETMODE, struct.pack("i", 0)))[0]
                if mode == KD_GRAPHICS:
                    return False
                fcntl.ioctl(self.fd, KDSETMODE, KD_GRAPHICS)
                log.info("the text console was drawing on the small screen; %s switched to graphics mode", active)
                return True
            except OSError as exc:
                # after a hang-up (systemd does one before a login prompt) the open console is dead: open it again,
                # by name, since a hang-up kills systemd's open file too
                self._close()
                self.passed.pop(active, None)
                if attempt == 2 and not self.warned:
                    log.warning("could not keep the text console off the small screen (%s)", exc)
                    self.warned = True
        return False

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            import fcntl
            fcntl.ioctl(self.fd, KDSETMODE, KD_TEXT)
        except Exception:
            pass
        self._close()

    def _close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None
        self.tty = ""


# ---------------------------------------------------------------- the service
def fetch_state(url: str, timeout: float = 1.5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def run(cfg: dict, url: str = "", device: str = "", interval: float = 0.5, stop_event=None,
        touch_input=None, expose=None, state_dir: str = "") -> int:
    from . import touch as touch_mod
    # the settings page's choices (screen.json) over the settings file, as in the screen service: until it answers,
    # they are all there is to go by, and the splash must already be the right way up
    tft = dict(cfg.get("tft") or {}, **prefs_mod.load_screen(state_dir))
    url = url or "http://127.0.0.1:%d/state" % int(cfg.get("web_port", 8080))
    device = device or tft.get("device") or ""
    fonts = Fonts()
    stop = {"now": False}

    def on_signal(signum, frame):
        stop["now"] = True

    if stop_event is None:
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
    else:
        stop = stop_event          # anything with a "now" key, set from another thread

    base = url[: -len("/state")] if url.endswith("/state") else url.rstrip("/")
    global PREVIEWS
    PREVIEWS = previews_from(base)
    panel = touch_mod.Panel(touch_mod.Api(base))
    tin = touch_input or touch_mod.TouchInput(tft.get("touch_device") or "")
    if expose is not None:          # for the tests
        expose["panel"] = panel
    fb, console = None, None
    passed = passed_files()        # tty1, opened by systemd for this program (OpenFile= in the service's unit)
    next_find, said_missing = 0.0, False
    started = time.time()
    splash_until = started + SPLASH_BOOT_S if _uptime() < 120 else 0.0    # just after a power-on: the splash first
    last_ok, good = 0.0, None
    last_frame = None
    scr = tft
    v = {"mode": "_starting"}
    next_state, next_cal = 0.0, 0.0
    pending = []
    log.info("small screen: reading %s", url)
    try:
        while not stop["now"]:
            t0 = time.time()
            if fb is None and t0 >= next_find:
                next_find = t0 + (0.5 if t0 - started < 30 else 5.0)   # early in the boot the screen appears soon
                fb = Framebuffer.find(device)
                if fb is None:
                    if not said_missing:
                        log.warning("no small screen found yet (looking for a 320 x 240 or 640 x 480 "
                                    "framebuffer%s); checking every 5 s", " " + device if device else "")
                        said_missing = True
                else:
                    try:
                        fb.open()
                        console = Console(fb, passed)
                        log.info("drawing on %r", fb)
                        last_frame = None
                        panel.screen, next_cal = (fb.width, fb.height), 0.0    # ask for this screen's calibration
                    except OSError as exc:
                        log.warning("can't open %s (%s)", fb.dev, exc)
                        fb = None
            tin.ensure(t0)
            draw = False
            if t0 >= next_state:
                next_state = t0 + interval
                draw = True
                got = fetch_state(url)
                if got is not None:
                    last_ok, good = t0, got
                    v = got
                elif good is not None and t0 - last_ok < 6:
                    v = good
                else:
                    v = {"mode": "_lost" if (last_ok or t0 - started > 90) else "_starting"}
                    if good:
                        v["settings_url"] = good.get("settings_url", "")
                        v["screen"] = good.get("screen")
                if tin.ok() and t0 >= next_cal:
                    next_cal = t0 + (30.0 if panel.cal is not None else 5.0)
                    # naming the screen: a calibration made on another one isn't handed out
                    info = fetch_state(base + "/api/touch" + ("?screen=%dx%d" % panel.screen if panel.screen else ""))
                    if info is not None:
                        panel.set_calibration(info.get("cal"))
                panel.watch(v, t0)
            scr = v.get("screen") or tft   # the settings page's choice, else the settings file
            flip = bool(scr.get("flip"))
            for kind, rx, ry, tt in pending:
                draw = panel.touch(kind, rx, ry, tt, flip) or draw
            pending = []
            draw = panel.tick(t0) or draw
            if panel.ui is not None:
                view = dict(v, ui=dict(panel.ui, hold=panel.hold_progress(t0)))
            elif t0 < splash_until:
                view = {"mode": "_starting"}
                draw = True
            elif tin.ok() and v.get("mode") == "idle" and not v.get("demo"):
                view = dict(v, touch_home="ready" if panel.ready() else "setup")
            else:
                view = v
            if fb is not None and draw:
                try:
                    if console is not None and console.check(t0):
                        last_frame = None          # the console may have drawn over the picture
                    img, hits = render_hits(view, fonts, now=t0, h24=bool(scr.get("clock_24h")), flip=flip,
                                            blink_dim=int(t0) % 2 == 1, scale=fb.scale())
                    panel.hits = hits
                    data = fb.frame(img)
                    if data != last_frame:
                        fb.write(data)
                        last_frame = data
                except OSError as exc:
                    log.warning("lost the small screen (%s); looking for it again", exc)
                    if console is not None:
                        console.release()
                    fb.close()
                    fb, console = None, None
                except Exception:
                    log.exception("could not draw the screen")
            elif fb is None and draw:
                try:
                    panel.hits = layout_hits(view, fonts)[1]
                except Exception:
                    log.exception("could not lay out the screen")
            busy = panel.ui is not None and (panel.press is not None or panel.work is not None)
            wait = 0.05 if busy else max(0.02, next_state - time.time())
            pending = tin.touches(wait)
    finally:
        if fb is not None:
            try:
                fb.write(fb.frame(render({"mode": "_stopped"}, fonts, flip=bool(scr.get("flip")), scale=fb.scale())))
            except Exception:
                pass
            if console is not None:
                console.release()
            fb.close()
        log.info("small screen stopped")
    return 0


def main(argv=None) -> int:
    from . import config as config_mod
    ap = argparse.ArgumentParser(prog="z1screen.tft", description="Draw BitCue on the Pi's small screen.")
    ap.add_argument("--config", default="", help="extra settings file (JSON)")
    ap.add_argument("--url", default="", help="screen data address (default http://127.0.0.1:<web_port>/state)")
    ap.add_argument("--device", default="", help="framebuffer to draw on, e.g. /dev/fb1 (default: find it)")
    ap.add_argument("--png", default="", help="draw the current screen into this picture file and stop")
    ap.add_argument("--view", default="", help="with --png: screen data from this JSON file instead of the service")
    ap.add_argument("--scale", type=int, default=1, help="with --png: enlarge the picture this many times")
    ap.add_argument("--list", action="store_true", help="list this Pi's framebuffers and stop")
    ap.add_argument("--state", default=os.environ.get("STATE_DIRECTORY", "/var/lib/z1screen"),
                    help="the screen service's folder, where the settings page keeps this screen's choices")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    logging.basicConfig(level=getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.list:
        fbs = Framebuffer.list_all()
        pick = Framebuffer.find(args.device or (cfg.get("tft") or {}).get("device") or "")
        for fb in fbs:
            print("%s%r" % ("* " if pick and fb.dev == pick.dev else "  ", fb))
        if not fbs:
            print("no framebuffers")
        return 0
    if args.png:
        if args.view:
            with open(args.view, "r", encoding="utf-8") as fh:
                v = json.load(fh)
        else:
            v = fetch_state(args.url or "http://127.0.0.1:%d/state" % int(cfg.get("web_port", 8080))) or {}
        img = render(v, Fonts())
        if args.scale > 1:
            img = img.resize((W * args.scale, H * args.scale), Image.NEAREST)
        img.save(args.png)
        print("wrote", args.png)
        return 0
    return run(cfg, args.url, args.device, state_dir=args.state)


if __name__ == "__main__":
    sys.exit(main())
