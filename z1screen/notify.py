"""Phone notifications at a bit change, sent through ntfy (https://ntfy.sh).

With the free ntfy app (iPhone, Android) or the ntfy web page subscribed to a topic, the phone shows
a notification when the Z1 stops for a bit change: the bit, its sticker color, and the container the
old bit goes back into. It can also warn a few minutes before the stop, to walk back to the machine,
and tell you when a job stops with an alarm or is paused. Each stop, alarm or pause sends one
notification, however long it lasts.

Anyone who knows a topic's name can read it on ntfy.sh, so the settings page suggests a long random
one. A self-hosted ntfy server, with an access token if it needs one, works the same way.

Settings ("notify" in z1screen.json, or the settings page, which wins):
  {"server": "https://ntfy.sh", "topic": "", "token": "", "stops": true, "soon_min": 0,
   "alarms": true, "pauses": true}
  topic     empty: no notifications
  stops     notify when the Z1 stops for a bit change (or for the probe)
  soon_min  also notify this many minutes before a stop (0: no early warning)
  alarms    notify when a job stops with an alarm (only during a job: the Z1 can report one at power on)
  pauses    notify when a job is paused
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

from . import __version__

log = logging.getLogger("z1screen.notify")

DEFAULTS = {"server": "https://ntfy.sh", "topic": "", "token": "", "stops": True, "soon_min": 0,
            "alarms": True, "pauses": True}
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SERVER_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?$")
# After a stop or a pause, the job must run on (or the Z1 be idle) this long before the same thing
# counts as a new one (the status can drop out for a few seconds during a stop, and a pause is often
# resumed and paused again). An alarm only needs to stay cleared long enough to rule out a flicker:
# a job restarted after one can hit the next straight away.
RAN_AGAIN_S = 20
ALARM_AGAIN_S = 3
# Modes that end an alarm or a pause; "connecting" and the like leave it as it was.
CLEAR_MODES = ("running", "idle", "toolchange")


def settings(cfg: dict) -> dict:
    """The notify settings with defaults filled in."""
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("notify") or {}).items() if k in DEFAULTS})
    return out


def clean(v: dict, old: dict) -> dict:
    """Checks what the settings page sends. A token left out (None) keeps the one saved. Raises ValueError."""
    if not isinstance(v, dict):
        raise ValueError("The notification settings did not arrive in the expected form.")
    server = str(v.get("server") or DEFAULTS["server"]).strip().rstrip("/")
    if not _SERVER_RE.match(server):
        raise ValueError("The ntfy server must be a web address like https://ntfy.sh.")
    topic = str(v.get("topic") or "").strip()
    if topic and not _TOPIC_RE.match(topic):
        raise ValueError("The topic can only have letters, digits, - and _ (at most 64).")
    token = v.get("token")
    token = str(old.get("token") or "") if token is None else str(token).strip()
    if len(token) > 200 or any(c.isspace() for c in token):
        raise ValueError("That access token doesn't look right.")
    try:
        soon = int(v.get("soon_min") or 0)
    except (TypeError, ValueError):
        raise ValueError("The early warning is a number of minutes.")
    if not 0 <= soon <= 60:
        raise ValueError("The early warning must be between 0 and 60 minutes.")
    return {"server": server, "topic": topic, "token": token, "stops": v.get("stops") is not False, "soon_min": soon,
            "alarms": v.get("alarms") is not False, "pauses": v.get("pauses") is not False}


def _bit(t: dict) -> str:
    """'Blue sticker · 30° V-bit, 0.2 mm tip' for a tool_info entry."""
    parts = []
    if t.get("sticker"):
        parts.append("%s sticker" % t["sticker"])
    elif t.get("unset"):
        parts.append("no color set")
    if t.get("label") and t.get("code") != "Probe":
        parts.append(t["label"])
    return " · ".join(parts)


def stop_message(view: dict) -> dict:
    tc = view.get("toolchange") or {}
    job = view.get("job") or {}
    code = tc.get("code") or "the next bit"
    title = "Z1 stopped: load the probe" if code == "Probe" else "Z1 stopped: load %s" % code
    lines = [s for s in (_bit(tc),) if s]
    if (tc.get("bit") or {}).get("text"):
        lines.append(tc["bit"]["text"] + (" (past its expected life)" if tc["bit"].get("state") == "over" and
                                          "past" not in tc["bit"]["text"] else ""))
    out = tc.get("out")
    if out and out.get("code") not in (None, "-"):
        where = ("the %s container" % out["sticker"]) if out.get("sticker") else "its container"
        lines.append("%s goes back in %s." % (out["code"], where))
    tail = []
    if tc.get("index") and tc.get("total"):
        tail.append("Change %d of %d" % (tc["index"], tc["total"]))
    if job.get("name"):
        tail.append(job["name"])
    if tail:
        lines.append(" · ".join(tail))
    lines.append("Press the Z1's front button once it's in.")
    return {"title": title, "message": "\n".join(lines), "tags": ["wrench"], "priority": 4}


def bits_message(warnings: list, job_name: str) -> dict:
    """At the start of a job: bits it would take past their expected life (bitlife.py)."""
    lines = list(warnings) + (["Job: %s" % job_name] if job_name else [])
    return {"title": "Z1: bits near the end of their life", "message": "\n".join(lines), "tags": ["warning"],
            "priority": 3}


def soon_message(view: dict, minutes: int) -> dict:
    job = view.get("job") or {}
    nx = job.get("next") or {}
    mins = max(1, int(round(float(nx.get("in") or 0) / 60.0)))
    code = nx.get("code") or "the next bit"
    what = "the probe" if code == "Probe" else code
    lines = [s for s in (_bit(nx), job.get("name") or "") if s]
    return {"title": "Z1: %s in about %d min" % (what, mins), "message": "\n".join(lines) or "Bit change coming up.",
            "tags": ["hourglass_flowing_sand"], "priority": 3}


def alarm_message(view: dict, job_name: str) -> dict:
    lines = [view.get("alarm") or "Machine stopped"]
    if job_name:
        lines.append("Job: %s" % job_name)
    return {"title": "Z1 alarm: job stopped", "message": "\n".join(lines), "tags": ["rotating_light"], "priority": 5}


def pause_message(view: dict, job_name: str) -> dict:
    job = view.get("job") or {}
    name = job.get("name") or job_name
    return {"title": "Z1 paused", "message": ("%s is paused." % name) if name else "The job is paused.",
            "tags": ["pause_button"], "priority": 4}


def post(conf: dict, msg: dict, timeout: float = 10.0) -> None:
    """Publishes one notification (ntfy's JSON form, so any text works in the title). Raises OSError."""
    body = json.dumps(dict(msg, topic=conf["topic"]), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(conf["server"].rstrip("/") + "/", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "BitCue/" + __version__})
    if conf.get("token"):
        req.add_header("Authorization", "Bearer " + conf["token"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(2048)
    except urllib.error.HTTPError as exc:
        raise OSError("the ntfy server answered %d %s" % (exc.code, exc.reason))
    except urllib.error.URLError as exc:
        raise OSError("can't reach %s (%s)" % (conf["server"], exc.reason))


class Notifier:
    """Watches the screen data and sends a notification once per stop (and once per early warning)."""

    def __init__(self, send=None) -> None:
        self._send = send or post
        self.stop_key = None
        self.ran_since = None
        self.soon_sent = set()
        self.job_file = None
        self.job_name = ""
        self.in_job = False
        self.episodes = {}      # "alarm" / "paused": {"on": bool, "off": time it last cleared}
        self.last = None        # {"t", "title", "ok", "error"} of the last notification tried

    def watch(self, view: dict, cfg: dict, click: str = "", now: float = None) -> list:
        """Called with each new screen data. Returns the messages it sent (for the tests)."""
        conf = settings(cfg)
        now = time.time() if now is None else now
        mode = view.get("mode")
        job = view.get("job") or {}
        sent = []
        if mode in ("running", "paused", "toolchange"):
            self.in_job = True
            self.job_name = job.get("name") or self.job_name
            if job.get("file") != self.job_file:
                self.job_file, self.soon_sent = job.get("file"), set()
        if self._episode("alarm", mode == "alarm", mode in CLEAR_MODES, now, ALARM_AGAIN_S) and self.in_job:
            if conf["topic"] and conf["alarms"]:
                sent.append(alarm_message(view, self.job_name))
        if self._episode("paused", mode == "paused", mode in CLEAR_MODES, now, RAN_AGAIN_S):
            if conf["topic"] and conf["pauses"]:
                sent.append(pause_message(view, self.job_name))
        if mode == "idle":
            self.job_file, self.soon_sent, self.stop_key, self.ran_since = None, set(), None, None
            self.in_job, self.job_name = False, ""
        if mode == "toolchange":
            tc = view.get("toolchange") or {}
            key = (job.get("file"), tc.get("code"), tc.get("index"))
            new = key != self.stop_key or (self.ran_since is not None and now - self.ran_since >= RAN_AGAIN_S)
            self.stop_key, self.ran_since = key, None
            if new and conf["topic"] and conf["stops"]:
                sent.append(stop_message(view))
        elif mode == "running":
            if self.ran_since is None:
                self.ran_since = now
            nx = job.get("next")
            soon = conf["soon_min"]
            if soon and nx and nx.get("in") is not None and 0 < nx["in"] <= soon * 60:
                key = nx.get("index") or nx.get("code")
                if key not in self.soon_sent:
                    self.soon_sent.add(key)
                    if conf["topic"]:
                        sent.append(soon_message(view, soon))
        for msg in sent:
            if click:
                msg["click"] = click
            self.send_later(conf, msg)
        return sent

    def _episode(self, kind: str, on: bool, clear: bool, now: float, gap: float) -> bool:
        """True when an alarm or a pause starts: not one that was already on, or that cleared less than gap s ago."""
        ep = self.episodes.setdefault(kind, {"on": False, "off": None})
        if on:
            new = not ep["on"] and (ep["off"] is None or now - ep["off"] >= gap)
            ep["on"] = True
            return new
        if clear and ep["on"]:
            ep["on"], ep["off"] = False, now
        return False

    def send_later(self, conf: dict, msg: dict) -> None:
        threading.Thread(target=self.send_now, args=(conf, msg), name="notify", daemon=True).start()

    def send_now(self, conf: dict, msg: dict) -> dict:
        try:
            self._send(conf, msg)
            self.last = {"t": time.time(), "title": msg["title"], "ok": True, "error": ""}
            log.info("notification sent: %s", msg["title"])
        except Exception as exc:
            self.last = {"t": time.time(), "title": msg["title"], "ok": False, "error": str(exc)}
            log.warning("notification not sent (%s): %s", exc, msg["title"])
        return self.last

    def info(self, cfg: dict) -> dict:
        """For the settings page; the token itself is never sent back."""
        conf = settings(cfg)
        return {"server": conf["server"], "topic": conf["topic"], "token_set": bool(conf["token"]),
                "stops": bool(conf["stops"]), "soon_min": int(conf["soon_min"] or 0),
                "alarms": bool(conf["alarms"]), "pauses": bool(conf["pauses"]), "last": self.last}

    @staticmethod
    def test_message(machine: str = "") -> dict:
        return {"title": "BitCue test", "tags": ["white_check_mark"], "priority": 3,
                "message": "Notifications from %s work." % (machine or "BitCue")}
