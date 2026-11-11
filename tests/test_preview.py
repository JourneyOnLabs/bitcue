"""Pictures of jobs' parts (z1screen/preview.py) and where they show: kept next to the profile, served at
/preview/<md5>.png, painted on the small screen's job list and review screen, named in the phone page's data.
Made-up job files only."""
import asyncio, copy, io, json, math, os, tempfile, time
from PIL import Image
from common import ROOT, check
from z1screen import config, preview, tft
from z1screen.hub import Hub
from z1screen.jobs import JobStore
from z1screen.web import Web

HEADER = (";@MKR|STOCK|id=cuboid|length=60|width=60|height=3|diameter=50\n"
          ";@MKR|TOOL|number=1|name=3.175 flat|type=Flat End|diameter=3.175|tipdiameter=3.175|angle=0\n"
          ";@MKR|TOOL|number=3|name=30 V-bit|type=Engraving|diameter=3.175|tipdiameter=0.2|angle=30\n")


def square_part(tabs=True):
    """A 40 x 40 mm square cut out of 3 mm stock (with two 6 mm holding tabs at half depth), a 10 mm hole cut
    through its middle, a 1 mm deep pocket on one side and a V-bit line on the other."""
    g = [HEADER, "G90 G21\nT1 M6\nG0 Z5\n"]
    # pocket: 10 x 10 mm at (-12..-2, -12..-2), 1 mm deep, rows 1 mm apart
    g.append("G0 X-12 Y-12\nG1 Z-1 F300\n")
    for k in range(11):
        y = -12 + k
        g.append("G1 X-2 Y%.1f\nG1 X-12 Y%.1f\n" % (y, y + 0.5))
    g.append("G0 Z5\n")
    # hole through the middle: a 10 mm circle with arcs
    g.append("G0 X5 Y0\nG1 Z-3.1 F200\nG2 X-5 Y0 I-5 J0\nG2 X5 Y0 I5 J0\nG0 Z5\n")
    # cut-out with tabs on the left and right sides
    g.append("G0 X-20 Y-20\nG1 Z-3.1 F300\nG1 X20 Y-20\nG1 X20 Y-3\n")
    g.append("G1 Z-1.5\nG1 X20 Y3\nG1 Z-3.1\n" if tabs else "")
    g.append("G1 X20 Y20\nG1 X-20 Y20\nG1 X-20 Y3\n")
    g.append("G1 Z-1.5\nG1 X-20 Y-3\nG1 Z-3.1\n" if tabs else "")
    g.append("G1 X-20 Y-20\nG0 Z5\n")
    g.append("T3 M6\nG0 X4 Y8\nG1 Z-0.4 F200\nG1 X14 Y8\nG0 Z5\nM30\n")
    return "".join(g).encode()


def pic(png):
    return Image.open(io.BytesIO(png)).convert("RGBA")


def at(img, x_mm, y_mm, half=22.0):
    """The pixel at a place on the part (the picture spans the part's outline, about +-half mm, Y up)."""
    s = img.width
    return img.getpixel((int(s / 2 + x_mm / half * (s / 2 - 6)), int(s / 2 - y_mm / half * (s / 2 - 6))))


print("drawing a part")
t0 = time.time()
png = preview.render(square_part())
img = pic(png)
check(img.size == (preview.SIZE, preview.SIZE), "a %d x %d picture" % img.size)
corner = img.getpixel((2, 2))
check(corner[3] == 0, "outside the part: clear (the plate around it is left out)")
top = at(img, 10, -10)
check(top[3] == 255 and top[0] > top[2] + 40, "the top of the part: bronze %s" % (top[:3],))
check(at(img, 0, 0)[3] == 0, "the hole cut through the middle: clear")
pocket = at(img, -7, -7)
check(pocket[3] == 255 and sum(pocket[:3]) < sum(top[:3]), "the pocket floor: part of the part, a little darker")
check(at(img, 21.4, 0)[3] == 255, "the holding tabs don't let the outside in: the part is still there beside them")
line = [at(img, x, 8)[:3] for x in (6, 9, 12)]
check(any(sum(c) != sum(top[:3]) for c in line), "the V-bit line shows")
check(time.time() - t0 < 20, "drawn in %.1f s" % (time.time() - t0))

plain = pic(preview.render(square_part(tabs=False)))
check(plain.getpixel((2, 2))[3] == 0 and at(plain, 10, -10)[3] == 255, "the same without tabs")
check(preview.render(b"G90\nG0 X10 Y10 Z5\nG0 X0 Y0\nM30\n") is None, "a file that cuts nothing: no picture")
check(preview.render(b"G90\nG1 X0 Y0 Z-1 F100\nG1 X0 Y0\n") is not None, "one plunge is still a picture")
inch = pic(preview.render(b"G20 G90\nG1 X0 Y0 Z-0.02 F10\nG1 X1 Y0\nG1 X1 Y1\nG1 X0 Y1\nG1 X0 Y0\n"))
check(inch.getbbox() is not None, "a file in inches is drawn too")

print("kept next to the profile")
state = tempfile.mkdtemp()
lib = tempfile.mkdtemp()
store = JobStore(state, [lib])
data = square_part()
prof = store.add_file("/sd/gcodes/parts/square.nc", data)
check(store.needs_preview(prof) and not store.preview_file(prof["md5"]), "a new profile has no picture yet")
check(store.make_preview(prof["md5"], data), "drawn by a separate program at low priority")
f = store.preview_file(prof["md5"])
check(f == os.path.join(state, "profiles", prof["md5"] + ".png") and pic(open(f, "rb").read()).size == img.size,
      "saved as profiles/<md5>.png")
check(not store.needs_preview(prof), "and not drawn again")
air = open(os.path.join(ROOT, "air-test.nc"), "rb").read()
p_air = store.add_file("/sd/gcodes/air-test.nc", air)
check(not store.make_preview(p_air["md5"], air) and not store.needs_preview(p_air),
      "a file that cuts nothing: no picture, and not tried again")
with open(os.path.join(lib, "0123456789abcdef0123456789abcdef.png"), "wb") as fh:
    fh.write(png)
check(store.preview_file("0123456789abcdef0123456789abcdef").startswith(lib), "a kit's library can carry pictures")
check(store.preview_file("../../etc/passwd") == "" and store.preview_file("") == "", "only md5 names are looked up")

print("served by the Pi")
cfg = copy.deepcopy(config.DEFAULTS)
cfg["web_port"] = 47681
hub = Hub(cfg, store)


async def get(path):
    r, w = await asyncio.open_connection("127.0.0.1", 47681)
    w.write(("GET %s HTTP/1.1\r\nHost: z1screen.local\r\n\r\n" % path).encode())
    await w.drain()
    raw = await r.read()
    w.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), head.decode("latin-1"), body


async def serve_checks():
    web = Web(hub, 47681)
    task = asyncio.create_task(web.serve())
    await asyncio.sleep(0.3)
    try:
        code, head, body = await get("/preview/%s.png" % prof["md5"])
        check(code == 200 and "image/png" in head and body == open(f, "rb").read(), "at /preview/<md5>.png")
        check("max-age" in head, "and cached (the name changes when the file does)")
        code, _, _ = await get("/preview/%s.png" % ("f" * 32))
        check(code == 404, "a part with no picture: not found")
        code, _, _ = await get("/preview/..%2f..%2fprofiles.png")
        check(code == 404, "nothing but md5 names")
    finally:
        task.cancel()

asyncio.run(serve_checks())
check(hub.preview_of(prof) == prof["md5"] and hub.preview_of(p_air) == "", "the service names the picture of a profile")

print("on the small screen")
fonts = tft.Fonts()
tft.PREVIEWS = tft.Previews(lambda md5: png if md5 == "pic" else None)
lst = {"screen": "list", "title": "PARTS", "page": 0, "items": [
    {"name": "Square", "sub": "22 min · place on the plate", "preview": "pic", "action": ("file", "/sd/a.nc")},
    {"name": "Not read", "sub": "Not read yet · tap to read it", "preview": "", "action": ("file", "/sd/b.nc")}]}
p = tft.Layout(fonts)
p._ui_list(lst)
imgs = [o for o in p.ops if o["o"] == "img"]
check(len(imgs) == 1 and imgs[0]["src"] == "pic" and imgs[0]["w"] == 32, "the job list: a picture beside a file that has one")
names = [o for o in p.ops if o["o"] == "t" and o["t"] in ("Square", "Not read")]
check(all(o["x"] == 50 for o in names), "names line up beside the pictures, also where one is missing")
p2 = tft.Layout(fonts)
p2._ui_list(dict(lst, items=[dict(i, preview="") for i in lst["items"]]))
check(not any(o["o"] == "img" for o in p2.ops) and all(o["x"] == 16 for o in p2.ops if o["o"] == "t" and o["t"] in ("Square", "Not read")),
      "no pictures in a folder: the list looks as before")
shot = tft.paint(p.ops, fonts)
box = shot.crop((10, 29, 42, 61))
check(len(set(box.getdata())) > 20, "the thumbnail is painted")
big = tft.paint(p.ops, fonts, scale=2)
check(len(set(big.crop((20, 58, 84, 122)).getdata())) > len(set(box.getdata())), "sharper on a 640 x 480 screen")

rv = tft.Layout(fonts)
rv._ui_review({"screen": "review", "title": "Square · placed at X10 Y0", "lines": ["Probe first, then T1 (Red sticker)",
              "Run time about 22 min"], "why": "", "note": "", "back": ("back_check",), "preview": "pic"})
ri = [o for o in rv.ops if o["o"] == "img"]
check(len(ri) == 1 and ri[0]["w"] == 88 and ri[0]["x"] > tft.W / 2, "the review screen: the part beside the title")
title = next(o for o in rv.ops if o["o"] == "t" and o["t"].startswith("Square"))
check(title["x"] + 0 < ri[0]["x"], "the title stays clear of it")

missing = tft.Previews(lambda md5: None)
check(missing.get("x", 32, 32) is None, "no picture yet: nothing drawn")
calls = []
later = tft.Previews(lambda md5: calls.append(md5) or None)
later.get("x", 32, 32)
later.get("x", 32, 32)
check(calls == ["x"], "and not asked for again at every redraw")
later.missing["x"] -= later.RETRY_S + 1
later.get("x", 32, 32)
check(calls == ["x", "x"], "but again a little later (it is drawn in the background)")

print("on the phone page")
page = open(os.path.join(ROOT, "z1screen", "web", "index.html"), encoding="utf-8").read()
check('id="jobPic"' in page and 'id="lastPic"' in page and '"preview/" + md5 + ".png"' in page,
      "the running job and the last job show their part")

print("all preview checks passed")
