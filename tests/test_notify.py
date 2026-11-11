"""Phone notifications at a bit change (z1screen/notify.py): when they are sent, what they say, the
settings page's choices, and the request sent to the ntfy server (a local stand-in, no network)."""
import argparse, asyncio, copy, importlib.util, json, os, re, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from common import ROOT, SIGN, check, make_library, status as st
from z1screen import config, notify, prefs
from z1screen.hub import Hub
from z1screen.jobs import JobStore

ON = {"notify": {"topic": "bitcue-test", "stops": True, "soon_min": 0}}


class Quiet(notify.Notifier):
    """Sends nothing; keeps what would have been sent."""
    def __init__(self):
        super().__init__(send=lambda conf, msg: None)
        self.out = []

    def send_later(self, conf, msg):
        self.out.append(msg)
        self.send_now(conf, msg)


def stop(code="T3", index=3, file="sign.nc", out=None):
    tc = {"tool": int(code[1:]) if code.startswith("T") else 0, "code": code, "label": "30° V-bit",
          "sticker": "Blue", "color": "#1565C0", "index": index, "total": 5}
    if out:
        tc["out"] = out
    return {"mode": "toolchange", "toolchange": tc, "job": {"file": file, "name": "Sample sign"}}


def running(nx_in=None, index=3, file="sign.nc"):
    job = {"file": file, "name": "Sample sign", "next": None}
    if nx_in is not None:
        job["next"] = {"code": "T3", "label": "30° V-bit", "sticker": "Blue", "in": nx_in, "index": index}
    return {"mode": "running", "job": job}


print("one notification per stop")
n = Quiet()
check(n.watch(stop(), ON, now=100) and len(n.out) == 1, "the Z1 stops for T3: sent")
check(not n.watch(stop(), ON, now=101), "still stopped: nothing more")
n.watch({"mode": "connecting"}, ON, now=105)
check(not n.watch(stop(), ON, now=108), "the status dropped out for a moment: not sent again")
n.watch(running(), ON, now=110)
check(not n.watch(stop(), ON, now=112), "cutting for 2 s, then the same stop again: not sent again")
n.watch(running(), ON, now=120)
n.watch(running(), ON, now=145)
check(len(n.watch(stop(), ON, now=146)) == 1, "after 25 s of cutting, the same bit and change count again")
n.watch(running(), ON, now=150)
check(len(n.watch(stop("T1", 4), ON, now=151)) == 1, "the next stop is sent straight away")
check(n.last["ok"] and n.last["title"] == "Z1 stopped: load T1", "the last one is kept for the settings page")

print("nothing without a topic, or with stops turned off")
n = Quiet()
check(not n.watch(stop(), {}, now=1) and not n.out, "no topic: nothing sent")
n = Quiet()
check(not n.watch(stop(), {"notify": {"topic": "x", "stops": False}}, now=1), "stops off: nothing sent")

print("an early warning, once per change")
n = Quiet()
soon = {"notify": {"topic": "bitcue-test", "stops": True, "soon_min": 3}}
check(not n.watch(running(400), soon, now=1), "T3 in 6 min 40 s: not yet")
sent = n.watch(running(170), soon, now=2)
check(len(sent) == 1 and sent[0]["title"] == "Z1: T3 in about 3 min", "T3 in 2 min 50 s: warned (%s)" % sent[0]["title"])
check("Blue sticker · 30° V-bit" in sent[0]["message"] and "Sample sign" in sent[0]["message"], "with the sticker and the job")
check(not n.watch(running(120), soon, now=3), "only once for that change")
check(len(n.watch(running(100, index=4), soon, now=4)) == 1, "the next change gets its own warning")
check(not n.watch(running(170), ON, now=5), "no early warning when it's off")
n.watch({"mode": "idle"}, soon, now=6)
check(len(n.watch(running(170), soon, now=7)) == 1, "a new job warns again for its first change")

print("one notification per alarm during a job")


def alarm(reason="Probe not detected: check the probe cable"):
    return {"mode": "alarm", "alarm": reason, "job": None}


def paused(file="sign.nc"):
    return {"mode": "paused", "job": {"file": file, "name": "Sample sign", "next": None}}


n = Quiet()
check(not n.watch(alarm("Machine not homed"), ON, now=1), "an alarm with no job before it (power on): nothing sent")
n.watch({"mode": "idle"}, ON, now=2)
n.watch(running(), ON, now=10)
sent = n.watch(alarm(), ON, now=20)
check(len(sent) == 1 and sent[0]["title"] == "Z1 alarm: job stopped" and sent[0]["priority"] == 5,
      "the job stops with an alarm: one notification, at the highest priority")
check(sent[0]["message"].split("\n") == ["Probe not detected: check the probe cable", "Job: Sample sign"],
      "with the reason and the job (%s)" % sent[0]["message"].replace("\n", " | "))
check(not n.watch(alarm(), ON, now=21), "still stopped: nothing more")
n.watch({"mode": "connecting"}, ON, now=25)
check(not n.watch(alarm(), ON, now=28), "the status dropped out for a moment: not sent again")
n.watch({"mode": "idle"}, ON, now=40)
check(not n.watch(alarm("Machine not homed"), ON, now=45),
      "unlocked, then an alarm with no job running: nothing sent")
n.watch(running(), ON, now=50)
check(len(n.watch(alarm("Cover opened while running"), ON, now=55)) == 1,
      "the next job stops with an alarm moments after starting: sent again")
n.watch({"mode": "idle"}, ON, now=56)
n.watch(running(), ON, now=56.5)
check(not n.watch(alarm("Cover opened while running"), ON, now=57), "but not for a flicker under 3 s")
n = Quiet()
n.watch(running(), ON, now=1)
check(not n.watch(alarm(), {"notify": dict(ON["notify"], alarms=False)}, now=2), "alarms switched off: nothing sent")

print("one notification per pause")
n = Quiet()
n.watch(running(), ON, now=1)
sent = n.watch(paused(), ON, now=5)
check(len(sent) == 1 and sent[0]["title"] == "Z1 paused" and sent[0]["message"] == "Sample sign is paused.",
      "the job is paused: one notification with the job's name")
check(not n.watch(paused(), ON, now=6), "still paused: nothing more")
n.watch(running(), ON, now=10)
check(not n.watch(paused(), ON, now=15), "resumed and paused again within 20 s: not sent again")
n.watch(running(), ON, now=20)
n.watch(running(), ON, now=45)
check(len(n.watch(paused(), ON, now=46)) == 1, "paused again after 25 s of cutting: sent again")
n = Quiet()
n.watch(running(), ON, now=1)
check(not n.watch(paused(), {"notify": dict(ON["notify"], pauses=False)}, now=2), "pauses switched off: nothing sent")
n = Quiet()
n.watch(running(), ON, now=1)
n.watch(paused(), ON, now=2)
check(len(n.watch(alarm("Stopped by hand"), ON, now=3)) == 1, "stopped while paused: the alarm is sent too")

print("what a stop notification says, from the hub's own screen data")
store = JobStore(tempfile.mkdtemp(), [make_library((SIGN,))])
cfg = copy.deepcopy(config.DEFAULTS)
cfg["notify"] = dict(cfg["notify"], topic="bitcue-test")
hub = Hub(cfg, store)
hub.notifier = Quiet()
hub.on_app_command("play " + SIGN)
hub.on_status(st("Run", 10, 0, 1))
hub.on_status(st("Tool", 40, 20, 300, tool=1, target=2))
v = hub.compute_view()
check(v["mode"] == "toolchange", "the hub shows a stop for T2")
hub._notify(v)
msg = hub.notifier.out[-1]
check(msg["title"] == "Z1 stopped: load T2", "title: " + msg["title"])
check("Yellow sticker" in msg["message"] and "T1 goes back in the Red container." in msg["message"],
      "the new bit's sticker and where the old one goes")
check("Change " in msg["message"] and "of " in msg["message"] and msg["priority"] == 4,
      "which change it is, at high priority")
check(msg["click"].endswith(":8080/") and "/settings" not in msg["click"], "tapping it opens the phone screen")
hub._notify(hub.compute_view())
check(len(hub.notifier.out) == 1, "the next screen update doesn't send it again")

print("an alarm from the hub's own screen data")
hub5 = Hub(copy.deepcopy(cfg), JobStore(tempfile.mkdtemp(), [make_library((SIGN,))]))
hub5.notifier = Quiet()
hub5.on_app_command("play " + SIGN)
hub5.on_status(st("Run", 10, 0, 1))
hub5._notify(hub5.compute_view())
hub5.on_status(st("Alarm", 40, 20, 300, halt=12))
v = hub5.compute_view()
check(v["mode"] == "alarm", "the hub shows the alarm")
hub5._notify(v)
msg = hub5.notifier.out[-1]
check(msg["title"] == "Z1 alarm: job stopped" and msg["message"].startswith("Probe not detected: check the probe cable"),
      "the Z1's reason for the halt: " + msg["message"].split("\n")[0])
check("Job: " in msg["message"], "and the job it stopped")

print("the probe")
probe = stop("Probe", None)
probe["toolchange"]["label"] = "Probe"
msg = notify.stop_message(probe)
check(msg["title"] == "Z1 stopped: load the probe" and "Probe" not in msg["message"].split("\n")[0],
      "named as the probe, without repeating the name")

print("the settings page's choices")
old = {"token": "tk_saved"}
c = notify.clean({"server": "https://ntfy.example.com/", "topic": "bitcue-abc_9", "stops": True, "soon_min": "5"}, old)
check(c == {"server": "https://ntfy.example.com", "topic": "bitcue-abc_9", "token": "tk_saved", "stops": True,
            "soon_min": 5, "alarms": True, "pauses": True}, "cleaned, and a token left out keeps the saved one")
c = notify.clean({"topic": "x", "alarms": False, "pauses": False}, {})
check(c["alarms"] is False and c["pauses"] is False and c["stops"] is True,
      "alarms and pauses can be switched off on their own; left out, they are on")
check(notify.clean({"topic": "x", "token": ""}, old)["token"] == "", "an empty token removes it")
check(notify.clean({}, {})["server"] == "https://ntfy.sh", "ntfy.sh unless another server is typed")
for bad, why in (({"topic": "my topic"}, "a space in the topic"), ({"topic": "a/b"}, "a slash in the topic"),
                 ({"server": "ntfy.sh"}, "a server without https://"), ({"soon_min": 90}, "a warning over 60 min"),
                 ({"token": "a b"}, "a token with a space")):
    try:
        notify.clean(bad, {})
        check(False, "refused: " + why)
    except ValueError:
        check(True, "refused: " + why)

state = tempfile.mkdtemp()
store2 = JobStore(state, [make_library((SIGN,))])
hub2 = Hub(copy.deepcopy(config.DEFAULTS), store2)
info = hub2.set_setup({"notify": {"topic": "bitcue-mine", "token": "tk_secret", "soon_min": 2, "pauses": False}})
check(info["notify"]["alarms"] is True and info["notify"]["pauses"] is False, "the page gets the alarm and pause switches")
check(info["notify"]["topic"] == "bitcue-mine" and info["notify"]["token_set"] and "token" not in info["notify"],
      "saved; the page is told a token is set, never the token")
check("tk_secret" not in json.dumps(info), "the token is nowhere in what the page gets")
hub3 = Hub(copy.deepcopy(config.DEFAULTS), JobStore(state, [make_library((SIGN,))]))
check(notify.settings(hub3.cfg)["topic"] == "bitcue-mine" and notify.settings(hub3.cfg)["token"] == "tk_secret",
      "kept after a restart (prefs.json)")
check(prefs.load(state)["notify"]["soon_min"] == 2, "in prefs.json with the other setup choices")
hub3.set_setup({"notify": {"topic": ""}})
check(not hub3.setup_info()["notify"]["topic"], "turned off")

print("the request to the ntfy server")
got = []


class Server(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        got.append((self.path, dict(self.headers), json.loads(body.decode("utf-8"))))
        code = 403 if got[-1][2]["topic"] == "locked" else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b'{"id":"x"}')

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 0), Server)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_port
conf = notify.settings({"notify": {"server": base, "topic": "bitcue-test", "token": "tk_1"}})
notify.post(conf, {"title": "Z1 stopped: load T3", "message": "Blue sticker · 30° V-bit", "priority": 4})
path, headers, body = got[-1]
check(path == "/" and body["topic"] == "bitcue-test", "published to the topic as JSON")
check(body["title"] == "Z1 stopped: load T3" and body["message"] == "Blue sticker · 30° V-bit",
      "text with ° and · arrives as it was")
check(headers.get("Authorization") == "Bearer tk_1", "with the access token")
try:
    notify.post(dict(conf, topic="locked"), {"title": "x", "message": "y"})
    check(False, "a refusal is reported")
except OSError as exc:
    check("403" in str(exc), "a refusal is reported (%s)" % exc)

print("Send a test")
hub4 = Hub(copy.deepcopy(config.DEFAULTS), JobStore(tempfile.mkdtemp(), [make_library((SIGN,))]))
try:
    asyncio.run(hub4.notify_test())
    check(False, "needs a topic")
except ValueError as exc:
    check("topic" in str(exc), "needs a topic first")
hub4.cfg["notify"] = {"server": base, "topic": "bitcue-test"}
info = asyncio.run(hub4.notify_test())
check(info["last"]["ok"] and got[-1][2]["title"] == "BitCue test", "the test arrives")
check(got[-1][2]["message"] == "Notifications from BitCue work.", "named after BitCue while no Z1 has been found")
hub4.cfg["notify"] = {"server": base, "topic": "locked"}
try:
    asyncio.run(hub4.notify_test())
    check(False, "a failed test says why")
except ValueError as exc:
    check("403" in str(exc), "a failed test says why (%s)" % exc)
srv.shutdown()

print("the settings page's notification buttons")
page = open(os.path.join(ROOT, "z1screen", "web", "settings.html"), encoding="utf-8").read()
form = page[page.index('id="notifyForm"'):page.index("</form>", page.index('id="notifyForm"'))]
row = re.search(r'<div class="([^"]*)"><button class="btn" type="submit">Save', form)
check(row and "wide" in row.group(1).split(), "Save, Send a test and Turn off sit on a row of their own, not beside a box")

print("the test tool (tools/notify_test.py)")
spec = importlib.util.spec_from_file_location("notify_test", os.path.join(ROOT, "tools", "notify_test.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
for flip in (True, False):
    cfg_t = tool.make_cfg(argparse.Namespace(config="", flip=flip), notify.clean({"topic": "x"}, {}))
    screen = Hub(cfg_t, JobStore(tempfile.mkdtemp(), [])).screen_info()
    check(screen["flip"] is flip, "--flip %s: the pretend Z1's screen is %s" % (flip, "upside down" if flip else "upright"))
n = notify.Notifier(send=lambda conf, msg: time.sleep(0.05))
got = tool.record_sends(n)
conf_t = notify.settings({"notify": {"topic": "x"}})
for title in ("Z1 stopped: load T2", "Z1 paused", "Z1: T6 in about 1 min"):
    n.send_later(conf_t, {"title": title, "message": ""})
deadline = time.time() + 5
while len(got) < 3 and time.time() < deadline:
    time.sleep(0.05)
check([r["title"] for r in got] == ["Z1 stopped: load T2", "Z1 paused", "Z1: T6 in about 1 min"] or
      sorted(r["title"] for r in got) == sorted(["Z1 stopped: load T2", "Z1 paused", "Z1: T6 in about 1 min"]),
      "three notifications sent at once are all recorded")
p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "notify_test.py"), "--soon", "1", "--pause", "--alarm",
                    "--wait", "1", "--speed", "30"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   universal_newlines=True, timeout=240)
would = [l.split("would send: ", 1)[1].split(" | ")[0] for l in p.stdout.splitlines() if "would send: " in l]
sent = [l.split("sent: ", 1)[1] for l in p.stdout.splitlines() if l.strip().startswith("sent: ")]
check(p.returncode == 0 and len(would) >= 11, "a dry run with a pause and an alarm sends %d notifications" % len(would))
check(sorted(sent) == sorted(would), "and lists every one as sent, also those sent close together")
check("Z1 paused" in sent and any(t.startswith("Z1 alarm") for t in sent) and sum(t.startswith("Z1 stopped") for t in sent) == 5,
      "5 stops, the pause and the alarm among them")
check("%d notifications sent, 0 not sent" % len(would) in p.stdout, "the summary counts them")

print("all notification checks passed")
