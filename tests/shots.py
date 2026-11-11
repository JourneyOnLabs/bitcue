"""Render the web screen in each state recorded by run_sim.py (tests/out/snapshots.json) at several display
sizes, with Playwright and Chromium (not part of run_all.sh).

  python3 tests/shots.py [out_dir] [state,state...]
"""
import json, os, sys, asyncio
from playwright.async_api import async_playwright
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out", "web-shots")
page_path = os.path.abspath(os.path.join(HERE, "..", "z1screen", "web", "index.html"))
snaps = json.load(open(os.path.join(HERE, "out", "snapshots.json")))
want = sys.argv[2].split(",") if len(sys.argv) > 2 else None
sizes = [s for s in [("kiosk", 1024, 600), ("hd", 1920, 1080), ("phone", 390, 844)] if not os.environ.get("ONLY") or s[0] == os.environ["ONLY"]]

async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for sname, w, h in sizes:
            pg = await b.new_page(viewport={"width": w, "height": h})
            await pg.add_init_script("window.Z1_DEMO = true;")
            await pg.goto("file://" + page_path)
            await pg.wait_for_timeout(300)
            for name, st in snaps:
                if want and name not in want:
                    continue
                await pg.evaluate("s => window.Z1Screen.render(s)", st)
                await pg.wait_for_timeout(120)
                path = os.path.join(OUT, "%s-%s.png" % (name, sname))
                await pg.screenshot(path=path, full_page=(sname == "phone"))
            await pg.close()
        await b.close()
asyncio.run(main())
