"""Build the playable demo page (no Pi needed) from the real screen page.

The screen data comes from z1screen.demo.build_timeline(): the screen program
itself run through a made-up run of the built-in sample job (a sign with three
bits, z1screen/sample-job.json). This script packs that
recording into the page with a control bar: Press Run, the Z1's front button,
speed, skip, and a few "what if" cases.

The page shows the Pi's 2.8-inch screen by default: for every recorded moment it
carries the drawing steps from z1screen.tft.layout() (the same steps the Pi
draws), and a small canvas painter draws them. "Phone page" switches to the web
page a phone or laptop shows.

  python3 tools/build_demo.py [output.html]      (default: docs/demo.html)
"""
import copy
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from z1screen import config, demo, tft  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tft_shots import add_bits  # noqa: E402

MACHINE = "Makera_Z1"
SETTINGS = "http://z1screen.local:8080/settings"
JOB_TITLE = "Sample sign (made-up demo job)"


def compact(tl):
    """Round numbers and share repeated parts so the recording stays small."""
    shared, index = [], {}

    def ref(obj):
        key = json.dumps(obj, sort_keys=True, ensure_ascii=False)
        if key not in index:
            index[key] = len(shared)
            shared.append(obj)
        return {"$": index[key]}

    def tidy(v):
        v = copy.deepcopy(v)
        v.pop("t", None)
        v["machine"] = ref({"name": v["machine"]["name"]})
        v["relay"] = ref({"name": v["relay"]["name"]})
        if v.get("legend"):
            v["legend"] = ref(v["legend"])
        j = v.get("job")
        if j:
            for k in ("elapsed", "left"):
                if j.get(k) is not None:
                    j[k] = round(j[k])
            j["percent"] = round(j["percent"], 2)
            if j.get("segments"):
                j["segments"] = ref(j["segments"])
            if j.get("now"):
                j["now"] = ref(j["now"])
            if j.get("next"):
                j["next"]["in"] = round(j["next"]["in"])
        tc = v.get("toolchange")
        if tc and tc.get("then"):
            tc["then"]["in"] = round(tc["then"]["in"])
        return v

    def steps(ops):
        out = []
        for op in ops:
            op = dict(op)
            if op.get("o") == "bar":
                op["segs"] = ref(op["segs"])
            out.append(ref(op))
        return out

    frames = [{"k": f["k"], "est": f["est"], "view": tidy(f["view"]), "tft": steps(f["tft"])} for f in tl["frames"]]
    extras = {k: tidy(v) for k, v in tl["extras"].items()}
    extras_tft = {k: steps(v) for k, v in tl["extras_tft"].items()}
    return {"shared": shared, "frames": frames, "extras": extras, "extras_tft": extras_tft, "total": tl["total"],
            "name": tl["name"], "relay": MACHINE + "_screen", "title": JOB_TITLE,
            "fonts": {k: [fam, weight] for k, (fam, weight) in tft.FONT_CSS.items()}}


def main():
    tl = demo.build_timeline(copy.deepcopy(config.DEFAULTS), demo.sample_profile(), MACHINE, SETTINGS)
    fonts = tft.Fonts()
    for f in tl["frames"]:
        add_bits(f["view"])                  # made-up bit life, so the gauges show
        f["tft"] = tft.layout(f["view"], fonts)
    tl["extras_tft"] = {k: tft.layout(v, fonts) for k, v in tl["extras"].items()}
    data = compact(tl)

    src = open(os.path.join(ROOT, "z1screen", "web", "index.html"), encoding="utf-8").read()
    page = src.split("<!-- page:start -->", 1)[1].split("<!-- page:end -->", 1)[0]
    page = page.replace("<!-- head:end -->\n</head>\n<body>\n", "")
    page = re.sub(r"/\* fonts:local:start \*/.*?/\* fonts:local:end \*/\n", "", page, flags=re.S)
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700'
             '&family=Barlow:wght@400;500;600;700&display=swap">\n')
    page = page.replace("<style>", fonts + "<style>", 1)
    # opened from disk, the page has no server to say it is UTF-8 (×, ·, ° would come out garbled)
    page = ('<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + page.lstrip())
    page = page.replace("</style>", DOCK_CSS + TFT_CSS + "</style>", 1)
    page = page.replace("<!-- demo:slot -->", TFT_HTML + DOCK_HTML + "<script>\nwindow.Z1_DEMO = true;\nwindow.Z1_TIMELINE = "
                        + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n</script>\n")
    page = page.rstrip() + "\n" + PLAYER_JS
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "demo.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page.lstrip())
    print("wrote", out, len(page.encode("utf-8")), "bytes;", len(data["frames"]), "frames,", len(data["shared"]),
          "shared parts")


DOCK_CSS = """
/* ---------- demo control bar (demo page only) ---------- */
:root { --dock: #0b0a09; --wait: #f4f0e8; }
body.has-demo .view { height: calc(100% - var(--dock-h, 150px)); }
.dock {
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 8; box-sizing: border-box;
  background: var(--dock); border-top: 1px solid var(--line); color: var(--ink); font-family: var(--body);
  padding: 12px var(--gutter) calc(12px + env(safe-area-inset-bottom, 0px));
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 24px; align-items: center;
  grid-template-areas: "label front" "say front" "controls front";
}
.dock-label { grid-area: label; font-weight: 600; font-size: 12px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); }
.dock-say { grid-area: say; margin: 0; font-size: 17px; line-height: 1.35; max-width: 72ch; min-height: 2.7em; text-wrap: pretty; }
.dock-controls { grid-area: controls; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; min-width: 0; }
.dbtn {
  display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box;
  font: 600 14px/1 var(--body); color: var(--ink); background: var(--panel); border: 1px solid var(--line);
  border-radius: 999px; padding: 0 1.05em; height: 38px; cursor: pointer; white-space: nowrap;
}
.dbtn:hover:not(:disabled) { border-color: var(--muted); }
.dbtn:disabled { opacity: .4; cursor: default; }
.dbtn.primary { background: var(--ink); color: var(--ground); border-color: var(--ink); }
.dbtn:focus-visible, .front:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
.dbtn .short { display: none; }
.dseg { display: inline-flex; border: 1px solid var(--line); border-radius: 999px; overflow: hidden; }
.dseg .dbtn { border: 0; border-radius: 0; height: 36px; }
.dseg .dbtn + .dbtn { border-left: 1px solid var(--line); }
.dseg .dbtn[aria-pressed="true"] { background: var(--ink); color: var(--ground); }
.whatif { position: relative; }
.whatif summary { list-style: none; }
.whatif summary::-webkit-details-marker { display: none; }
.whatif-menu {
  position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 9; display: flex; flex-direction: column; gap: 4px;
  padding: 8px; width: 20rem; background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  box-shadow: 0 14px 34px rgba(0,0,0,.5);
}
.whatif-menu .dbtn { justify-content: flex-start; text-align: left; border: 0; background: transparent; white-space: normal; height: auto; min-height: 40px; padding: .6em .9em; line-height: 1.25; }
.whatif-menu .dbtn:hover { background: var(--line); }
.front {
  grid-area: front; display: flex; flex-direction: column; align-items: center; gap: 7px; padding: 4px 2px;
  background: none; border: 0; color: var(--ink); cursor: pointer;
  font: 600 12px/1 var(--body); letter-spacing: .09em; text-transform: uppercase;
}
.front .ring {
  width: 66px; height: 66px; border-radius: 50%; display: grid; place-items: center; box-sizing: border-box;
  border: 3px solid #cfc8bb; background: #2a2824; box-shadow: inset 0 -5px 0 rgba(0,0,0,.35);
}
.front .ring i { width: 28px; height: 28px; border-radius: 50%; background: #3b3832; box-shadow: inset 0 0 0 2px rgba(255,255,255,.08); }
.front.waiting .ring { border-color: var(--wait); box-shadow: 0 0 0 4px rgba(255,255,255,.06), 0 0 24px var(--wait); }
.front.waiting .ring i { background: var(--wait); }
.front:disabled { opacity: .45; cursor: default; }
@media (prefers-reduced-motion: no-preference) {
  .front.waiting .ring { animation: frontglow 1.1s ease-in-out infinite; }
  @keyframes frontglow { 50% { box-shadow: 0 0 0 8px rgba(255,255,255,.03), 0 0 6px var(--wait); } }
}
@media (max-width: 760px), (max-aspect-ratio: 4/5) {
  body.has-demo { padding-bottom: var(--dock-h, 220px); }
  body.has-demo .view { height: auto; }
  .dock { grid-template-columns: auto minmax(0, 1fr); gap: 8px 14px; grid-template-areas: "label label" "say say" "front controls"; }
  .dock-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dock-say { font-size: 15px; min-height: 4.05em; }
  .dock-controls { gap: 6px; }
  .dbtn { height: 36px; padding: 0 .85em; font-size: 13px; }
  .dbtn .long { display: none; }
  .dbtn .short { display: inline; }
  .front { gap: 5px; font-size: 11px; }
  .front .ring { width: 58px; height: 58px; }
  .whatif-menu { position: fixed; left: 16px; right: 16px; width: auto; bottom: calc(var(--dock-h, 220px) + 8px); }
}
"""

TFT_CSS = """
/* ---------- the Pi's 2.8-inch screen (demo page only) ---------- */
.tft-stage {
  position: fixed; left: 0; right: 0; top: 0; bottom: var(--dock-h, 150px); z-index: 6; box-sizing: border-box;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px;
  padding: calc(16px + env(safe-area-inset-top, 0px)) 16px 16px; background: var(--ground);
}
body:not(.tft-mode) .tft-stage { display: none; }
body.tft-mode .view, body.tft-mode .demo-tag, body.tft-mode .banner { display: none !important; }
.tft-bezel { background: #050505; border-radius: 16px; padding: var(--bz, 18px); box-shadow: 0 0 0 1px #2c2a26, 0 18px 50px rgba(0,0,0,.55); }
.tft-bezel canvas { display: block; border-radius: 2px; background: #000; }
.tft-note { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.4; text-align: center; max-width: 60ch; }
"""

TFT_HTML = """<div class="tft-stage" id="tftStage">
  <div class="tft-bezel" id="tftBezel"><canvas id="tftCanvas" width="320" height="240" role="img" aria-label="The Pi's 2.8-inch screen"></canvas></div>
  <p class="tft-note">The Pi's 2.8-inch screen (320 &times; 240), shown enlarged. It is drawn from the same layout steps the Pi uses.</p>
</div>
"""

DOCK_HTML = """<div class="dock" id="dock" role="region" aria-label="Demo controls">
  <span class="dock-label" id="dockLabel">Demo</span>
  <p class="dock-say" id="say" aria-live="polite"></p>
  <div class="dock-controls">
    <button class="dbtn primary" id="act" type="button">Press Run</button>
    <button class="dbtn" id="skip" type="button"><span class="long">Skip to the next stop</span><span class="short">Skip ahead</span></button>
    <div class="dseg" role="group" aria-label="Demo speed">
      <button class="dbtn" type="button" data-speed="1" aria-pressed="false"><span class="long">Real speed</span><span class="short">1&times;</span></button>
      <button class="dbtn" type="button" data-speed="60" aria-pressed="true">60&times;</button>
      <button class="dbtn" type="button" data-speed="360" aria-pressed="false">360&times;</button>
    </div>
    <div class="dseg" role="group" aria-label="What to show">
      <button class="dbtn" type="button" data-show="tft" aria-pressed="true"><span class="long">Pi's screen</span><span class="short">Pi</span></button>
      <button class="dbtn" type="button" data-show="page" aria-pressed="false"><span class="long">Phone page</span><span class="short">Phone</span></button>
    </div>
    <details class="whatif" id="whatif">
      <summary class="dbtn">What if&hellip;</summary>
      <div class="whatif-menu">
        <button class="dbtn" type="button" data-what="probe">the probe cable isn't plugged in</button>
        <button class="dbtn" type="button" data-what="hand">the front button is pressed while it cuts</button>
        <button class="dbtn" type="button" data-what="laptop">the laptop is closed during the job</button>
      </div>
    </details>
    <button class="dbtn" id="restart" type="button">Start over</button>
  </div>
  <button class="front" id="front" type="button" aria-label="The Z1's front button"><span class="ring"><i></i></span>Front button</button>
</div>
"""

PLAYER_JS = """<script>
(function () {
  "use strict";
  var TL = window.Z1_TIMELINE;
  var $ = function (id) { return document.getElementById(id); };
  function inflate(o) {
    if (Array.isArray(o)) return o.map(inflate);
    if (o && typeof o === "object") {
      if (o.$ !== undefined && Object.keys(o).length === 1) return inflate(TL.shared[o.$]);
      var out = {};
      Object.keys(o).forEach(function (k) { out[k] = inflate(o[k]); });
      return out;
    }
    return o;
  }
  var frames = TL.frames.map(function (f) { return { k: f.k, est: f.est, view: inflate(f.view), tft: inflate(f.tft) }; });
  var extras = { probe: inflate(TL.extras.probe), hand: inflate(TL.extras.hand) };
  var extrasTft = { probe: inflate(TL.extras_tft.probe), hand: inflate(TL.extras_tft.hand) };

  // ---- the Pi's 2.8-inch screen: draws the same steps as z1screen/tft.py paint()
  var cv = $("tftCanvas"), cx = cv.getContext("2d"), tftScale = 2;
  function fnt(f, z) { var d = TL.fonts[f]; return d[1] + " " + z + "px '" + d[0] + "', 'Arial Narrow', sans-serif"; }
  function hexRgb(c) { c = (c || "#000000").replace("#", ""); return [0, 2, 4].map(function (i) { return parseInt(c.substr(i, 2), 16); }); }
  function mixHex(a, b, t) {
    var x = hexRgb(a), y = hexRgb(b);
    return "#" + x.map(function (v, i) { var m = Math.round(v * t + y[i] * (1 - t)); return (m < 16 ? "0" : "") + m.toString(16); }).join("");
  }
  function hhmm(ms) {
    var d = new Date(ms), h = d.getHours(), m = d.getMinutes();
    return (h % 12 || 12) + ":" + (m < 10 ? "0" : "") + m + " " + (h < 12 ? "AM" : "PM");
  }
  function rr(x, y, w, h, r) {
    cx.beginPath();
    if (!r) { cx.rect(x, y, w, h); return; }
    cx.moveTo(x + r, y); cx.arcTo(x + w, y, x + w, y + h, r); cx.arcTo(x + w, y + h, x, y + h, r);
    cx.arcTo(x, y + h, x, y, r); cx.arcTo(x, y, x + w, y, r); cx.closePath();
  }
  function poly(pts, c) {
    cx.beginPath(); cx.moveTo(pts[0][0], pts[0][1]);
    for (var i = 1; i < pts.length; i++) cx.lineTo(pts[i][0], pts[i][1]);
    cx.closePath(); cx.fillStyle = c; cx.fill();
  }
  function stripes(x, y, w, h, c, c1, c2, p) {
    cx.fillStyle = c; cx.fillRect(x, y, w, h);
    var half = p / 2;
    for (var k = -2; k * p < w + h + p; k++) {
      var a = x + k * p;
      poly([[a, y], [a + half, y], [a + half - h, y + h], [a - h, y + h]], c1);
      poly([[a + half, y], [a + p, y], [a + p - h, y + h], [a + half - h, y + h]], c2);
    }
  }
  function txt(op, s, color) {
    cx.font = fnt(op.f, op.z); cx.fillStyle = color; cx.textBaseline = "alphabetic";
    var a = op.a || "l";
    if (op.ls) {
      var total = op.ls * (s.length - 1), i;
      for (i = 0; i < s.length; i++) total += cx.measureText(s[i]).width;
      var x = op.x - (a === "r" ? total : a === "m" ? total / 2 : 0);
      cx.textAlign = "left";
      for (i = 0; i < s.length; i++) { cx.fillText(s[i], x, op.y); x += cx.measureText(s[i]).width + op.ls; }
      return;
    }
    cx.textAlign = a === "r" ? "right" : a === "m" ? "center" : "left";
    if (op.mw) cx.fillText(s, op.x, op.y, op.mw); else cx.fillText(s, op.x, op.y);
  }
  function bar(op) {
    var x = op.x, y = op.y, w = op.w, h = op.h, px = Math.round(w * op.p / 100);
    cx.save(); rr(x, y, w, h, op.r || 0); cx.clip();
    cx.fillStyle = op.track; cx.fillRect(x, y, w, h);
    [["q", "lq", w], ["c", "lc", px]].forEach(function (L) {
      cx.save(); cx.beginPath(); cx.rect(x, y, L[2], h); cx.clip();
      if (op.segs.length) {
        op.segs.forEach(function (sg) {
          var x0 = Math.round(sg.a * w), x1 = Math.round(sg.b * w), c = sg[L[0]];
          if (x1 <= x0) return;
          if (sg.u) stripes(x + x0, y, x1 - x0, h, c, mixHex("#FFFFFF", c, 0.10), mixHex("#000000", c, 0.14), 8);
          else { cx.fillStyle = c; cx.fillRect(x + x0, y, x1 - x0, h); }
          cx.fillStyle = op.g; cx.fillRect(x + x1 - 2, y, 2, h);
          if (sg.l) {
            cx.font = fnt(op.f, op.z); cx.fillStyle = sg[L[1]]; cx.textAlign = "left"; cx.textBaseline = "alphabetic";
            cx.fillText(sg.l, x + x0 + 4, y + Math.floor(h / 2) + Math.floor(op.z * 0.36));
          }
        });
      } else if (L[0] === "c") { cx.fillStyle = op.plain; cx.fillRect(x, y, w, h); }
      cx.restore();
    });
    cx.restore();
    var mx = x + px;
    cx.fillStyle = op.g; cx.fillRect(mx - 3, y - 5, 6, h + 10);
    cx.fillStyle = op.m; cx.fillRect(mx - 1, y - 3, 2, h + 6);
  }
  function paintTft(ops, clockMs) {
    var dim = Math.floor(Date.now() / 1000) % 2 === 1, k = tftScale * (window.devicePixelRatio || 1);
    cx.setTransform(k, 0, 0, k, 0, 0);
    cx.fillStyle = "#141311"; cx.fillRect(0, 0, 320, 240);
    ops.forEach(function (op) {
      if (op.o === "r") { rr(op.x, op.y, op.w, op.h, op.r || 0); cx.fillStyle = op.c; cx.fill(); }
      else if (op.o === "s") {
        cx.save(); rr(op.x, op.y, op.w, op.h, op.r || 0); cx.clip();
        stripes(op.x, op.y, op.w, op.h, op.c, op.c1, op.c2, op.p || 24); cx.restore();
      }
      else if (op.o === "c") { cx.beginPath(); cx.arc(op.x, op.y, op.r, 0, Math.PI * 2); cx.fillStyle = op.c; cx.fill(); }
      else if (op.o === "t") {
        var s = op.t, kk = op.k;
        if (kk) {
          var when = kk.tag === "ended" ? (S.doneClock || clockMs) : kk["in"] !== undefined ? clockMs + kk["in"] * 1000 : kk.at * 1000;
          s = kk.fmt.split("%%").map(function (part) { return part.replace("%s", hhmm(when)); }).join("%");
        }
        txt(op, s, dim && op.bl ? op.bl : op.c);
      }
      else if (op.o === "bar") bar(op);
    });
  }
  function drawTft() {
    if (!document.body.classList.contains("tft-mode")) return;
    paintTft(S.whatif ? extrasTft[S.whatif] : frames[S.i].tft, S.clock);
  }
  function fitTft() {
    var stage = $("tftStage"), note = stage.querySelector(".tft-note");
    var bz = Math.max(10, Math.min(22, Math.round(window.innerWidth / 50)));
    var w = stage.clientWidth - 32 - 2 * bz, h = stage.clientHeight - 32 - 2 * bz - (note ? note.offsetHeight + 14 : 0);
    tftScale = Math.max(0.75, Math.min(3, w / 320, h / 240));
    var dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(320 * tftScale * dpr); cv.height = Math.round(240 * tftScale * dpr);
    cv.style.width = Math.round(320 * tftScale) + "px"; cv.style.height = Math.round(240 * tftScale) + "px";
    $("tftBezel").style.setProperty("--bz", bz + "px");
    drawTft();
  }
  function setShow(which) {
    S.show = which === "page" ? "page" : "tft";
    document.body.classList.toggle("tft-mode", S.show === "tft");
    Array.prototype.forEach.call(document.querySelectorAll("[data-show]"), function (o) {
      o.setAttribute("aria-pressed", o.dataset.show === S.show ? "true" : "false");
    });
    try { localStorage.setItem("z1demo-show", S.show); } catch (e) {}
    if (S.show === "tft") fitTft();
  }
  var S = { phase: "ready", i: 0, est: 0, speed: 60, paused: false, clock: Date.now(), doneClock: 0, show: "tft",
            whatif: null, toast: "", toastUntil: 0, t: 0, shownAt: 0, sayText: "" };

  function dur(s) {
    s = Math.max(0, Math.round(s));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? h + " h " + (m < 10 ? "0" : "") + m + " min" : m + " min";
  }
  function view() {
    var v;
    if (S.whatif) {
      v = Object.assign({}, extras[S.whatif]);
    } else {
      v = Object.assign({}, frames[S.i].view);
      if (v.last_job) v.last_job = Object.assign({}, v.last_job, { ended: (S.doneClock || S.clock) / 1000 });
    }
    v.clock = S.clock / 1000;
    return v;
  }
  function show() {
    window.Z1Screen.render(view());
    drawTft();
    S.shownAt = performance.now();
    dock();
  }
  function enter(i) {
    S.i = i;
    var f = frames[i];
    if (f.k === "stop") { S.phase = "stop"; S.est = f.est; return true; }
    if (f.k === "done") { S.phase = "done"; S.est = f.est; S.doneClock = S.clock; return true; }
    S.phase = "run";
    return false;
  }
  function step(dt) {
    if (S.whatif) return false;
    if (S.phase === "run" && !S.paused) {
      S.clock += dt * 1000 * S.speed;
      S.est += dt * S.speed;
      var moved = false;
      while (S.i + 1 < frames.length && frames[S.i + 1].est <= S.est) {
        moved = true;
        if (enter(S.i + 1)) break;
      }
      return moved;
    }
    S.clock += dt * 1000;
    return false;
  }
  function loop(t) {
    var dt = S.t ? Math.min(0.25, Math.max(0, (t - S.t) / 1000)) : 0;
    S.t = t;
    if (step(dt) || t - S.shownAt > 1000) show();
    else if (S.toast && performance.now() > S.toastUntil) { S.toast = ""; dock(); }
    requestAnimationFrame(loop);
  }

  function say() {
    if (S.toast && performance.now() < S.toastUntil) return S.toast;
    if (S.whatif === "probe") return "If the probe cable isn't plugged in, the Z1 stops when it tries to probe and the screen shows this. Plug the cable in, unlock the Z1 in Makera Studio, and start the job again.";
    if (S.whatif === "hand") return "Pressing the front button while the Z1 is cutting stops the job, and the screen shows this. On the real Z1 you would unlock it in Makera Studio and start the job again.";
    var v = frames[S.i].view;
    if (S.phase === "ready") return "The Z1 is idle. You set up the job in Makera Studio as usual, connected to " + TL.relay + ", and press Run.";
    if (S.phase === "stop") {
      var tc = v.toolchange || {};
      if (tc.tool === 0) return "The Z1 beeps and waits for the probe. Put the probe in, plug its cable into the port on the side of the head, then press the front button once.";
      return "The Z1 beeps and waits for " + tc.code + ". Fit the " + (tc.label || "bit") +
        (tc.sticker ? " with the " + tc.sticker.toLowerCase() + " sticker" : "") + ", then press the front button once.";
    }
    if (S.phase === "done") return "The job is done. The screen shows the finished job until the next one starts.";
    if (S.paused) return "Paused. This only pauses the demo; the real Z1 keeps cutting.";
    return "The Z1 is cutting. Nothing to do until it beeps." +
      (S.speed === 1 ? " Playing at the machine's real speed." : " The demo plays " + S.speed + "\\u00d7 faster than the machine.");
  }
  function dock() {
    var text = say();
    if (text !== S.sayText) { $("say").textContent = text; S.sayText = text; }
    var act = $("act"), front = $("front");
    act.hidden = S.phase === "stop" && !S.whatif;
    act.textContent = S.whatif ? "Go back" : S.phase === "ready" ? "Press Run" : S.phase === "done" ? "Play again"
      : S.paused ? "Resume" : "Pause";
    $("skip").disabled = S.whatif !== null || S.phase !== "run";
    front.disabled = S.whatif !== null || (S.phase !== "stop" && S.phase !== "run");
    var waiting = S.phase === "stop" && !S.whatif;
    front.classList.toggle("waiting", waiting);
    var tc = waiting ? frames[S.i].view.toolchange : null;
    front.style.setProperty("--wait", tc ? tc.color : "#f4f0e8");
    front.setAttribute("aria-label", waiting ? "The Z1's front button: press to carry on after loading the bit" : "The Z1's front button");
  }

  function act() {
    if (S.whatif) { S.whatif = null; show(); return; }
    if (S.phase === "ready") { enter(1); show(); return; }
    if (S.phase === "done") { restart(); return; }
    if (S.phase === "run") { S.paused = !S.paused; dock(); }
  }
  function front() {
    if (S.whatif) return;
    if (S.phase === "stop") {
      S.phase = "run";
      if (S.i + 1 < frames.length) S.i += 1;
      S.paused = false;
      show();
    } else if (S.phase === "run") {
      S.whatif = "hand";
      show();
    }
  }
  function skip() {
    if (S.phase !== "run" || S.whatif) return;
    var j = S.i + 1;
    while (j < frames.length && frames[j].k !== "stop" && frames[j].k !== "done") j++;
    if (j >= frames.length) return;
    var target = Math.max(S.est, frames[j].est - 90);
    S.clock += (target - S.est) * 1000;
    S.est = target;
    while (S.i + 1 < j && frames[S.i + 1].est <= S.est) S.i++;
    S.paused = false;
    show();
  }
  function restart() {
    S.phase = "ready"; S.i = 0; S.est = 0; S.paused = false; S.whatif = null; S.toast = "";
    S.clock = Date.now(); S.doneClock = 0;
    show();
  }
  function whatIf(kind) {
    $("whatif").open = false;
    if (kind === "laptop") {
      S.toast = "Nothing changes on the screen. The job runs from the Z1's own storage, and the Pi keeps asking the Z1 for its status.";
      S.toastUntil = performance.now() + 9000;
      dock();
      return;
    }
    S.whatif = kind;
    show();
  }

  $("dockLabel").textContent = "Demo \\u00b7 " + TL.title + " \\u00b7 " + dur(TL.total) + " job, " +
    frames.filter(function (f) { return f.k === "stop"; }).length + " stops";
  $("act").addEventListener("click", act);
  $("front").addEventListener("click", front);
  $("skip").addEventListener("click", skip);
  $("restart").addEventListener("click", restart);
  Array.prototype.forEach.call(document.querySelectorAll("[data-speed]"), function (b) {
    b.addEventListener("click", function () {
      S.speed = Number(b.dataset.speed);
      Array.prototype.forEach.call(document.querySelectorAll("[data-speed]"), function (o) {
        o.setAttribute("aria-pressed", o === b ? "true" : "false");
      });
      dock();
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-what]"), function (b) {
    b.addEventListener("click", function () { whatIf(b.dataset.what); });
  });
  document.addEventListener("click", function (e) {
    var w = $("whatif");
    if (w.open && !w.contains(e.target)) w.open = false;
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-show]"), function (b) {
    b.addEventListener("click", function () { setShow(b.dataset.show); show(); });
  });
  document.body.classList.add("has-demo");
  function fit() {
    document.body.style.setProperty("--dock-h", $("dock").offsetHeight + "px");
    if (S.show === "tft") fitTft();
  }
  if (window.ResizeObserver) new ResizeObserver(fit).observe($("dock"));
  window.addEventListener("resize", fit);
  var saved = "tft";
  try { saved = localStorage.getItem("z1demo-show") || "tft"; } catch (e) {}
  setShow(saved);
  fit();
  show();
  if (document.fonts && document.fonts.load) {
    Promise.all(Object.keys(TL.fonts).map(function (f) { return document.fonts.load(fnt(f, 24)); }))
      .then(drawTft, drawTft);
    document.fonts.ready.then(drawTft);
  }
  requestAnimationFrame(loop);
})();
</script>
"""

if __name__ == "__main__":
    main()
