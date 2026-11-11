"""Tries phone notifications without the Z1: plays notify-test.nc (five bit changes) on a pretend Z1 on
this computer, presses the front button by itself a few seconds after each stop, and sends the real
notifications to your ntfy topic.

  python3 tools/notify_test.py --topic bitcue-yourtopic           to ntfy.sh (about 2 minutes)
  python3 tools/notify_test.py --topic bitcue-yourtopic --soon 1  with the early warning, 1 min before each stop
  python3 tools/notify_test.py --pause --alarm                    also a pause after the first stop, and an
                                                                  alarm (probe not detected) at the last stop
  python3 tools/notify_test.py                                    no topic: shows what would be sent, sends nothing

Options: --config z1screen.json (your bit names and sticker colors), --flip (draw the screen upside down,
as the settings page's switch does), --server URL (your own ntfy
server), --token TOKEN, --wait S (seconds before the button is
pressed, default 8), --speed N (how much faster than real time the pretend Z1 runs, default 10),
--file FILE (another job file). While it runs, http://127.0.0.1:47580 shows the phone screen.
Needs nothing but Python; the pretend Z1 and the screen only listen on this computer.
"""
import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for p in (ROOT, os.path.join(ROOT, "tests")):
    sys.path.insert(0, p)
from sim_app import SimApp  # noqa: E402
from sim_machine import SimMachine  # noqa: E402
from z1screen import config, gcode, notify  # noqa: E402
from z1screen.hub import Hub  # noqa: E402
from z1screen.jobs import JobStore  # noqa: E402
from z1screen.web import Web  # noqa: E402

WEB, RELAY, DISC, MACHINE = 47580, 47522, 47533, 47524
JOB = "/sd/gcodes/notify-test.nc"


def stand_in():
    """A local ntfy stand-in that prints what it gets (for a run without a topic)."""
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            print("    would send: %s | %s" % (msg["title"], msg["message"].replace("\n", " | ")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_port


def make_cfg(args, conf) -> dict:
    """The pretend Z1's screen settings: the settings file's bits and screen, this tool's ports, and conf."""
    cfg = config.load(args.config) if args.config else copy.deepcopy(config.DEFAULTS)
    cfg.update({"machine_ip": "", "machine_name": "", "start_from_screen": False})
    cfg.update({"discovery_port": DISC, "relay_port": RELAY, "web_port": WEB, "announce_to": ["127.0.0.1"],
                "forward_ports": [], "poll_interval": 0.3, "notify": conf})
    if args.flip:
        cfg["tft"] = dict(cfg.get("tft") or {}, flip=True)
    return cfg


def record_sends(notifier) -> list:
    """Every notification the notifier tries, in order, with its result (also those sent close together)."""
    results = []
    send_now = notifier.send_now

    def recorded(conf, msg):
        r = dict(send_now(conf, msg))
        results.append(r)
        return r
    notifier.send_now = recorded
    return results


async def report(results) -> None:
    shown = 0
    while True:
        while shown < len(results):
            r = results[shown]
            shown += 1
            print("  %s %s%s" % ("sent:" if r["ok"] else "NOT SENT:", r["title"], "" if r["ok"] else " (%s)" % r["error"]),
                  flush=True)
        await asyncio.sleep(0.1)


async def run(args, conf) -> int:
    data = open(args.file, "rb").read()
    store = JobStore(tempfile.mkdtemp(prefix="bitcue-notify-"), [])
    store._index(gcode.analyze(data, JOB))
    cfg = make_cfg(args, conf)
    sim = SimMachine(port=MACHINE, announce_to=("127.0.0.1", DISC), speed=args.speed, probe_first=False)
    sim.files[JOB] = data
    await sim.start()
    hub = Hub(cfg, store)
    results = record_sends(hub.notifier)
    reporter = asyncio.create_task(report(results))
    task = asyncio.create_task(hub.run(Web(hub, WEB)))
    t0 = time.time()
    while not (hub.own is not None and hub.status is not None):
        if time.time() - t0 > 15:
            print("The pretend Z1 did not connect.")
            return 1
        await asyncio.sleep(0.2)
    app = await SimApp().connect("127.0.0.1", RELAY)
    print("Playing %s on the pretend Z1 (%dx speed). Phone screen: http://127.0.0.1:%d" %
          (os.path.basename(args.file), args.speed, WEB))
    await app.command("play " + JOB)
    total = len(gcode.Profile(gcode.analyze(data, JOB)).changes)
    stops, started, paused, alarm_t = 0, False, False, None
    t0 = time.time()
    while time.time() - t0 < 900:
        v = hub.view or {}
        mode = v.get("mode")
        if mode in ("running", "toolchange"):
            started = True
        if mode == "toolchange" and sim.waiting:
            stops += 1
            tc = v["toolchange"]
            if args.alarm and stops == total:
                print("Stop %d: load %s. The pretend Z1 stops with an alarm instead (probe not detected)." %
                      (stops, tc["code"]))
                await asyncio.sleep(args.wait)
                sim.waiting, sim.playing, sim.state, sim.halt = False, False, "Alarm", 12
                alarm_t = time.time()
                continue
            print("Stop %d: load %s (%s). Pressing the front button in %g s." %
                  (stops, tc["code"], tc.get("sticker") or "no color", args.wait))
            await asyncio.sleep(args.wait)
            sim.confirm()
            await asyncio.sleep(1.0)
        elif args.pause and not paused and stops >= 1 and mode == "running" and sim.state == "Run" and not sim.waiting:
            # only while the job really runs: a pause started during a stop would leave the pretend Z1 stuck
            paused = True
            hold = max(args.wait, 2.0)          # the screen reads the status about once a second
            print("Pausing the job for %g s." % hold)
            sim.state = "Hold"
            await asyncio.sleep(hold)
            if sim.state == "Hold":
                sim.state = "Run"
        elif alarm_t is not None and time.time() - alarm_t > 3:
            break
        elif started and mode == "idle":
            break
        await asyncio.sleep(0.3)
    await asyncio.sleep(1.5)        # the last notification's thread
    await asyncio.sleep(0.2)        # and the reporter
    reporter.cancel()
    print("Done: %d stops, %d notifications sent, %d not sent." %
          (stops, sum(1 for r in results if r["ok"]), sum(1 for r in results if not r["ok"])))
    await app.close()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    return 0 if stops else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--topic", default="", help="your ntfy topic (empty: send nothing, show what would be sent)")
    ap.add_argument("--server", default="https://ntfy.sh")
    ap.add_argument("--token", default="")
    ap.add_argument("--soon", type=int, default=0, help="early warning, minutes before each stop (0: off)")
    ap.add_argument("--wait", type=float, default=8.0, help="seconds before the front button is pressed")
    ap.add_argument("--speed", type=float, default=10.0)
    ap.add_argument("--file", default=os.path.join(ROOT, "notify-test.nc"))
    ap.add_argument("--config", default="", help="a z1screen.json, for its bit names and sticker colors")
    ap.add_argument("--pause", action="store_true", help="pause the job once, after the first stop")
    ap.add_argument("--alarm", action="store_true", help="stop with an alarm at the last stop instead of finishing")
    ap.add_argument("--flip", action="store_true", help="draw the screen upside down (the settings page's switch)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(name)s %(levelname)s %(message)s")
    srv = None
    if args.topic:
        conf = notify.clean({"server": args.server, "topic": args.topic, "token": args.token, "soon_min": args.soon}, {})
        print("Sending to topic %s on %s." % (conf["topic"], conf["server"]))
    else:
        srv, base = stand_in()
        conf = notify.clean({"server": base, "topic": "bitcue-dry-run", "soon_min": args.soon}, {})
        print("No --topic: nothing leaves this computer; each notification is printed instead.")
    try:
        return asyncio.run(run(args, conf))
    finally:
        if srv:
            srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
