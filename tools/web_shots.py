"""Pictures of the phone page and the settings page, for the README.

  python3 tools/web_shots.py [out_dir]          (default docs/screens/web)

Starts the screen service (the Hub and its web page) on this computer with a pretend Z1 (tests/sim_machine.py)
that holds only the made-up sample jobs (tools/samples.py), plays the sample sign through every tool change,
and takes pictures with headless Google Chrome (driven over --remote-debugging-pipe, so nothing else needs
installing). Everything stays on 127.0.0.1: Chrome gets a dead proxy and a throwaway profile, and the
service, the pretend Z1 and Chrome are all stopped at the end, whatever happens.

Phone pictures are 390 x 844 CSS pixels at device scale 2; the settings page is also taken whole at 1100 px
wide. Set CHROME to use another Chrome or Chromium.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS)       # common.py puts the project and tools/ on the path too
from common import SIGN, TAG, cfg as test_cfg, make_library, sample_files  # noqa: E402
from sim_machine import SimMachine  # noqa: E402
from z1screen.hub import Hub  # noqa: E402
from z1screen.jobs import JobStore  # noqa: E402
from z1screen.web import Web  # noqa: E402

WEB = 47080
CHROME = os.environ.get("CHROME") or next(
    (p for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 shutil.which("google-chrome") or "", shutil.which("chromium") or "",
                 shutil.which("chromium-browser") or "") if p and os.path.exists(p)), "")
PHONE = (390, 844, 2)
DESKTOP = (1100, 900, 1)


# ---------------------------------------------------------------- the service and the pretend Z1
class Service:
    """The Hub with its web page and a pretend Z1, on an event loop in a thread of their own."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error = None
        self.dirs = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(30) or self.error:
            raise RuntimeError("the screen service didn't start: %s" % (self.error or "timed out"))

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except BaseException as e:      # noqa: BLE001
            self.error = e
            self.ready.set()
        finally:                        # let every task finish cancelling before the loop closes
            left = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
            for t in left:
                t.cancel()
            if left:
                self.loop.run_until_complete(asyncio.gather(*left, return_exceptions=True))
            self.loop.close()

    async def _main(self):
        cfg = test_cfg(plate=None)
        cfg.update({"discovery_port": 47333, "relay_port": 47224, "web_port": WEB, "announce_to": ["127.0.0.1"],
                    "announce_port": 47334, "forward_ports": [], "poll_interval": 0.5, "announce_interval": 0.5,
                    "read_machine_config": False})
        lib = make_library([SIGN, TAG], seed=True)
        state = tempfile.mkdtemp(prefix="z1web-state-")
        self.dirs += [lib, state]
        self.hub = Hub(cfg, JobStore(state, [lib]))
        # the address the pages show, as on a real Pi (not this computer's test port)
        self.hub.settings_url = lambda: "http://z1screen.local:8080/settings"
        self.sim = SimMachine(port=47222, announce_to=("127.0.0.1", 47333), speed=60)
        files = sample_files()
        for p in (SIGN, TAG):
            self.sim.files[p] = files[p]
        await self.sim.start()
        self.task = asyncio.ensure_future(self.hub.run(Web(self.hub, WEB)))
        t = time.time()
        while self.hub.own is None or self.hub.status is None:
            if time.time() - t > 20:
                raise RuntimeError("the Hub never connected to the pretend Z1")
            await asyncio.sleep(0.1)
        self.ready.set()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    def call(self, fn, *args):
        """Run fn(*args) on the service's loop and return what it returns."""
        done, box = threading.Event(), {}

        def go():
            try:
                box["v"] = fn(*args)
            finally:
                done.set()
        self.loop.call_soon_threadsafe(go)
        done.wait(10)
        return box.get("v")

    def stop(self):
        if self.thread.is_alive():
            def end():
                for t in asyncio.all_tasks(self.loop):
                    t.cancel()
                srv = getattr(getattr(self, "sim", None), "server", None)
                if srv is not None:
                    srv.close()
            self.loop.call_soon_threadsafe(end)
            self.thread.join(10)
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)


def state():
    with urllib.request.urlopen("http://127.0.0.1:%d/state" % WEB, timeout=5) as r:
        return json.loads(r.read())


def wait_state(pred, timeout, what):
    t = time.time()
    while time.time() - t < timeout:
        st = state()
        if pred(st):
            return st
        time.sleep(0.1)
    raise RuntimeError("timed out waiting for " + what)


# ---------------------------------------------------------------- headless Chrome over a pipe
class Chrome:
    """Headless Chrome, spoken to with the DevTools protocol over --remote-debugging-pipe (fds 3 and 4)."""

    def __init__(self):
        if not CHROME:
            raise RuntimeError("Google Chrome not found; set CHROME to its program")
        self.profile = tempfile.mkdtemp(prefix="z1web-chrome-")
        to_r, to_w = os.pipe()        # we write commands, Chrome reads fd 3
        from_r, from_w = os.pipe()    # Chrome writes fd 4, we read
        args = [CHROME, "--headless=new", "--remote-debugging-pipe", "--user-data-dir=" + self.profile,
                "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                "--disable-component-update", "--disable-sync", "--disable-extensions", "--disable-default-apps",
                "--disable-domain-reliability", "--disable-client-side-phishing-detection", "--metrics-recording-only",
                "--no-pings", "--mute-audio", "--hide-scrollbars", "--force-color-profile=srgb",
                "--proxy-server=http://127.0.0.1:9", "about:blank"]   # 127.0.0.1 itself is never proxied

        def fds():               # in the child, before Chrome starts: the two pipe ends as fds 3 and 4
            a, b = os.dup(to_r), os.dup(from_w)
            os.dup2(a, 3)
            os.dup2(b, 4)
        self.proc = subprocess.Popen(args, preexec_fn=fds, close_fds=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(to_r)
        os.close(from_w)
        self.w = os.fdopen(to_w, "wb", buffering=0)
        self.r = os.fdopen(from_r, "rb", buffering=0)
        self.n = 0
        self.buf = b""
        self.lock = threading.Lock()
        self.replies = {}
        self.cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()
        tid = self.send("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.send("Target.attachToTarget", targetId=tid, flatten=True)["sessionId"]
        self.cmd("Page.enable")

    def _reader(self):
        while True:
            chunk = self.r.read(65536)
            if not chunk:
                with self.cv:
                    self.replies[None] = True
                    self.cv.notify_all()
                return
            self.buf += chunk
            while b"\0" in self.buf:
                msg, self.buf = self.buf.split(b"\0", 1)
                m = json.loads(msg)
                if "id" in m:
                    with self.cv:
                        self.replies[m["id"]] = m
                        self.cv.notify_all()

    def send(self, method, session=None, **params):
        with self.lock:
            self.n += 1
            i = self.n
        m = {"id": i, "method": method, "params": params}
        if session:
            m["sessionId"] = session
        self.w.write(json.dumps(m).encode() + b"\0")
        with self.cv:
            ok = self.cv.wait_for(lambda: i in self.replies or None in self.replies, 60)
            if not ok or i not in self.replies:
                raise RuntimeError("Chrome stopped answering (%s)" % method)
            rep = self.replies.pop(i)
        if "error" in rep:
            raise RuntimeError("%s: %s" % (method, rep["error"]))
        return rep.get("result", {})

    def cmd(self, method, **params):
        return self.send(method, self.session, **params)

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def size(self, w, h, dsf, mobile):
        self.cmd("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=dsf, mobile=mobile)

    def open(self, url):
        self.cmd("Page.navigate", url=url)
        t = time.time()
        while self.js("document.readyState") != "complete" or self.js("location.href") != url:
            if time.time() - t > 20:
                raise RuntimeError("Chrome couldn't open %s (it shows %s)" % (url, self.js("location.href")))
            time.sleep(0.1)
        self.js("document.fonts.ready.then(function () { return 1; })")

    def shot(self, path, full=False):
        clip = None
        if full:
            m = self.cmd("Page.getLayoutMetrics")
            cs = m.get("cssContentSize") or m["contentSize"]
            clip = {"x": 0, "y": 0, "width": cs["width"], "height": cs["height"], "scale": 1}
        params = {"format": "png"}
        if clip:
            params.update(clip=clip, captureBeyondViewport=True)
        data = self.cmd("Page.captureScreenshot", **params)["data"]
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(data))
        print("  " + os.path.relpath(path, ROOT))

    def close(self):
        try:
            self.send("Browser.close")
        except Exception:      # noqa: BLE001
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        for f in (self.w, self.r):
            try:
                f.close()
            except OSError:
                pass
        shutil.rmtree(self.profile, ignore_errors=True)


# ---------------------------------------------------------------- the pictures
def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screens", "web")
    os.makedirs(out, exist_ok=True)
    base = "http://127.0.0.1:%d/" % WEB
    svc, chrome = Service(), None
    try:
        svc.start()
        chrome = Chrome()
        p = lambda name: os.path.join(out, name + ".png")   # noqa: E731

        wait_state(lambda s: s.get("mode") == "idle", 20, "Ready")
        print("settings page")
        chrome.size(*DESKTOP, False)
        chrome.open(base + "settings")
        time.sleep(2.0)                      # its sections fill in from the api/ requests
        chrome.shot(p("settings-desktop"), full=True)
        chrome.size(*PHONE, True)
        chrome.open(base + "settings")
        time.sleep(2.0)
        chrome.shot(p("settings-phone"))

        print("phone job screen")
        chrome.open(base)
        time.sleep(1.5)
        chrome.shot(p("phone-ready"))

        svc.call(svc.sim.start_play, SIGN)
        stops = {0: "phone-stop-probe", 1: "phone-stop-t1", 2: "phone-stop-t2", 3: "phone-stop-t3"}
        taken = set()
        ran = False
        t0 = time.time()
        while time.time() - t0 < 240:
            st = state()
            mode = st.get("mode")
            if mode == "toolchange" and svc.sim.waiting:
                tool = st["toolchange"]["tool"]
                if tool in stops and tool not in taken:
                    time.sleep(1.2)
                    chrome.shot(p(stops[tool]))
                    taken.add(tool)
                svc.call(svc.sim.confirm)
                time.sleep(0.6)
            elif (mode == "running" and not ran and 2 in taken and (st.get("job") or {}).get("known")
                  and ((st["job"].get("next") or {}).get("in") or 0) > 60):
                svc.call(setattr, svc.sim, "speed", 1.0)       # hold the countdown still for the picture
                time.sleep(2.0)
                chrome.shot(p("phone-running"))
                svc.call(setattr, svc.sim, "speed", 60.0)
                ran = True
            elif mode == "idle" and st.get("last_job") and taken:
                break
            time.sleep(0.15)
        else:
            raise RuntimeError("the sample job didn't finish")
        wait_state(lambda s: s.get("mode") == "idle" and (s.get("last_job") or {}).get("done"), 20, "job done")
        time.sleep(1.5)
        chrome.shot(p("phone-done"))
        missing = [n for t, n in stops.items() if t not in taken] + ([] if ran else ["phone-running"])
        if missing:
            raise RuntimeError("no picture of: " + ", ".join(missing))
        print("wrote the web pictures to", out)
    finally:
        if chrome is not None:
            chrome.close()
        svc.stop()


if __name__ == "__main__":
    main()
