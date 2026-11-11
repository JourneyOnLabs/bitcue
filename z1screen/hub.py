"""The relay between Makera Studio and the Z1, and the job tracking behind the screen.

How it works:
  * The Z1 announces itself on the Wi-Fi every second (UDP port 3333,
    "name,ip,port,busy"). The Pi listens for that to find the machine.
  * The Z1 takes one app connection at a time on TCP port 2222. The Pi keeps
    that connection and asks for the status once a second, so the screen works
    with the laptop closed. (With "connection": "polite" in the settings it
    connects only for a moment every few seconds instead, leaving the machine
    free for an app that connects to it directly.)
  * The Pi announces itself as "<machine name>_screen". When Makera Studio
    connects to that, the Pi drops its own connection, opens a fresh one for
    Makera Studio and passes every byte through unchanged in both directions.
    It reads the status replies as they go by. When Makera Studio disconnects,
    the Pi goes back to asking for the status itself.
  * On its own it sends only the status request '?', a "progress" request if
    it joined a job part way through (to learn the file name), and, on its own
    connection while the Z1 is idle, folder listings ("ls") and file reads
    ("download", the way Makera's controllers read a file). It never stops the
    machine or confirms a tool change.
  * It starts a job only when the Pi's small touch screen asks for it (see
    jobs_api.py and start.py): the same three commands Makera Studio sends at
    Run, only while the Z1 is idle with no alarm, and only where the job is
    clear of what is already cut on the plate.
  * The demo and the color check (settings page) only change what the screen
    shows, only while the Z1 is idle or not found, and end as soon as the Z1
    starts a job. They send nothing to the machine.
  * The Pi's own small screen is drawn by a separate service (tft.py) from the
    same screen data the web page uses (GET /state).
"""

from __future__ import annotations

import asyncio
import collections
import copy
import logging
import os
import re
import socket
import threading
import time

from . import __version__
from . import bits
from . import demo as demo_mod
from . import prefs as prefs_mod
from . import framing as fr
from . import bitlife as bitlife_mod
from . import notify as notify_mod
from .capture import TransferCapture, normalize_path
from .gcode import Profile
from .jobs_api import JobsMixin
from .status import STATE_WORDS, parse_status

log = logging.getLogger("z1screen.hub")

SPECIAL_TOOLS = {0: "Probe", 8888: "Laser", 9999: "3D probe"}
UNSET_COLOR = "#5A5650"   # a bit with no sticker color: striped gray on the screen
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEMO_OK = ("idle", "searching", "connecting")   # screen modes in which a demo may play


class DemoBlocked(Exception):
    """The Z1 is busy, so the demo or color check can't take over the screen."""


def _luminance(hex_color: str) -> float:
    try:
        h = hex_color.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except (ValueError, AttributeError, IndexError):
        return 0.0
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def text_color_for(bg: str) -> str:
    return "#111111" if _luminance(bg) > 0.3 else "#FFFFFF"


class Hub(JobsMixin):
    def __init__(self, cfg: dict, store) -> None:
        self.cfg = cfg
        self.store = store
        # the settings page's setup choices (Anchor 1, starting from the screen, run settings, PIN)
        self.state_dir = getattr(store, "state_dir", "") or ""
        self.prefs = prefs_mod.load(self.state_dir)
        prefs_mod.apply(self.cfg, self.prefs)
        self.started = time.time()
        self.loop = None
        self.polite = str(cfg.get("connection", "hold")).lower() == "polite"
        # machine, from config or from its announcement
        self.m_name = cfg.get("machine_name") or ""
        self.m_ip = cfg.get("machine_ip") or ""
        self.m_port = int(cfg.get("machine_port", 2222))
        self.m_busy = None
        self.m_extra = []
        self.m_seen = 0.0
        self.my_ip = ""
        # connections
        self.clients = []
        self.own = None
        self.own_connecting = False
        self.own_fail = 0
        self.own_next_try = 0.0
        self.last_client_end = 0.0
        self.client_count = 0
        # data from the machine
        self.status = None
        self.status_t = 0.0
        self.fw = ""
        self.console = collections.deque(maxlen=80)
        self.commands = collections.deque(maxlen=80)
        self.captures_done = 0
        self.captures_dropped = 0
        # job tracking
        self.job = None
        self.last_job = None
        self.play_path = ""
        self.play_path_t = 0.0
        self.progress_path = ""
        self.progress_path_t = 0.0
        self.file_size = None
        self.file_size_t = 0.0
        self.done_t = 0.0
        self.progress_asked = 0.0
        # screen
        self.view = {}
        self.view_seq = 0
        self.view_event = None
        self.store.listeners.append(self._profile_ready_from_thread)
        # bit colors: the starting table, replaced by the one saved from the settings page
        self.kit_tools = copy.deepcopy(cfg.get("tools", {}) or {})
        saved = bits.load(self.state_dir)
        self.tools_saved = saved is not None
        if saved is not None:
            self.cfg["tools"] = saved
        # demo and color check (screen only)
        self.demo = None
        self.demo_note = ""
        self.hostname = socket.gethostname()
        # the Pi's small screen: turned upside down or not (settings page, else the settings file)
        self.screen_prefs = prefs_mod.load_screen(self.state_dir)
        self._preview_lock = threading.Lock()
        self._preview_fonts = None
        # phone notifications at a bit change
        self.notifier = notify_mod.Notifier()
        # how long each bit has cut (bitlife.py)
        self.bitlife = bitlife_mod.BitLife(self.state_dir)
        self.bit_tracker = bitlife_mod.Tracker(self.bitlife)
        self.bit_plan = {"file": None, "warnings": []}     # bits the job on now would take past their life
        # job list, plate spots, starting from the small screen, touch calibration
        self._init_jobs()

    # ------------------------------------------------------------ helpers
    def tool_info(self, n, prof=None) -> dict:
        tools = self.cfg.get("tools", {}) or {}
        ent = tools.get(str(n), {}) if n is not None else {}
        if not isinstance(ent, dict):
            ent = {}
        label = ""
        if n in SPECIAL_TOOLS:
            label = ent.get("name") or SPECIAL_TOOLS[n]
        else:
            if prof is not None and self.cfg.get("names_from_file", True):
                label = prof.tools.get(n, "")
            if not label:
                label = ent.get("name", "") or ""
        color = str(ent.get("color") or "")
        unset = not _HEX.match(color)
        if unset:
            color = UNSET_COLOR
        code = "Probe" if n == 0 else ("T%d" % n if n is not None and n >= 0 else "-")
        if n in (8888, 9999):
            code = SPECIAL_TOOLS[n]
        return {
            "tool": n,
            "code": code,
            "label": label,
            "color": color,
            "text": text_color_for(color),
            "sticker": "" if unset else (ent.get("sticker", "") or ""),
            "unset": unset,
        }

    def settings_url(self) -> str:
        port = int(self.cfg.get("web_port", 8080))
        host = self.hostname or ""
        if host and host != "localhost" and "." not in host:
            base = host + ".local"
        else:
            base = self.my_ip or host or "z1screen.local"
        return "http://%s%s/settings" % (base, "" if port == 80 else ":%d" % port)

    def dirty(self) -> None:
        if self.view_event is not None:
            self.view_event.set()

    def _profile_ready_from_thread(self, path: str) -> None:
        """Called on the profile worker thread; hand over to the event loop."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._profile_ready)

    def _profile_ready(self) -> None:
        self._resolve_profile()
        self.dirty()

    def _on_capture(self, kind: str, path: str, data: bytes) -> None:
        self.captures_done += 1
        self.store.add_file_async(kind, path, data)

    # ------------------------------------------------------------ discovery
    class _Disc(asyncio.DatagramProtocol):
        def __init__(self, hub):
            self.hub = hub

        def datagram_received(self, data, addr):
            try:
                self.hub.on_announcement(data, addr)
            except Exception:
                log.debug("ignored an announcement", exc_info=True)

    def on_announcement(self, data: bytes, addr) -> None:
        text = data.decode("utf-8", "ignore").strip()
        f = text.split(",")
        if len(f) < 4:
            return
        name, ip, port, busy = f[0].strip(), f[1].strip(), f[2].strip(), f[3].strip()
        suffix = self.cfg.get("relay_name_suffix", "_screen")
        if suffix and name.endswith(suffix):
            return  # that is us
        if not suffix and self.my_ip and ip == self.my_ip and port == str(self.cfg.get("relay_port", 2222)):
            return  # also us
        want_ip = self.cfg.get("machine_ip")
        want_name = self.cfg.get("machine_name")
        if want_ip and ip != want_ip:
            return
        if want_name and name != want_name:
            return
        if self.m_ip and ip != self.m_ip and time.time() - self.m_seen < 30:
            return  # stick with the machine we found first
        first = not self.m_ip or self.m_name != name
        self.m_name = name
        self.m_ip = ip
        try:
            self.m_port = int(port)
        except ValueError:
            pass
        self.m_busy = busy == "1"
        self.m_extra = f[4:]
        self.m_seen = time.time()
        if first:
            log.info("found the machine: %s at %s:%s", name, ip, port)
            self._update_my_ip()
            self.dirty()

    def _update_my_ip(self) -> None:
        target = self.m_ip or "8.8.8.8"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((target, 9))
            self.my_ip = s.getsockname()[0]
            s.close()
        except OSError:
            pass

    def _held_by_other(self) -> bool:
        """The machine says it is busy and neither the Pi nor an app through the Pi holds it."""
        now = time.time()
        return bool(self.m_busy and now - self.m_seen < 5 and self.own is None and not self.own_connecting
                    and not self.clients and now - self.last_client_end > 4)

    async def announcer(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        n = 0
        while True:
            await asyncio.sleep(float(self.cfg.get("announce_interval", 1.0)))
            try:
                n += 1
                if n % 30 == 0 or not self.my_ip:
                    self._update_my_ip()
                if not (self.m_ip and self.my_ip):
                    continue
                if self.m_seen and time.time() - self.m_seen > 30 and not self.cfg.get("machine_ip"):
                    continue  # the machine has gone quiet; don't advertise a relay to nothing
                base = self.m_name or self.cfg.get("machine_name") or "Makera_Z1"
                name = base + self.cfg.get("relay_name_suffix", "_screen")
                busy = "1" if (self.clients or self._held_by_other()) else "0"
                fields = [name, self.my_ip, str(self.cfg.get("relay_port", 2222)), busy] + list(self.m_extra)
                msg = ",".join(fields).encode()
                port = int(self.cfg.get("announce_port") or self.cfg.get("discovery_port", 3333))
                targets = list(self.cfg.get("announce_to") or [])
                if not targets:
                    parts = self.my_ip.split(".")
                    targets = ["255.255.255.255"]
                    if len(parts) == 4:
                        targets.append(".".join(parts[:3] + ["255"]))
                for t in targets:
                    try:
                        sock.sendto(msg, (t, port))
                    except OSError:
                        pass
            except Exception:
                log.exception("announcement failed")

    # ------------------------------------------------------------ taps
    def _new_tap(self) -> dict:
        now = time.time()
        return {
            "mp": fr.FrameParser(check_crc=False),
            "cp": fr.FrameParser(check_crc=False),
            "info": fr.LineBuffer(),
            "mtext": fr.LineBuffer(),
            "ctext": fr.LineBuffer(),
            "last_status": now,
            "t0": now,
            "capture": TransferCapture(self._on_capture),
            "xfer_open": False,
            "xfer_t": 0.0,
        }

    def _note_transfer(self, sess: dict, ptype: int) -> None:
        sess["xfer_t"] = time.time()
        if ptype == fr.FILE_START:
            sess["xfer_open"] = True
        elif ptype in (fr.FILE_END, fr.FILE_CAN):
            sess["xfer_open"] = False

    def in_transfer(self, sess: dict) -> bool:
        idle = time.time() - sess.get("xfer_t", 0.0)
        if idle < 5:
            return True
        return bool(sess.get("xfer_open")) and idle < 120

    def tap_machine(self, sess: dict, data: bytes) -> None:
        frames = sess["mp"].feed(data)
        for ptype, payload in frames:
            if ptype in (fr.LOAD_INFO, fr.LOAD_FINISH, fr.LOAD_ERROR):
                self.tap_load_frame(sess, ptype, payload)
            elif ptype == fr.STATUS_RES:
                sess["last_status"] = time.time()
                self.on_status(payload.decode("utf-8", "replace"))
            elif ptype == fr.NORMAL_INFO:
                for line in sess["info"].feed(payload):
                    self.on_console_line(line)
            elif ptype in fr.FILE_TYPES:
                self._note_transfer(sess, ptype)
                if sess.get("fetch") is not None:
                    sess["last_status"] = time.time()      # the Z1 is answering a file read
                    sess["fetch"].on_frame(ptype, payload)
                else:
                    sess["capture"].machine_frame(ptype, payload)
            elif ptype == fr.FIRM_VER:
                self.fw = payload.decode("utf-8", "replace").strip()
        if sess["mp"].frames == 0:
            # machine speaking plain text (older protocol)
            for line in sess["mtext"].feed(data):
                if line.startswith("<"):
                    sess["last_status"] = time.time()
                    self.on_status(line)
                elif line:
                    self.on_console_line(line)

    def tap_client(self, sess: dict, data: bytes) -> None:
        frames = sess["cp"].feed(data)
        for ptype, payload in frames:
            if ptype == fr.CTRL_MULTI:
                text = payload.decode("utf-8", "replace")
                self.note_app_command(sess, text)
                self.on_app_command(text)
            elif ptype in fr.FILE_TYPES:
                self._note_transfer(sess, ptype)
                sess["capture"].client_frame(ptype, payload)
        if sess["cp"].frames == 0:
            for line in sess["ctext"].feed(data):
                self.on_app_command(line)

    # ------------------------------------------------------------ machine data
    _re_g10 = re.compile(r"^G10\s*L2\s*P0\s*X\s*(-?\d+(?:\.\d+)?)\s*Y\s*(-?\d+(?:\.\d+)?)", re.I)

    def on_app_command(self, text: str) -> None:
        cmd = text.strip()
        if not cmd:
            return
        self.commands.append((round(time.time(), 1), cmd[:160].replace("\x01", " ")))
        m = self._re_g10.match(cmd)
        if m:
            # the work origin Makera Studio's run window set (Anchor 1 plus its offsets), shown on the settings
            # page next to the Pi's Anchor 1 so the two can be compared
            self.app_origin = {"x": float(m.group(1)), "y": float(m.group(2)), "t": time.time()}
        low = cmd.lower()
        if low.startswith("buffer "):
            cmd = cmd[7:].strip()
            low = cmd.lower()
        if low.startswith("play "):
            parts = cmd.split()
            if len(parts) >= 2:
                self.play_path = normalize_path(parts[1])
                self.play_path_t = time.time()
                log.info("job started from the app: %s", self.play_path)
                if self.job and not self.job.get("path") and self.play_path_t >= self.job["started"] - 30:
                    self.job["path"] = self.play_path
                    self._resolve_profile()

    _re_size = re.compile(r"^\s*File size (\d+)")
    _re_prog = re.compile(r"^file: (.+?), (\d+) % complete")

    def on_console_line(self, line: str) -> None:
        line = line.rstrip("\r")
        if not line:
            return
        self.console.append((round(time.time(), 1), line[:200]))
        self.watch_line(line)
        m = self._re_size.match(line)
        if m:
            self.file_size = int(m.group(1))
            self.file_size_t = time.time()
            if self.job and not self.job.get("size"):
                self.job["size"] = self.file_size
            return
        m = self._re_prog.match(line)
        if m:
            self.progress_path = normalize_path(m.group(1))
            self.progress_path_t = time.time()
            if self.job and not self.job.get("path"):
                self.job["path"] = self.progress_path
                self._resolve_profile()
            return
        if "Done printing file" in line:
            self.done_t = time.time()

    def on_status(self, text: str) -> None:
        st = parse_status(text)
        if not st:
            return
        now = time.time()
        self.status = st
        self.status_t = now
        if st["playing"]:
            job = self.job
            if job is not None and st["elapsed"] + 5 < job["elapsed"] and st["line"] < job["line"]:
                self._end_job(st, restarted=True)
                job = None
            if job is None:
                self._start_job(st)
            job = self.job
            job["elapsed"] = st["elapsed"]
            job["line"] = st["line"]
            job["percent"] = st["percent"]
            job["seen"] = now
            self._track_stops(job, st)
            if job.get("profile") is None and job.get("path"):
                self._resolve_profile()
            self._job_cutting(job, st)
        else:
            if self.job is not None and now - self.job["seen"] > 3:
                self._end_job(st)
        self.dirty()

    @staticmethod
    def _track_stops(job: dict, st: dict) -> None:
        """Remember each tool stop the machine finishes, in order.

        While the tool-change routine and the first rapid moves after it run,
        the line number the machine reports stays on the last cutting line
        before the change. The finished stops tell the screen which change has
        really been passed.
        """
        tgt = st.get("target_tool")
        if st["state"] == "Tool" and tgt is not None and tgt >= 0:
            job["waiting_for"] = tgt
        elif job.get("waiting_for") is not None:
            job.setdefault("stops", []).append(job["waiting_for"])
            job["waiting_for"] = None

    @staticmethod
    def _passed(prof, stops) -> int:
        """Number of the file's tool changes already done, from the finished stops."""
        i = 0
        changes = prof.changes
        for t in stops or []:
            for k in range(i, len(changes)):
                if changes[k]["tool"] == t:
                    i = k + 1
                    break
        return i

    def _start_job(self, st: dict) -> None:
        now = time.time()
        path = ""
        if self.play_path and now - self.play_path_t < 6 * 3600:
            path = self.play_path
        elif self.progress_path and now - self.progress_path_t < 120:
            path = self.progress_path
        size = self.file_size if (self.file_size and now - self.file_size_t < 6 * 3600) else None
        self.job = {
            "path": path,
            "size": size,
            "started": now,
            "elapsed": st["elapsed"],
            "line": st["line"],
            "percent": st["percent"],
            "seen": now,
            "profile": None,
            "ratio": None,
            "stops": [],
            "waiting_for": None,
        }
        self.done_t = 0.0
        log.info("job running: %s", path or "(file name not known yet)")
        self._resolve_profile()

    def _end_job(self, st: dict, restarted: bool = False) -> None:
        job = self.job
        self.job = None
        if not job:
            return
        done = job.get("percent", 0) >= 99 or (self.done_t and time.time() - self.done_t < 120)
        self._job_spot_end(job, bool(done) and not restarted)
        prof = job.get("profile")
        self.last_job = {
            "name": self._pretty(job.get("path", "")),
            "done": bool(done) and not restarted,
            "elapsed": job.get("elapsed", 0),
            "ended": time.time(),
            "reason": "" if done else (st.get("halt_text") or "Stopped before the end"),
            "tools": [self.tool_info(t, prof) for t in (prof.d.get("used_tools", []) if prof else [])],
            "preview": self.preview_of(prof.d) if prof else "",
        }
        # keep a play command that already belongs to the next job
        if self.play_path_t <= job["started"]:
            self.play_path = ""
        log.info("job ended (%s): %s", "done" if done else "stopped", job.get("path"))

    def _resolve_profile(self) -> None:
        job = self.job
        if not job or job.get("profile") is not None or not job.get("path"):
            return
        d = self.store.find(job["path"], job.get("size"))
        if d is None and job.get("size") is not None:
            d = self.store.find(job["path"], None)
            if d is not None:
                log.warning("profile size differs from the machine's file size; using it anyway: %s", job["path"])
        if d is not None:
            job["profile"] = Profile(d)
            self._job_spot(job)
            log.info("timing known for %s (%d tool changes)", job["path"], len(d.get("changes", [])))
            self.dirty()

    @staticmethod
    def _pretty(path: str) -> str:
        base = os.path.basename(path or "")
        for ext in (".nc", ".gcode", ".ngc", ".tap", ".cnc", ".txt"):
            if base.lower().endswith(ext):
                base = base[: -len(ext)]
                break
        return base

    # ------------------------------------------------------------ own connection
    async def own_loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                if self.clients or self.own is not None or not self.m_ip:
                    continue
                now = time.time()
                if now < self.own_next_try or now - self.last_client_end < 1.0:
                    continue
                if self._held_by_other():
                    # Another app is connected straight to the machine. Never compete for it.
                    self.own_next_try = now + 2
                    self.dirty()
                    continue
                try:
                    await self._own_session()
                    self.own_fail = 0
                except Exception as exc:
                    self.own_fail += 1
                    log.info("could not talk to the machine (%s)", exc)
                self.own = None
                gap = float(self.cfg.get("polite_gap", 4.0)) if self.polite else 0.0
                self.own_next_try = time.time() + (gap if self.own_fail == 0 else min(10, 1 + self.own_fail * 2))
                self.dirty()
            except Exception:
                log.exception("own connection loop error")

    async def _own_session(self) -> None:
        self.own_connecting = True
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.m_ip, self.m_port), 4)
        finally:
            self.own_connecting = False
        if self.clients:
            writer.close()  # an app arrived while we were connecting; it goes first
            return
        sess = self._new_tap()
        sess["writer"] = writer
        sess["text"] = False
        self.own = sess
        if not self.polite:
            log.info("connected to the machine at %s:%d", self.m_ip, self.m_port)
        self.dirty()
        poller = asyncio.create_task(self._own_poll(sess))
        try:
            while not self.clients:
                try:
                    data = await asyncio.wait_for(reader.read(65536), 1.0)
                except asyncio.TimeoutError:
                    if time.time() - sess["last_status"] > 20:
                        raise ConnectionError("no answer from the machine")
                    if self.polite and time.time() - sess["t0"] > 3 and sess.get("fetch") is None \
                            and not sess.get("hold"):
                        break
                    continue
                if not data:
                    if self.clients:
                        break  # we closed it to hand over to the app
                    raise ConnectionError("machine closed the connection")
                try:
                    self.tap_machine(sess, data)
                except Exception:
                    log.exception("could not read the machine's reply")
                if sess.get("fetch") is not None or sess.get("hold"):
                    continue   # a file read or folder listing is under way: keep the connection
                if self.polite and sess.get("polled") and sess["last_status"] > sess["t0"]:
                    asked = sess.get("progress_sent", 0.0)
                    if not asked or self.progress_path_t > asked or time.time() - asked > 1.5:
                        break  # polite mode: one status reply (and the file name, if asked) is enough
        finally:
            poller.cancel()
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 2)
            except Exception:
                pass
            self.own = None
            if not self.polite:
                log.info("own connection closed")

    async def _own_poll(self, sess: dict) -> None:
        interval = float(self.cfg.get("poll_interval", 1.0))
        w = sess["writer"]
        while True:
            if sess.get("fetch") is not None:
                await asyncio.sleep(0.2)     # no status requests in the middle of a file read
                continue
            if sess["text"]:
                w.write(b"?")
            else:
                w.write(fr.build_frame(fr.CTRL_SINGLE, b"?"))
            sess["polled"] = True
            # learn the file name if we joined a job part way through
            job = self.job
            if job and not job.get("path") and time.time() - self.progress_asked > 15:
                self.progress_asked = time.time()
                sess["progress_sent"] = self.progress_asked
                if sess["text"]:
                    w.write(b"progress\n")
                else:
                    w.write(fr.build_frame(fr.CTRL_MULTI, b"progress"))
            await w.drain()
            if (not sess["text"] and time.time() - sess["t0"] > 6 and sess["mp"].frames == 0
                    and time.time() - sess["last_status"] > 5):
                log.info("no framed replies; trying the plain text protocol")
                sess["text"] = True
            await asyncio.sleep(interval)

    async def stop_own(self) -> None:
        for _ in range(80):
            if not self.own_connecting:
                break
            await asyncio.sleep(0.05)
        sess = self.own
        if not sess:
            return
        try:
            sess["writer"].close()
        except Exception:
            pass
        for _ in range(40):
            if self.own is None:
                break
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------ relay
    async def on_relay_client(self, creader, cwriter) -> None:
        """An app (Makera Studio) connected to the Pi: give it its own connection to the machine."""
        peer = cwriter.get_extra_info("peername")
        if not self.m_ip:
            log.info("app connected from %s before the machine was found; closing", peer)
            cwriter.close()
            return
        sess = self._new_tap()
        sess["peer"] = peer
        first = not self.clients
        self.clients.append(sess)
        self.client_count += 1
        log.info("app connected from %s; opening a connection to the machine for it", peer)
        self.dirty()
        uwriter = None
        try:
            if first:
                await self.stop_own()
            ureader = None
            deadline = time.time() + 8
            last_exc = None
            while time.time() < deadline:
                try:
                    ureader, uwriter = await asyncio.wait_for(asyncio.open_connection(self.m_ip, self.m_port), 2)
                    break
                except Exception as exc:
                    last_exc = exc
                    await asyncio.sleep(0.3)
            if uwriter is None:
                log.warning("could not reach the machine for the app: %s", last_exc)
                return
            sess["uwriter"] = uwriter
            c2m = asyncio.create_task(self._pump(creader, uwriter, self.tap_client, sess))
            m2c = asyncio.create_task(self._pump(ureader, cwriter, self.tap_machine, sess))
            keep = asyncio.create_task(self._client_keepalive(sess))
            done, pending = await asyncio.wait({c2m, m2c}, return_when=asyncio.FIRST_COMPLETED)
            if c2m in done and m2c in pending:
                # The app finished sending; pass that on and let the machine's last replies through.
                try:
                    if uwriter.can_write_eof():
                        uwriter.write_eof()
                    await asyncio.wait_for(asyncio.shield(m2c), 3)
                except Exception:
                    pass
            for t in (c2m, m2c, keep):
                if not t.done():
                    t.cancel()
        finally:
            for w in (cwriter, uwriter):
                if w is not None:
                    try:
                        w.close()
                    except Exception:
                        pass
            if sess in self.clients:
                self.clients.remove(sess)
            if sess["capture"].cur is not None:
                sess["capture"].cur = None
            self.last_client_end = time.time()
            log.info("app disconnected (%s)", peer)
            self.dirty()

    async def _pump(self, reader, writer, tap, sess) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                writer.write(data)
                try:
                    tap(sess, data)
                except Exception:
                    log.exception("could not read relayed data (traffic still passed through)")
                await writer.drain()
        except (ConnectionError, OSError):
            return      # one side went away

    async def _client_keepalive(self, sess: dict) -> None:
        """If the app stops asking for the status, ask for it between its frames.

        Also, if a job is running and the screen doesn't know its file (the Pi's
        software restarted part way through a job while Makera Studio was connected
        through the Pi), ask the machine for the job's progress, which names the
        file. That is asked at most 3 times per connection, 15 s apart.
        """
        while True:
            await asyncio.sleep(0.5)
            if self.in_transfer(sess):
                continue
            cp = sess["cp"]
            if cp.frames == 0 or not cp.idle():
                continue
            w = sess.get("uwriter")
            if w is None:
                continue
            job, st = self.job, self.status
            if (job is not None and not job.get("path") and st and st.get("playing")
                    and sess.get("progress_asks", 0) < 3 and time.time() - self.progress_asked > 15):
                self.progress_asked = time.time()
                sess["progress_asks"] = sess.get("progress_asks", 0) + 1
                w.write(fr.build_frame(fr.CTRL_MULTI, b"progress"))
                log.info("asked the machine which file is running")
            if time.time() - sess["last_status"] < 2.0:
                continue
            w.write(fr.build_frame(fr.CTRL_SINGLE, b"?"))
            sess["last_status"] = time.time()  # ask again in about 2 s if still quiet

    # ------------------------------------------------------------ plain forwards (camera, web page)
    async def forward_server(self, spec) -> None:
        """Pass a port straight through to the machine ("80", or "local:remote")."""
        text = str(spec)
        if ":" in text:
            port, remote = (int(x) for x in text.split(":", 1))
        else:
            port = remote = int(text)

        async def handle(r, w):
            if not self.m_ip:
                w.close()
                return
            try:
                ur, uw = await asyncio.wait_for(asyncio.open_connection(self.m_ip, remote), 4)
            except Exception:
                w.close()
                return

            async def pipe(a, b):
                try:
                    while True:
                        d = await a.read(65536)
                        if not d:
                            break
                        b.write(d)
                        await b.drain()
                    if b.can_write_eof():
                        b.write_eof()
                except Exception:
                    pass

            try:
                await asyncio.gather(pipe(r, uw), pipe(ur, w))
            finally:
                for x in (w, uw):
                    try:
                        x.close()
                    except Exception:
                        pass

        try:
            server = await asyncio.start_server(handle, "0.0.0.0", port)
            log.info("passing port %d through to the machine", port)
            async with server:
                await server.serve_forever()
        except OSError as exc:
            log.warning("cannot pass port %d through (%s)", port, exc)

    # ------------------------------------------------------------ the screen's view
    def compute_view(self) -> dict:
        now = time.time()
        relay_name = (self.m_name + self.cfg.get("relay_name_suffix", "_screen")) if self.m_name else ""
        v = {
            "t": round(now, 1),
            "machine": {"name": self.m_name, "ip": self.m_ip, "fw": self.fw},
            "relay": {"name": relay_name, "app_connected": bool(self.clients), "ip": self.my_ip},
            "mode": "searching",
            "state": "",
            "state_text": "",
            "job": None,
            "toolchange": None,
            "alarm": None,
            "last_job": None,
            "legend": [],
            "settings_url": self.settings_url(),
            "touch_request": self.touch_request,
        }
        fresh_s = 8 + (float(self.cfg.get("polite_gap", 4.0)) if self.polite else 0)
        reading = self.own is not None and self.own.get("fetch") is not None   # no status polls during a file read
        st = self.status if (reading or (now - self.status_t) < fresh_s) else None
        if st is None:
            if not self.m_ip:
                v["mode"] = "searching"
            elif self._held_by_other():
                v["mode"] = "direct"
            else:
                v["mode"] = "connecting"
            return v

        state = st["state"]
        v["state"] = state
        v["state_text"] = STATE_WORDS.get(state, state)
        job = self.job if (self.job and st["playing"]) else None
        prof = job.get("profile") if job else None
        passed = self._passed(prof, job.get("stops")) if prof is not None else 0

        if job:
            line = st["line"]
            elapsed = st["elapsed"]
            jv = {
                "name": self._pretty(job.get("path", "")) or "Job",
                "file": os.path.basename(job.get("path", "")),
                "elapsed": elapsed,
                "known": prof is not None,
                "loading": bool(job.get("path")) and prof is None and self.store.is_pending(job["path"]),
                "percent": float(st["percent"]),
                "left": None,
                "next": None,
                "changes_total": 0,
                "changes_left": None,
                "segments": [],
                "now": self.tool_info(st.get("tool"), prof) if st.get("tool", -1) >= 0 else None,
                "preview": self.preview_of(prof.d) if prof is not None else "",
            }
            if prof is not None:
                changes = prof.changes
                est = prof.time_at(line) if line > 0 else 0.0
                if passed > 0:
                    est = max(est, changes[passed - 1]["t"])
                ratio = self._update_ratio(job, elapsed, est)
                total = prof.total
                jv["percent"] = max(0.0, min(100.0, 100.0 * est / total)) if total > 0 else 0.0
                jv["left"] = max(0.0, (total - est) * ratio)
                jv["changes_total"] = len(changes)
                idx = prof.next_change(line)
                idx = len(changes) if idx is None else idx
                idx = max(idx, passed)
                jv["changes_left"] = len(changes) - idx
                if idx < len(changes):
                    ch = changes[idx]
                    nx = self.tool_info(ch["tool"], prof)
                    nx["in"] = max(0.0, (ch["t"] - est) * ratio)
                    nx["index"] = idx + 1
                    nx["total"] = len(changes)
                    jv["next"] = nx
                for tool, a, b in prof.segments():
                    ti = self.tool_info(tool, prof)
                    jv["segments"].append({"tool": tool, "code": ti["code"], "start": round(a, 4),
                                           "end": round(b, 4), "color": ti["color"], "unset": ti["unset"]})
                for t in prof.d.get("used_tools", []):
                    v["legend"].append(self.tool_info(t, prof))
            elif st["percent"] >= 2:
                jv["left"] = elapsed * (100 - st["percent"]) / max(1, st["percent"])
            v["job"] = jv
            v["mode"] = "paused" if state in ("Hold", "Pause") else "running"
        else:
            v["mode"] = "idle"
            if self.last_job and now - self.last_job["ended"] < 12 * 3600:
                v["last_job"] = self.last_job

        target = st.get("target_tool")
        if state == "Tool" and target is not None and target >= 0:
            tc = self.tool_info(target, prof)
            cur = st.get("tool")
            if cur is not None and cur >= 0 and cur != target:
                tc["out"] = self.tool_info(cur, prof)      # the bit coming out, and the color of its container
            if prof is not None:
                changes = prof.changes
                k = next((i for i in range(passed, len(changes)) if changes[i]["tool"] == target), None)
                if k is not None:
                    tc["index"] = k + 1
                    tc["total"] = len(changes)
                    after = k + 1
                    here = changes[k]["t"]
                else:
                    after = passed  # the probe (or a bit not in the file) comes before the next change
                    here = max(prof.time_at(max(1, st.get("line", 0))),
                               changes[passed - 1]["t"] if passed > 0 else 0.0)
                if after < len(changes):
                    nx = changes[after]
                    then = self.tool_info(nx["tool"], prof)
                    then["in"] = max(0.0, (nx["t"] - here) * ((job or {}).get("ratio") or 1.0))
                    then["index"] = after + 1
                    tc["then"] = then
            v["toolchange"] = tc
            v["mode"] = "toolchange"
        if state == "Alarm" or st.get("halt"):
            v["alarm"] = st.get("halt_text") or "Machine stopped"
            v["mode"] = "alarm"
        return v

    @staticmethod
    def _update_ratio(job: dict, elapsed: float, est: float) -> float:
        """How fast the machine runs compared with the estimate.

        Measured over the last stretch of cutting (at least 10 estimated
        minutes), so a long probing or leveling step at the start does not
        skew it. Starts at 1.0 until there is enough data.
        """
        samples = job.setdefault("samples", [])
        ratio = job.get("ratio") or 1.0
        if est <= 0:
            return ratio
        if samples and est < samples[-1][1]:
            samples.clear()
        if samples and est - samples[-1][1] < 15:
            return ratio  # nothing new since the last sample
        samples.append((elapsed, est))
        if len(samples) > 400:
            del samples[:100]
        window = None
        for e0, s0 in samples:
            if est - s0 >= 600:
                window = (e0, s0)
            else:
                break
        if window is not None:
            e0, s0 = window
            r = max(0.6, min(1.8, (elapsed - e0) / (est - s0)))
            ratio = r if job.get("ratio") is None else job["ratio"] * 0.8 + r * 0.2
            job["ratio"] = ratio
            while len(samples) > 2 and est - samples[1][1] > 2700:
                samples.pop(0)
        return ratio

    async def view_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.view_event.wait(), 0.25 if self.demo else 1.0)
            except asyncio.TimeoutError:
                pass
            self.view_event.clear()
            try:
                v = self.compute_view()
                self._bits(v)
                self._notify(v)
                if self.demo is not None:
                    v = self._demo_view(v)
                v["screen"] = self.screen_info()
                self.view = v
                self.view_seq += 1
            except Exception:
                log.exception("could not build the screen data")
            await asyncio.sleep(0.2)

    # ------------------------------------------------------------ bit life
    def bit_material(self, prof=None) -> str:
        """The material counted against: the plate on record's, else the one the job file names (the job on now,
        or prof, a profile's data)."""
        pl = self.plates.plate if getattr(self, "plates", None) is not None and self.plates.is_set else {}
        m = (pl or {}).get("material") or ""
        if prof is None and (self.job or {}).get("profile") is not None:
            prof = self.job["profile"].d
        if not m and prof is not None:
            m = (prof.get("notes") or {}).get("plate_note_material") or prof.get("material") or ""
        return m

    def bit_warnings(self, prof) -> list:
        """For the review screen before a start: bits the job would take past their expected life."""
        try:
            warn = self.bitlife.plan(self.bit_needs(Profile(prof)), self.bit_material(prof))
            return [self.bitlife.plan_text(w, short=True) for w in warn]
        except Exception:
            log.exception("could not check the bits' life")
            return []

    def bit_needs(self, prof) -> dict:
        """How long each bit of a job cuts: {slot: (kind, seconds)}, the probe and laser left out."""
        needs = {}
        if prof is None:
            return needs
        for tool, a, b in prof.segments():
            if tool in bitlife_mod.NOT_BITS or tool is None:
                continue
            label = bitlife_mod.kind_of(self.tool_info(tool, prof))
            old = needs.get(tool, (label, 0.0))[1]
            needs[tool] = (label, old + (b - a) * prof.total)
        return needs

    def _bits(self, v: dict) -> None:
        """Counts the time of the bit in the spindle, names the bit to put in at a stop and, once a job is on,
        warns about bits it would take past their expected life. The real machine only, never the demo."""
        try:
            mat = self.bit_material()
            self.bit_tracker.watch(v, mat)
            tc = v.get("toolchange")
            if tc and tc.get("tool") not in bitlife_mod.NOT_BITS:
                cur = self.bitlife.d["slots"].get(str(tc["tool"])) or {}
                adds = (tc.get("then") or {}).get("in")
                if adds is None:
                    adds = (v.get("job") or {}).get("left") or 0
                st = self.bitlife.status(cur.get("bit"), mat, job_adds=adds)
                if st:
                    tc["bit"] = dict(st, text=self.bitlife.short_text(st))
                    if st.get("limit") and st["state"] != "over" and st["used"] + st["job_adds"] > st["limit"]:
                        tc["bit"]["state"] = "near"
            job = v.get("job")
            if job and v.get("mode") in ("running", "paused"):
                self._bits_running(job, mat)
            if v.get("mode") == "idle":
                self.bit_plan = {"file": None, "warnings": []}
            elif job and job.get("known") and job.get("file") != self.bit_plan["file"]:
                prof = (self.job or {}).get("profile")
                warn = self.bitlife.plan(self.bit_needs(prof), mat)
                self.bit_plan = {"file": job.get("file"), "warnings": [self.bitlife.plan_text(w) for w in warn]}
                conf = notify_mod.settings(self.cfg)
                if warn and conf["topic"] and conf["stops"]:
                    self.notifier.send_later(conf, dict(notify_mod.bits_message(self.bit_plan["warnings"],
                                                                                job.get("name") or ""),
                                                        click=self.phone_url()))
            if job and self.bit_plan["warnings"] and job.get("file") == self.bit_plan["file"]:
                job["bit_warnings"] = list(self.bit_plan["warnings"])
        except Exception:
            log.exception("could not count bit time")

    def _bits_running(self, job: dict, mat: str) -> None:
        """For the running screen: the bit cutting now (its time so far in this stretch counted in) and the bit
        the next stop will name, with how long it will cut until the stop after."""
        life = self.bitlife
        now, nx = job.get("now"), job.get("next")
        bid, secs = self.bit_tracker.cutting()
        if now and bid and now.get("tool") not in bitlife_mod.NOT_BITS:
            st = life.status(bid, mat)
            if st:
                st["used"] += round(secs)
                if st.get("limit"):
                    st["state"] = "over" if st["used"] >= st["limit"] else \
                        "near" if st["used"] >= bitlife_mod.NEAR * st["limit"] else "ok"
                adds = nx.get("in") if nx else job.get("left")      # until the next stop, or the job's end
                now["bit"] = dict(st, used_text=bitlife_mod.hm(st["used"]), job_adds=adds or 0)
        if nx and isinstance(nx.get("tool"), int) and nx["tool"] not in bitlife_mod.NOT_BITS:
            nb = self.bit_tracker.next_for(nx["tool"], bitlife_mod.kind_of(nx))
            adds = 0.0
            prof = (self.job or {}).get("profile")
            k = nx.get("index")
            if prof is not None and k:
                ch = prof.changes
                end = ch[k]["t"] if k < len(ch) else prof.total
                adds = max(0.0, (end - ch[k - 1]["t"]) * ((self.job or {}).get("ratio") or 1.0))
            st = life.status(nb, mat, job_adds=adds) if nb else {}
            if st:
                nx["bit"] = dict(st, text=life.short_text(st))

    def bitlife_info(self) -> dict:
        return dict(self.bitlife.info(), material=bitlife_mod.material_key(self.bit_material()))

    def change_bitlife(self, data: dict) -> dict:
        life, a = self.bitlife, str(data.get("action", ""))
        if a == "next":
            life.next_bit(int(data.get("slot")))
        elif a == "pick":
            life.pick(int(data.get("slot")), str(data.get("bit", "")))
        elif a == "add":
            life.add_bit(str(data.get("kind", "")), str(data.get("label", "") or ""))
        elif a == "retire":
            life.retire(str(data.get("bit", "")), str(data.get("reason", "")), str(data.get("note", "") or ""))
        elif a == "limit":
            life.set_limit(str(data.get("kind", "")), str(data.get("material", "")), data.get("hours"))
        else:
            raise ValueError("Unknown bit life action.")
        if self.view_event is not None:
            self.view_event.set()
        return self.bitlife_info()

    # ------------------------------------------------------------ phone notifications
    def phone_url(self) -> str:
        return self.settings_url()[:-len("/settings")] + "/"

    def _notify(self, v: dict) -> None:
        """Notifications follow the real machine only, never the demo."""
        try:
            self.notifier.watch(v, self.cfg, click=self.phone_url())
        except Exception:
            log.exception("could not check for notifications")

    async def notify_test(self) -> dict:
        conf = notify_mod.settings(self.cfg)
        if not conf["topic"]:
            raise ValueError("Save a topic first.")
        msg = dict(self.notifier.test_message(self.m_name), click=self.phone_url())
        last = await asyncio.get_running_loop().run_in_executor(None, self.notifier.send_now, conf, msg)
        if not last["ok"]:
            raise ValueError("Not sent: %s." % last["error"])
        return self.notifier.info(self.cfg)

    # ------------------------------------------------------------ bit colors (settings page)
    def bits_info(self) -> dict:
        names = self.store.tool_names() if hasattr(self.store, "tool_names") else {}
        return {
            "tools": copy.deepcopy(self.cfg.get("tools", {}) or {}),
            "kit": copy.deepcopy(self.kit_tools),
            "saved": self.tools_saved,
            "palette": [{"name": n, "color": c} for n, c in bits.PALETTE],
            "seen": names,
            "machine": self.m_name,
            "settings_url": self.settings_url(),
        }

    def set_tools(self, table) -> None:
        tools = bits.clean(table)
        bits.save(self.state_dir, tools)
        self.cfg["tools"] = tools
        self.tools_saved = True
        self.dirty()

    def reset_tools(self) -> None:
        bits.reset(self.state_dir)
        self.cfg["tools"] = copy.deepcopy(self.kit_tools)
        self.tools_saved = False
        self.dirty()

    # ------------------------------------------------------------ the Pi's small screen (settings page)
    def screen_info(self) -> dict:
        tft = self.cfg.get("tft") or {}
        flip, h24 = self.screen_prefs.get("flip"), self.screen_prefs.get("clock_24h")
        return {"display": str(self.cfg.get("display", "tft")), "flip": bool(tft.get("flip")) if flip is None else flip,
                "clock_24h": bool(tft.get("clock_24h")) if h24 is None else h24}

    def set_screen(self, data: dict) -> dict:
        prefs = dict(self.screen_prefs)
        for key, what in (("flip", "whether the picture is turned upside down"),
                          ("clock_24h", "12-hour or 24-hour times")):
            if key in data:
                if not isinstance(data[key], bool):
                    raise ValueError("Choose %s." % what)
                prefs[key] = data[key]
        if prefs == self.screen_prefs:
            return self.screen_info()
        prefs_mod.save_screen(self.state_dir, prefs)
        self.screen_prefs = prefs
        log.info("small screen: picture %s, %s clock", "upside down" if prefs.get("flip") else "the right way up",
                 "24-hour" if prefs.get("clock_24h") else "12-hour")
        self.dirty()
        return self.screen_info()

    def _preview_png(self, view: dict):
        from . import tft
        with self._preview_lock:
            if self._preview_fonts is None:
                self._preview_fonts = tft.Fonts()
            # as a person sees it: upright, whichever way round the screen is mounted
            return tft.png_bytes(tft.render(view, self._preview_fonts, flip=False))

    async def screen_png(self):
        """A picture of what the small screen shows now, or None if Pillow isn't installed."""
        if str(self.cfg.get("display", "tft")) != "tft":
            return None
        try:
            from . import tft
            if tft.Image is None:
                return None
        except Exception:
            return None
        view = copy.deepcopy(self.view)
        return await asyncio.get_running_loop().run_in_executor(None, self._preview_png, view)

    # ------------------------------------------------------------ demo and color check (screen only)
    def demo_blocked(self) -> str:
        """Why a demo can't take over the screen right now, or "" if it can."""
        mode = self.compute_view().get("mode")
        if mode in ("running", "paused", "toolchange"):
            return "The Z1 is running a job. The demo only plays while the Z1 is idle."
        if mode == "alarm":
            return "The Z1 is stopped with an alarm. Clear it in Makera Studio first."
        if mode == "direct":
            return ("Makera Studio is connected straight to the Z1, so the screen can't tell whether a job "
                    "is running. Connect through the _screen entry, or disconnect, and try again.")
        return ""

    def demo_jobs(self) -> list:
        """The built-in sample job first, then the job files the screen knows."""
        out = []
        sample = demo_mod.sample_profile()
        if sample is not None:
            out.append({"path": demo_mod.SAMPLE, "name": "Sample job (built in)", "total": sample.get("total", 0),
                        "changes": len(sample.get("changes") or []), "tools": sample.get("used_tools", [])})
        return out + (self.store.jobs() if hasattr(self.store, "jobs") else [])

    def _demo_profile(self, path: str = ""):
        d = None
        if path and path != demo_mod.SAMPLE:
            d = self.store.find(path)
        if d is None or not d.get("changes"):
            d = demo_mod.sample_profile()
        if d is None or not d.get("changes"):
            jobs = self.store.jobs() if hasattr(self.store, "jobs") else []
            d = self.store.find(jobs[0]["path"]) if jobs else None
        if d is None:
            raise ValueError("The screen doesn't know any job files yet, so there is nothing to play.")
        return d

    async def start_demo(self, path: str = "", speed=60.0) -> dict:
        why = self.demo_blocked()
        if why:
            raise DemoBlocked(why)
        prof = self._demo_profile(path)
        cfg = copy.deepcopy(self.cfg)
        loop = asyncio.get_running_loop()
        tl = await loop.run_in_executor(None, demo_mod.build_timeline, cfg, prof, self.m_name,
                                        self.settings_url())
        self.demo = demo_mod.DemoPlayer(tl, speed=speed)
        self.demo_note = ""
        log.info("demo started on the screen: %s", tl["name"])
        self.dirty()
        return self.demo_status()

    async def start_check(self, tool, entry=None) -> dict:
        why = self.demo_blocked()
        if why:
            raise DemoBlocked(why)
        try:
            n = int(tool)
        except (TypeError, ValueError):
            raise ValueError("Choose a bit number to show.")
        ent = None
        if entry:
            ent = bits.clean({str(n): entry})[str(n)]
        tl = demo_mod.build_check(self.cfg, n, ent, self.m_name, self.settings_url())
        self.demo = demo_mod.DemoPlayer(tl, kind="check")
        self.demo_note = ""
        self.dirty()
        return self.demo_status()

    def demo_press(self) -> dict:
        if self.demo is not None and self.demo.kind == "demo":
            self.demo.press(time.time())
            self.dirty()
        return self.demo_status()

    def demo_skip(self) -> dict:
        if self.demo is not None:
            self.demo.skip(time.time())
            self.dirty()
        return self.demo_status()

    def demo_speed(self, speed) -> dict:
        if self.demo is not None:
            self.demo.set_speed(speed)
            self.dirty()
        return self.demo_status()

    def stop_demo(self, note: str = "") -> dict:
        if self.demo is not None:
            log.info("demo ended (%s)", note or "stopped from the settings page")
        self.demo = None
        self.demo_note = note
        self.dirty()
        return self.demo_status()

    def demo_status(self) -> dict:
        if self.demo is None:
            return {"active": False, "note": self.demo_note, "blocked": self.demo_blocked()}
        return self.demo.status(time.time())

    def _demo_view(self, real: dict) -> dict:
        """The demo's screen, unless the real machine needs the screen."""
        mode = real.get("mode")
        if mode not in DEMO_OK:
            if mode == "direct":
                note = "The demo stopped because Makera Studio connected straight to the Z1."
            elif mode == "alarm":
                note = "The demo stopped because the Z1 reported an alarm."
            else:
                note = "The demo stopped because the Z1 started a job."
            self.stop_demo(note)
            return real
        now = time.time()
        d = self.demo
        d.tick(now)
        why = d.finished(now)
        if why:
            if d.kind == "check":
                note = ""
            elif why == "done":
                note = "The demo finished."
            else:
                note = "The demo ended after 30 minutes."
            self.stop_demo(note)
            return real
        return d.view(now)

    def debug_info(self) -> dict:
        st = self.status
        job = self.job or {}
        return {
            "version": __version__,
            "uptime": round(time.time() - self.started),
            "connection_mode": "polite" if self.polite else "hold",
            "machine": {"name": self.m_name, "ip": self.m_ip, "port": self.m_port, "busy": self.m_busy,
                        "seen_ago": round(time.time() - self.m_seen, 1) if self.m_seen else None,
                        "fw": self.fw, "extra": self.m_extra},
            "my_ip": self.my_ip,
            "own_connected": self.own is not None,
            "app_connected": len(self.clients),
            "app_connections": self.client_count,
            "status_age": round(time.time() - self.status_t, 1) if self.status_t else None,
            "status": {k: v for k, v in (st or {}).items() if k != "fields"},
            "job": {k: v for k, v in job.items() if k not in ("profile", "samples")},
            "job_has_timing": bool(job.get("profile")),
            "profiles": self.store.count(),
            "bit_colors": "settings page" if self.tools_saved else "settings file",
            "start_from_screen": self.start_enabled(),
            "plate_set": self.plates.is_set,
            "app_origin": getattr(self, "app_origin", None),
            "small_screen": self.screen_info(),
            "demo": self.demo_status(),
            "captures_done": self.captures_done,
            "anchor1": self.anchor1,
            "anchor1_from": self.anchor_source,
            "machine_config": dict(self.machine_cfg, error=self._cfg_error, tries=self._cfg_tries),
            "reads": {p: {k: v for k, v in r.items() if k not in ("task", "obj")} for p, r in self.reads.items()},
            "plate": {"size": self.plates.plate, "cuts": [{k: v for k, v in c.items() if k != "shape"}
                                                          for c in self.plates.cuts]},
            "listings": {k: {"age": round(time.time() - v["t"]), "files": len(v["entries"])}
                         for k, v in self.listings.items()},
            "last_start": self.last_start,
            "touch_calibration": self.touch_cal()[0] or "none",
            "screen_size": self.screen_size,
            "last_commands": list(self.commands)[-30:],
            "last_console": list(self.console)[-30:],
        }

    # ------------------------------------------------------------ start
    async def run(self, web) -> None:
        loop = asyncio.get_running_loop()
        self.loop = loop
        self.view_event = asyncio.Event()
        dport = int(self.cfg.get("discovery_port", 3333))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.bind(("0.0.0.0", dport))
        disc, _ = await loop.create_datagram_endpoint(lambda: Hub._Disc(self), sock=sock)
        if self.m_ip:
            self._update_my_ip()
        relay = await asyncio.start_server(self.on_relay_client, "0.0.0.0", int(self.cfg.get("relay_port", 2222)))
        log.info("relay listening on port %s (%s connection)", self.cfg.get("relay_port", 2222),
                 "polite" if self.polite else "held")
        tasks = [
            asyncio.create_task(self.announcer()),
            asyncio.create_task(self.own_loop()),
            asyncio.create_task(self.view_loop()),
            asyncio.create_task(self.jobs_loop()),
            asyncio.create_task(web.serve()),
        ]
        for p in self.cfg.get("forward_ports", []) or []:
            tasks.append(asyncio.create_task(self.forward_server(p)))
        try:
            async with relay:
                await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            disc.close()
