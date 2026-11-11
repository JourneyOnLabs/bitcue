"""The job list, the plate on the machine, and starting a job from the small touch screen.

Part of the hub (Hub inherits JobsMixin). What it adds:

  * Folder listings of the Z1's card. The Pi reads the answers to Makera
    Studio's own "ls" requests as they pass through the relay, and asks for a
    folder itself only on its own connection (never in the middle of Makera
    Studio's), so Makera Studio's file browser is never disturbed.
  * Reading a job file from the Z1 (fetch.py) when the Pi hasn't seen it, so
    any file already on the machine can be placed and started.
  * The plate on the machine and what is cut on it (plates.py, platemap.py).
    Every job that runs through the Pi is recorded where it was cut, whether
    it was started on the Pi or in Makera Studio.
  * Placing a job on the plate: the Pi suggests a clear place, the small
    screen moves the part, and the Pi checks the place again at the start.
  * Starting a job: the three commands Makera Studio sends at Run (start.py),
    only when starting from the small screen is turned on (settings page), only
    when the small screen on this Pi asks for it (the request must come from
    the Pi itself), only while the Z1 is idle with no alarm, and only where the
    placement check passes.
  * Anchor 1 from the Z1's own settings file (/sd/config.txt), when the
    settings say "anchor1": "auto" (the default); otherwise the file is read
    once to compare.
  * The setup section of the settings page (setup_info, set_setup), kept in
    prefs.json in the state folder (prefs.py).
  * The touch screen's calibration, kept in the state folder.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time

from . import __version__
from . import fetch
from . import framing as fr
from . import notify as notify_mod
from . import platemap as pm
from . import plates as plates_mod
from . import prefs as prefs_mod
from . import quicklz
from . import start as start_mod
from .gcode import Profile

log = logging.getLogger("z1screen.jobs_api")

ROOT = "/sd/gcodes"
TOUCH_FILE = "touch.json"
PITFT_SCREEN = [320, 240]   # the PiTFT's framebuffer: a touch calibration saved without its screen was made on it
# Built-in touch calibrations by framebuffer size, for a screen with none saved for it. The 640 x 480 one was made on
# a Waveshare 3.5inch DPI screen (capacitive, Goodix) in its housing, mounted upside down. Resistive PiTFT panels
# vary too much for one.
BUILT_IN_TOUCH = {(640, 480): {"ax": [-0.66246, -0.00757, 322.105], "ay": [-0.03491, -0.39334, 247.210], "flip": True}}
LISTING_FRESH = 600.0       # a folder listing younger than this is trusted for "is the file there"
JOB_EXT = (".nc", ".gcode", ".ngc", ".tap", ".cnc")
SEED = os.path.join("seed", "plate.json")
MACHINE_CONFIG = "/sd/config.txt"
NAME_WAIT = 120.0           # after joining a running job, wait this long for its file name before adding its area


def _parse_listing(text: str) -> list:
    """Lines of 'ls -e -s': name (spaces as \\x01, folders end in /), size, date YYYYmmddHHMMSS."""
    out = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("<"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0].startswith("."):
            continue
        name = parts[0].replace("\x01", " ")
        is_dir = name.endswith("/")
        if is_dir:
            name = name[:-1]
        try:
            size = int(parts[1])
        except ValueError:
            continue
        out.append({"name": name, "dir": is_dir, "size": size, "date": parts[2] if len(parts) > 2 else ""})
    return out


def _ls_dir_of(text: str) -> str:
    """The folder of an 'ls' command line, or "" if it isn't one."""
    parts = text.strip().split()
    if not parts or parts[0] != "ls":
        return ""
    path = ""
    for p in parts[1:]:
        if not p.startswith("-"):
            path = p
    path = path.replace("\x01", " ").rstrip("/") or "/"
    return path


_RE_ANCHOR = {k: re.compile(r"^\s*coordinate\.anchor1_%s\s+(-?\d+(?:\.\d+)?)" % k, re.M) for k in "xy"}


def anchor_from_config(text: str):
    """Anchor 1 [x, y] from the machine's config.txt (coordinate.anchor1_x / _y), or None."""
    mx, my = _RE_ANCHOR["x"].search(text), _RE_ANCHOR["y"].search(text)
    if not mx or not my:
        return None
    return [float(mx.group(1)), float(my.group(1))]


def job_label(path: str) -> str:
    """A short name for a job file: its design name if a "design_names" pattern matches (bracket-position-2.nc
    -> Bracket), else the file name without its extension."""
    base = os.path.splitext(os.path.basename(path or ""))[0]
    dk = start_mod.design_key(path)
    return start_mod.design_name(dk) if dk != base else base


class JobsMixin:
    # ------------------------------------------------------------ set up (called from Hub.__init__)
    def _init_jobs(self) -> None:
        start_mod.set_design_names(self.cfg.get("design_names"))
        self.listings = {}            # folder -> {"t": time, "entries": [...]}
        self.layout_folders = set(self.cfg.get("plate_layouts") or [])
        self.plates = plates_mod.Plates(self.state_dir, self._seed_path(), self.cfg.get("plate"))
        self.line_waiters = []        # (test, future) waiting for a console line from the machine
        self.starting = False
        self.last_start = None        # what happened at the last start from the small screen
        self.touch = self._load_touch()
        self.screen_size = None       # [w, h] of the small screen's framebuffer, as its program last named it
        self.touch_request = 0        # counts "calibrate the touch screen" presses on the settings page
        self.reads = {}               # path -> {"state", "error", "obj", "t"}: files being read from the Z1
        self.preview_tried = set()    # paths read again just for their part's picture (once per start)
        self._bg_read = None          # {"obj": FileRead} while a file is read again just for its picture
        self.machine_cfg = {}         # what the Pi read from the Z1's config.txt
        self._cfg_tries, self._cfg_next, self._cfg_error = 0, 0.0, ""
        self._own_lock_obj = None
        self.app_origin = None        # the last work origin (G10) Makera Studio set through the Pi
        self.anchor1, self.anchor_auto, self.anchor_source = None, True, ""
        self._set_anchor(self.cfg.get("anchor1"))

    def _set_anchor(self, a) -> None:
        """Anchor 1 from the settings: [x, y], or anything else for "read it from the machine's config.txt"."""
        if isinstance(a, (list, tuple)) and len(a) == 2:
            self.anchor1 = [float(a[0]), float(a[1])]
            self.anchor_auto = False
            self.anchor_source = "settings"
        else:
            self.anchor_auto = True
            read = (self.machine_cfg or {}).get("anchor1")
            self.anchor1 = list(read) if read else None
            self.anchor_source = "the Z1's config.txt" if read else ""

    def start_enabled(self) -> bool:
        return self.cfg.get("start_from_screen") is True

    def settings_pin(self) -> str:
        return str(self.prefs["settings_pin"] if "settings_pin" in self.prefs else self.cfg.get("settings_pin") or "")

    def _seed_path(self) -> str:
        for d in getattr(self.store, "library_dirs", []) or []:
            p = os.path.join(d, SEED)
            if os.path.isfile(p):
                return p
        return ""

    def _own_lock(self) -> asyncio.Lock:
        if self._own_lock_obj is None:
            self._own_lock_obj = asyncio.Lock()
        return self._own_lock_obj

    def _own_free(self) -> str:
        """"" if the Pi may use its own connection for a request now, else why not."""
        if self.clients:
            return ("Makera Studio is connected through the screen, so the Pi can't read files from the Z1 "
                    "right now. Disconnect it in Makera Studio and try again.")
        if self.own is None:
            return "The Pi isn't connected to the Z1 right now."
        if self.own.get("text"):
            return "This Z1 answers in the older text protocol, which the Pi can't read files with."
        if self.job is not None or (self.status or {}).get("playing"):
            return "The Z1 is running a job. The Pi reads files while it is idle."
        return ""

    # ------------------------------------------------------------ listings
    def note_app_command(self, sess, text: str) -> None:
        """Remember which folder an 'ls' from Makera Studio was for, so its answer can be read."""
        if sess is None:
            return
        d = _ls_dir_of(text)
        sess["ls_dir"] = d or None
        sess["ls_buf"] = []

    def tap_load_frame(self, sess, ptype: int, payload: bytes) -> None:
        """LOAD_INFO / LOAD_FINISH / LOAD_ERROR frames: the answer to a folder listing."""
        d = sess.get("ls_dir")
        if not d:
            return
        if ptype == fr.LOAD_INFO:
            sess.setdefault("ls_buf", []).append(payload)
        elif ptype == fr.LOAD_FINISH:
            text = b"".join(sess.get("ls_buf") or []).decode("utf-8", "replace")
            self.listings[d] = {"t": time.time(), "entries": _parse_listing(text)}
            sess["ls_dir"], sess["ls_buf"] = None, []
            ev = getattr(self, "_listing_event", None)
            if ev is not None:
                ev.set()
            self.dirty()
        elif ptype == fr.LOAD_ERROR:
            sess["ls_dir"], sess["ls_buf"] = None, []

    async def list_folder(self, folder: str, timeout: float = 4.0) -> bool:
        """Ask the Z1 for a folder's files on the Pi's own connection. False if that isn't possible now."""
        if self._own_free() or time.time() < getattr(self, "_ls_off_until", 0.0):
            return False
        self._yield_own()
        async with self._own_lock():
            sess = self.own
            if self._own_free():
                return False
            self._listing_event = asyncio.Event()
            sess["ls_dir"], sess["ls_buf"] = folder, []
            sess["hold"] = sess.get("hold", 0) + 1
            try:
                sess["writer"].write(fr.build_frame(fr.CTRL_MULTI,
                                                    ("ls -e -s " + start_mod.escape_path(folder)).encode()))
                await sess["writer"].drain()
                await asyncio.wait_for(self._listing_event.wait(), timeout)
                return folder in self.listings and time.time() - self.listings[folder]["t"] < timeout + 1
            except Exception:
                # no listing came back: don't ask again for an hour (the job list then uses what the Pi knows)
                self._ls_off_until = time.time() + 3600
                log.info("the Z1 didn't answer a folder listing; not asking again for an hour")
                sess["ls_dir"], sess["ls_buf"] = None, []
                return False
            finally:
                sess["hold"] = max(0, sess.get("hold", 1) - 1)
                self._listing_event = None

    async def refresh_listings(self) -> None:
        if not await self.list_folder(ROOT):
            return
        for e in self.listings.get(ROOT, {}).get("entries", []):
            if e["dir"]:
                await self.list_folder(ROOT + "/" + e["name"])

    def _on_card(self, path: str):
        """True / False if a fresh enough listing says whether the file is on the Z1's card, None if unknown."""
        folder = os.path.dirname(path)
        lst = self.listings.get(folder)
        if not lst or time.time() - lst["t"] > LISTING_FRESH:
            # no listing of its own folder: a fresh listing of the folder above may show the folder isn't there
            up = self.listings.get(os.path.dirname(folder))
            if folder != ROOT and up and time.time() - up["t"] <= LISTING_FRESH:
                if not any(e["dir"] and e["name"] == os.path.basename(folder) for e in up["entries"]):
                    return False
            return None
        name = os.path.basename(path)
        return any(e["name"] == name and not e["dir"] for e in lst["entries"])

    # ------------------------------------------------------------ reading files from the Z1
    async def _read_bytes(self, path: str, rs=None):
        """The raw bytes of a file on the Z1's card (and the checksum the Z1 gave). Raises fetch.ReadError."""
        async with self._own_lock():
            why = self._own_free()
            if why:
                raise fetch.ReadError(why)
            sess = self.own
            f = fetch.FileRead(path)
            if rs is not None:
                rs.update(state="reading", obj=f)
            sess["fetch"] = f
            try:
                data = await asyncio.wait_for(f.run(sess["writer"]), 900)
                return data, f.md5
            except asyncio.TimeoutError:
                raise fetch.ReadError("Reading the file took too long.")
            except (ConnectionError, OSError):
                raise fetch.ReadError("The connection to the Z1 closed while reading the file.")
            except fetch.ReadError:
                if self.clients:
                    raise fetch.ReadError("Makera Studio connected through the screen, so the Pi stopped reading.")
                raise
            finally:
                sess["fetch"] = None

    def read_status(self, path: str) -> dict:
        r = self.reads.get(path)
        if self.store.exact(path) is not None and (not r or r.get("state") == "done"):
            return {"state": "done", "pct": 100, "error": ""}
        if not r:
            return {"state": "none", "pct": 0, "error": ""}
        obj = r.get("obj")
        pct = 100 if r["state"] in ("profiling", "done") else int(100 * obj.progress()) if obj else 0
        return {"state": r["state"], "pct": pct, "error": r.get("error", "")}

    def start_read(self, path: str) -> dict:
        """Start reading a job file from the Z1 (if it isn't known or being read already)."""
        path = (path or "").strip()
        if not path.startswith(ROOT + "/") or not path.lower().endswith(JOB_EXT):
            raise ValueError("That isn't a job file on the Z1.")
        if self.store.exact(path) is not None:
            return self.read_status(path)
        r = self.reads.get(path)
        if r and r.get("state") in ("waiting", "reading", "profiling"):
            return self.read_status(path)
        self._yield_own()
        why = self._own_free()
        if why:
            self.reads[path] = {"state": "error", "error": why, "t": time.time()}
            return self.read_status(path)
        self.reads[path] = {"state": "waiting", "error": "", "t": time.time()}
        self.reads[path]["task"] = asyncio.ensure_future(self._read_task(path))
        return self.read_status(path)

    async def read_file(self, path: str):
        """Read a job file now (waits); returns its profile or None."""
        self.start_read(path)
        task = (self.reads.get(path) or {}).get("task")
        if task is not None:
            await asyncio.shield(task)
        return self.store.exact(path)

    async def _read_task(self, path: str) -> None:
        rs = self.reads[path]
        try:
            data, md5 = await self._read_bytes(path, rs)
            rs["state"] = "profiling"
            loop = asyncio.get_running_loop()
            prof = await loop.run_in_executor(None, self.store.add_file, path, data, md5, "read")
            rs["state"] = "done"
            if self.store.needs_preview(prof):          # the picture follows; the job can be placed meanwhile
                asyncio.ensure_future(self._draw_preview(prof["md5"], data))
        except (fetch.ReadError, ValueError) as exc:
            rs.update(state="error", error=str(exc))
            log.info("could not read %s from the Z1: %s", path, exc)
        except Exception:
            log.exception("could not read %s from the Z1", path)
            rs.update(state="error", error="The Pi couldn't read this file.")
        finally:
            rs["t"] = time.time()
            rs.pop("task", None)
            rs.pop("obj", None)
            self.dirty()

    async def _draw_preview(self, md5: str, data: bytes) -> None:
        if await asyncio.get_running_loop().run_in_executor(None, self.store.make_preview, md5, data):
            self.dirty()

    async def _backfill_preview(self) -> bool:
        """Reads one job file the Pi knows but has no picture of (profiles from before pictures, or captured
        while busy) and draws its part. True if it read one."""
        for p in self._profiles():
            path = p["path"]
            if path in self.preview_tried or not self.store.needs_preview(p) or self._on_card(path) is False:
                continue
            self.preview_tried.add(path)
            self._bg_read = {}
            try:
                data, md5 = await self._read_bytes(path, self._bg_read)
            except fetch.ReadError as exc:
                log.info("stopped reading %s for its picture: %s", path, exc)
                self.preview_tried.discard(path)      # try again later (Makera Studio connected, for example)
                return True
            finally:
                self._bg_read = None
            loop = asyncio.get_running_loop()
            raw = quicklz.decompress_container(data) if quicklz.looks_compressed(data) else data
            if hashlib.md5(raw).hexdigest() != p["md5"]:   # the file changed on the card: profile it again too
                p = await loop.run_in_executor(None, self.store.add_file, path, data, md5, "read")
            await self._draw_preview(p["md5"], raw)
            return True
        return False

    def _yield_own(self) -> None:
        """Someone needs the Pi's own connection now (a tap on JOBS, a file to read, a start): a file being read
        again only for its picture stops at once, and is read again later."""
        f = (self._bg_read or {}).get("obj")
        if f is not None:
            f.stop("The Pi stopped reading this file for its picture, to do what was asked on the screen.")

    async def _read_machine_config(self) -> None:
        """Anchor 1 from the Z1's own config.txt (used when the settings say "anchor1": "auto")."""
        self._cfg_tries += 1
        self._cfg_next = time.time() + 600
        try:
            data, _ = await self._read_bytes(MACHINE_CONFIG)
            if quicklz.looks_compressed(data):
                data = quicklz.decompress_container(data)
            a = anchor_from_config(data.decode("utf-8", "replace"))
            self.machine_cfg = {"anchor1": a, "t": time.time(), "bytes": len(data)}
            self._cfg_error = "" if a else "config.txt has no coordinate.anchor1_x / _y"
            if a and self.anchor_auto:
                self.anchor1, self.anchor_source = a, "the Z1's config.txt"
                log.info("Anchor 1 from the Z1's config.txt: X%s Y%s", a[0], a[1])
            elif a and self.anchor1 and (abs(a[0] - self.anchor1[0]) > 0.01 or abs(a[1] - self.anchor1[1]) > 0.01):
                log.warning("Anchor 1 in the Z1's config.txt (X%s Y%s) differs from the settings (X%s Y%s)",
                            a[0], a[1], self.anchor1[0], self.anchor1[1])
        except Exception as exc:
            self._cfg_error = str(exc)
            log.info("could not read the Z1's config.txt (%s)", exc)
        self.dirty()

    async def jobs_loop(self) -> None:
        """While the Z1 is idle: read its config.txt once, and the files of cut areas the Pi hadn't read."""
        while True:
            await asyncio.sleep(3.0)
            try:
                if self._own_free() or self.starting or self.demo is not None:
                    continue
                if (not self.machine_cfg and self._cfg_tries < 3 and time.time() >= self._cfg_next
                        and self.cfg.get("read_machine_config", True)):
                    await self._read_machine_config()
                    continue
                for c in self.plates.unknown():
                    f = c.get("file") or ""
                    if not f or c.get("unreadable") or self._own_free():
                        continue
                    prof = self.store.exact(f) or await self.read_file(f)
                    if prof is not None and prof.get("cut"):
                        self.plates.update(c["id"], shape=prof["cut"])
                        log.info("cut area %s (%s) is known now", c["id"], f)
                    elif prof is not None or (self.reads.get(f) or {}).get("state") == "error":
                        self.plates.update(c["id"], unreadable=True)
                    self.dirty()
                if not self._own_free() and self.cfg.get("read_for_previews", True):
                    await self._backfill_preview()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("jobs loop error")

    # ------------------------------------------------------------ jobs for the screens
    def _profiles(self) -> list:
        return [p for p in self.store._all() if (p.get("path") or "").startswith(ROOT + "/")]

    def _run_for(self, prof):
        return start_mod.run_for(prof, self.cfg.get("run_defaults"))

    def _layout_of(self, prof) -> str:
        path = prof.get("path") or ""
        folder = os.path.dirname(path)
        return folder if folder in self.layout_folders and (prof.get("notes") or {}).get("part") else ""

    def _why_not(self, prof) -> str:
        """Why this file can't be started from the Pi now (apart from where it goes), or ""."""
        if prof is None:
            return "The Pi hasn't read this file yet."
        if not prof.get("cut"):
            return "The Pi found no cutting moves in this file, so it can't place it."
        run, _ = self._run_for(prof)
        if run.get("anchor") != 1:
            return "This file's origin is Anchor %s; the Pi places jobs from Anchor 1 only." % run.get("anchor")
        if self.anchor1 is None:
            return "Anchor 1 isn't known yet (settings page, Setup)."
        if not self.plates.is_set:
            return "Set the plate size on the settings page first."
        if self._on_card(prof["path"]) is False:
            return "This file is no longer on the Z1."
        return ""

    def machine_ready(self) -> str:
        """"" if the Z1 can be started now, else why not."""
        st = self.status
        if self.m_ip == "" or st is None or time.time() - self.status_t > 4:
            return "The Pi has no fresh word from the Z1."
        if self.demo is not None:
            return "A demo or color check is on the screen. Stop it on the settings page first."
        if st.get("halt") or st.get("state") == "Alarm":
            return "The Z1 is stopped with an alarm. Unlock it in Makera Studio first."
        if self.job is not None and not st.get("playing") and st.get("state") == "Idle":
            return "The Z1 is just finishing a job. Try again in a few seconds."
        if st.get("playing") or self.job is not None or st.get("state") != "Idle":
            return "The Z1 is busy (%s)." % (st.get("state") or "?")
        if self.own is None and not self.clients:
            return "The Pi isn't connected to the Z1 right now."
        return ""

    def jobs_view(self) -> dict:
        """Folders on the Z1 and what is in them, for the Jobs screen.

        A layout folder (plate_layouts in the settings) lists its designs, each
        with a spot map; any other folder lists its files, each placed freely."""
        folders = {}

        def folder_entry(folder):
            return folders.setdefault(folder, {"path": folder, "name": "Top folder" if folder == ROOT
                                               else os.path.basename(folder),
                                               "layout": folder in self.layout_folders, "items": {}})

        for p in self._profiles():
            path = p["path"]
            if self._on_card(path) is False:
                continue                      # a file the Pi knows that isn't on the card any more
            folder = os.path.dirname(path)
            f = folder_entry(folder)
            why = self._why_not(p)
            if f["layout"] and (p.get("notes") or {}).get("part"):
                dk = start_mod.design_key(path)
                d = f["items"].setdefault("d:" + dk, {"kind": "design", "key": dk, "name": start_mod.design_name(dk),
                                                      "files": 0, "spots": 0, "startable": 0,
                                                      "time": p.get("total", 0), "preview": ""})
                d["preview"] = d["preview"] or self.preview_of(p)
                d["files"] += 1
                d["spots"] += 1
                if not why:
                    d["startable"] += 1
            else:
                f["items"]["f:" + path] = {"kind": "file", "path": path, "name": job_label(path),
                                           "file": os.path.basename(path), "read": True,
                                           "time": p.get("total", 0), "why_not": why, "preview": self.preview_of(p)}
        for folder, lst in self.listings.items():
            if folder != ROOT and not folder.startswith(ROOT + "/"):
                continue
            for e in lst["entries"]:
                if e["dir"] or not e["name"].lower().endswith(JOB_EXT):
                    continue
                path = folder + "/" + e["name"]
                if self.store.exact(path) is not None:
                    continue
                f = folder_entry(folder)
                f["items"]["f:" + path] = {"kind": "file", "path": path, "name": job_label(path), "file": e["name"],
                                           "read": False, "size": e["size"], "time": 0,
                                           "reading": self.read_status(path)}
        out = []
        for f in sorted(folders.values(), key=lambda f: (f["path"] != ROOT, f["name"].lower())):
            items = sorted(f["items"].values(), key=lambda d: d["name"].lower())
            if items:
                out.append(dict(f, items=items))
        return {"folders": out, "ready": self.machine_ready(), "listed": sorted(self.listings),
                "read_blocked": self._own_free(), "start_enabled": self.start_enabled(),
                "plate_set": self.plates.is_set, "settings_url": self.settings_url()}

    # ------------------------------------------------------------ placing a job
    def _geometry(self, prof, run):
        """The job at its own place on the plate: footprint, Z probe spot, leveling box, box of the bit's path."""
        off = run.get("offset") or [0.0, 0.0]
        ox, oy = float(off[0]), float(off[1])
        foot = pm.stamp(pm.Grid.from_json(prof["cut"]), ox, oy) if prof.get("cut") else pm.Grid()
        pb = prof.get("path_box")
        path_box = [pb[0] + ox, pb[1] + oy, pb[2] + ox, pb[3] + oy] if pb else None
        probe = run.get("probe")
        probe_pt = [probe["x"] + ox, probe["y"] + oy] if probe else None
        level = None
        if run.get("level") and prof.get("box"):
            b = prof["box"]
            level = [b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy]
        return foot, probe_pt, level, path_box

    def _checker(self, except_layout: str = "") -> pm.Plate:
        pl = self.plates.plate
        return pm.Plate(pl["w"], pl["h"], self.plates.occupancy(except_layout),
                        gap=float(self.cfg.get("plate_gap", pm.GAP)))

    def _unknown_text(self, unknown) -> str:
        names = ", ".join(c.get("name") or os.path.basename(c.get("file") or "") or "a job" for c in unknown[:3])
        if any(c.get("unreadable") or not c.get("file") for c in unknown):
            return ("The Pi doesn't know the area cut by %s on this plate. Check the plate, then remove that area "
                    "on the settings page or press NEW PLATE." % names)
        if self.clients:
            return ("The Pi needs to read %s to know the area it cut on this plate, and can't while Makera Studio "
                    "is connected through the screen. Disconnect it in Makera Studio, then try again." % names)
        return "The Pi is reading %s to know the area it cut on this plate. Try again in a minute." % names

    def _file_info(self, prof, run) -> dict:
        """What the check and start screens say about a job file."""
        notes = prof.get("notes") or {}
        first = [t for t in prof.get("used_tools") or []][:1]
        prof_tools = prof.get("tools") or {}
        check = start_mod.thickness_check(prof)
        stock = prof.get("stock") or {}
        dims = notes.get("plate_note_dims") or ([stock.get("length"), stock.get("width"), stock.get("height")]
                                                if stock.get("length") else None)
        return {
            "path": prof["path"], "file": os.path.basename(prof["path"]), "name": job_label(prof["path"]),
            "time": prof.get("total", 0), "changes": len(prof.get("changes") or []),
            "plate_note": notes.get("plate_note") or "",
            "file_plate": {"w": dims[0], "h": dims[1], "t": dims[2],
                           "material": notes.get("plate_note_material") or prof.get("material") or ""} if dims else None,
            "depth": round(-float(prof["zmin"]), 2) if prof.get("zmin") is not None else None,
            "thickness": {"plate": check[0], "depth": check[1], "max_ok": check[2]} if check else None,
            "first_bit": self.tool_info(first[0], Profile(prof)) if first else None,
            "first_bit_name": prof_tools.get(str(first[0]), "") if first else "",
            "probe_first": bool(run.get("probe")),
            "preview": self.preview_of(prof),
            "bit_warnings": self.bit_warnings(prof),
        }

    def preview_of(self, prof) -> str:
        """The md5 that names a profile's part picture (/preview/<md5>.png), or "" while there is none."""
        md5 = (prof or {}).get("md5") or ""
        has = getattr(self.store, "preview_file", None)      # (the demo's stand-in store keeps no pictures)
        return md5 if md5 and has is not None and has(md5) else ""

    def place_view(self, path: str) -> dict:
        """One job file on the plate: everything the placing screen needs (heavy: run it off the event loop)."""
        prof = self.store.exact(path)
        out = {"path": path, "file": os.path.basename(path), "name": job_label(path), "read": prof is not None,
               "ready": self.machine_ready(), "plate": dict(self.plates.plate), "previous": self.plates.previous_info()}
        if prof is None:
            out.update(why_not=self._why_not(None), reading=self.read_status(path), read_blocked=self._own_free())
            return out
        run, source = self._run_for(prof)
        foot, probe, level, path_box = self._geometry(prof, run)
        layout = self._layout_of(prof)
        P = self._checker(layout)
        why = self._why_not(prof)
        unknown = self.plates.unknown()
        start_at = None
        if not why and foot:
            at = P.auto_place(foot, probe, level, path_box)
            start_at = [at[0] * pm.RES, at[1] * pm.RES] if at is not None else None
        occ_all = self.plates.occupancy()
        out.update(self._file_info(prof, run))
        out.update({
            "why_not": why, "layout": layout, "run": run, "run_source": source,
            "occ": P.occ.to_json(), "occ_all": occ_all.to_json() if layout else None,
            "cuts": [{"id": c["id"], "name": c.get("name") or "", "state": c.get("state", "cut")}
                     for c in self.plates.cuts],
            "started": [c["id"] for c in self.plates.cuts if c.get("state") == "started"],
            "started_area": self._started_area().to_json(),
            "unknown": self._unknown_text(unknown) if unknown else "",
            "foot": foot.to_json() if foot else None, "probe": probe, "level": level, "path_box": path_box,
            "gap": P.gap, "probe_edge": P.probe_edge, "start_at": start_at,
            "no_room": bool(not why and foot and start_at is None),
        })
        return out

    def _started_area(self) -> pm.Grid:
        g = pm.Grid()
        for c in self.plates.cuts:
            if c.get("state") == "started":
                g = g.union(self.plates.area(c))
        return g

    def design_view(self, folder: str, design: str) -> dict:
        """One design in a layout folder: the plate, its spots, and each spot's file."""
        profs = [p for p in self._profiles() if os.path.dirname(p["path"]) == folder]
        lay = plates_mod.layouts(profs).get(folder)
        P = self._checker(folder)
        files = {}
        for p in profs:
            if start_mod.design_key(p["path"]) != design:
                continue
            notes = p.get("notes") or {}
            part = notes.get("part")
            key = plates_mod.spot_key(part["cx"], part["cy"]) if part else ""
            run, _ = self._run_for(p)
            foot, probe, level, path_box = self._geometry(p, run)
            why = self._why_not(p)
            place = P.check(foot, 0, 0, probe, level, path_box, measure=False) if foot else None
            info = self._file_info(p, run)
            info.update({"spot": key, "run": start_mod.run_lines(run), "run_settings": run, "why_not": why,
                         "place": place["code"] if place else "", "place_text": place["text"] if place else ""})
            files[key or p["path"]] = info
        spots = []
        by_path = {p["path"]: p for p in profs}
        if lay:
            for s in sorted(lay["spots"].values(), key=lambda s: s["n"]):
                rec = self.plates.spot_cut(folder, s["key"])
                f = files.get(s["key"])
                state = rec.get("state", "cut") if rec else "free"
                why = (f or {}).get("why_not", "")
                place = f.get("place") if f else ""
                if not f and not rec:
                    # no file of this design here: still show whether the spot is in use, from another design's file
                    other = next((by_path[p] for p in s["files"].values() if p in by_path), None)
                    if other is not None and other.get("cut"):
                        orun, _ = self._run_for(other)
                        ofoot, oprobe, olevel, opb = self._geometry(other, orun)
                        place = P.check(ofoot, 0, 0, None, None, opb, measure=False)["code"]
                if not rec and place not in ("", "ok"):
                    state = "taken" if place in ("overlap", "close") else state
                    why = why or (f or {}).get("place_text", "")
                spots.append({"key": s["key"], "n": s["n"], "cx": s["cx"], "cy": s["cy"], "d": round(s["d"], 1),
                              "state": state, "cut_design": (rec or {}).get("name", ""),
                              "mine": s["key"] in files, "why": why if state == "free" else "",
                              "where": plates_mod.where(s["cx"], s["cy"], lay["plate"]["w"], lay["plate"]["h"])
                              if lay.get("plate") else ""})
        unknown = self.plates.unknown()
        return {"folder": folder, "design": design, "name": start_mod.design_name(design),
                "plate": dict(self.plates.plate), "previous": self.plates.previous_info(),
                "layout_plate": (lay or {}).get("plate"), "spots": spots,
                "files": files, "ready": self.machine_ready(), "occ": self.plates.occupancy().to_json(),
                "started_area": self._started_area().to_json(),
                "cuts": [{"id": c["id"], "name": c.get("name") or "", "state": c.get("state", "cut"),
                          "spot": c.get("spot") or "", "layout": c.get("layout") or ""} for c in self.plates.cuts],
                "unknown": self._unknown_text(unknown) if unknown else ""}

    # ------------------------------------------------------------ the plate record (screen and settings page)
    def plates_info(self) -> dict:
        info = self.plates.info()
        lays = plates_mod.layouts(self._profiles(), self.layout_folders)
        info["layouts"] = [{"folder": f, "name": os.path.basename(f),
                            "spots": [{"key": s["key"], "n": s["n"], "cx": s["cx"], "cy": s["cy"], "d": round(s["d"], 1)}
                                      for s in sorted(lay["spots"].values(), key=lambda s: s["n"])]}
                           for f, lay in sorted(lays.items())]
        def label(p):
            part = (p.get("notes") or {}).get("part")
            return job_label(p["path"]) + (", spot %d" % part["n"] if part and self._layout_of(p) else "")
        info["files"] = sorted(({"path": p["path"], "name": label(p), "folder": os.path.dirname(p["path"])}
                                for p in self._profiles() if p.get("cut")), key=lambda d: d["path"].lower())
        info["anchor1"] = self.anchor1
        info["anchor_source"] = self.anchor_source
        info["unknown"] = self._unknown_text(self.plates.unknown()) if self.plates.unknown() else ""
        info["gap"] = float(self.cfg.get("plate_gap", pm.GAP))
        return info

    def _add_file_cut(self, path: str, dx: float, dy: float, name: str = "", by: str = "hand") -> int:
        prof = self.store.exact(path)
        if prof is None:
            raise ValueError("The Pi hasn't read that file.")
        if not prof.get("cut"):
            raise ValueError("The Pi found no cutting moves in that file.")
        run, _ = self._run_for(prof)
        off = run.get("offset") or [0.0, 0.0]
        layout = self._layout_of(prof)
        part = (prof.get("notes") or {}).get("part")
        spot = plates_mod.spot_key(part["cx"], part["cy"]) if (layout and part and not dx and not dy) else ""
        return self.plates.add(name or job_label(path), path, prof["cut"], [off[0] + dx, off[1] + dy], "cut", by,
                               layout if spot else "", spot)

    def change_plate(self, data: dict) -> dict:
        action = str(data.get("action", ""))
        if action == "new":
            size = None
            if data.get("w") is not None:
                tmp = plates_mod.Plates("", "", None)
                tmp.set_size(data)
                size = {k: tmp.plate[k] for k in ("w", "h", "t", "material")}
            self.plates.new_plate(size)
        elif action == "previous":
            if not self.plates.restore_previous():
                raise ValueError("There's no earlier plate to go back to.")
        elif action == "size":
            self.plates.set_size(data)
        elif action == "remove":
            try:
                cid = int(data.get("id"))
            except (TypeError, ValueError):
                raise ValueError("Unknown cut area.")
            if not self.plates.remove(cid):
                raise ValueError("Unknown cut area.")
        elif action == "add_file":
            dx, dy = _offset(data.get("offset"))
            self._add_file_cut(str(data.get("path", "")), dx, dy, str(data.get("name", "") or ""))
        elif action == "add_rect":
            try:
                x0, y0, x1, y1 = (float(data.get(k)) for k in ("x0", "y0", "x1", "y1"))
            except (TypeError, ValueError):
                raise ValueError("Type the area's corners in mm.")
            x0, x1 = sorted((x0, x1))
            y0, y1 = sorted((y0, y1))
            if x1 - x0 < 0.5 or y1 - y0 < 0.5:
                raise ValueError("The area must be at least 0.5 mm each way.")
            g = pm.Grid.rect(x0, y0, x1, y1)
            self.plates.add(str(data.get("name", "") or "Marked by hand")[:60], "", g.to_json(), [0, 0], "cut", "hand")
        elif action == "spot":
            folder, key = str(data.get("folder", "")), str(data.get("key", ""))
            lays = plates_mod.layouts(self._profiles(), self.layout_folders)
            if folder not in lays or key not in lays[folder]["spots"]:
                raise ValueError("Unknown spot.")
            if data.get("state") == "free":
                self.plates.remove_spot(folder, key)
            else:
                if self.plates.spot_cut(folder, key) is None:
                    files = lays[folder]["spots"][key]["files"]
                    path = files.get(str(data.get("design", ""))) or next(iter(files.values()))
                    self._add_file_cut(path, 0.0, 0.0, job_label(path))
        else:
            raise ValueError("Unknown plate action.")
        self.dirty()
        return self.plates_info()

    # ------------------------------------------------------------ recording what each job cuts
    def _job_spot(self, job: dict) -> None:
        """Called when a job's file is known: name, layout and spot for its cut area."""
        path = job.get("path") or ""
        prof = job.get("profile")
        d = prof.d if prof is not None else {}
        layout = self._layout_of(d) if d else ""
        part = (d.get("notes") or {}).get("part")
        job["area"] = {"name": job_label(path), "layout": layout,
                       "spot": plates_mod.spot_key(part["cx"], part["cy"]) if (layout and part) else ""}

    def _job_cutting(self, job: dict, st: dict) -> None:
        """The first time a job gets past the probe and its first bit change, add its area to the plate."""
        if job.get("cut_skip"):
            return
        if job.get("cut_id"):
            if job.get("cut_unnamed") and job.get("path"):
                self._job_named(job)
            return
        prof = job.get("profile")
        if prof is not None:
            first_line = prof.changes[0]["line"] if prof.changes else 0
            stops = [t for t in job.get("stops") or [] if t not in (0, None)]
            cutting = bool(stops) or st.get("line", 0) > first_line + 5
        else:
            cutting = st.get("elapsed", 0) > 180          # file not read yet: count it as cutting after 3 min
        if not cutting:
            return
        path = job.get("path") or ""
        if not path and time.time() - job.get("started", 0) < NAME_WAIT:
            return      # joined a running job (the Pi restarted, say): its file name usually comes within seconds
        wco = self._wco()
        if wco is None or self.anchor1 is None:
            job["cut_skip"] = True
            log.info("can't place this job's area on the plate (no work origin or Anchor 1)")
            return
        if prof is not None and not prof.d.get("cut"):
            job["cut_skip"] = True
            return
        if "area" not in job:
            self._job_spot(job)
        area = job["area"]
        shift = [round(wco[0] - self.anchor1[0], 3), round(wco[1] - self.anchor1[1], 3)]
        rec = self.plates.stopped(path, shift)
        if rec is not None:
            # the same file run again where it stopped: its area is already on the plate
            job["cut_id"] = rec["id"]
            log.info("this run of %s is where it stopped before (cut area %s)", rec.get("name"), rec["id"])
            self.dirty()
            return
        ls = self.last_start or {}
        by = "pi" if (ls.get("ok") and ls.get("path") == path and time.time() - ls.get("t", 0) < 900) else "app"
        spot = area["spot"] if (area["spot"] and abs(shift[0]) < 0.01 and abs(shift[1]) < 0.01) else ""
        job["cut_id"] = self.plates.add(area["name"] or "A job the Pi couldn't name", path,
                                        prof.d.get("cut") if prof is not None else None, shift, "started", by,
                                        area["layout"] if spot else "", spot)
        if not path:
            job["cut_unnamed"], job["cut_shift"] = True, shift
        self.dirty()

    def _job_named(self, job: dict) -> None:
        """The file name came after the job's area was added without it. If this file stopped here before,
        use that area and drop the unnamed one; else name the area."""
        job.pop("cut_unnamed", None)
        cid, path, shift = job["cut_id"], job["path"], job.get("cut_shift") or [0.0, 0.0]
        rec = self.plates.stopped(path, shift)
        if rec is not None and rec["id"] != cid:
            self.plates.remove(cid)
            job["cut_id"] = rec["id"]
            log.info("this run of %s is where it stopped before (cut area %s)", rec.get("name"), rec["id"])
        else:
            self._job_spot(job)
            area, prof = job["area"], job.get("profile")
            spot = area["spot"] if (area["spot"] and abs(shift[0]) < 0.01 and abs(shift[1]) < 0.01) else ""
            fields = {"name": area["name"], "file": path, "layout": area["layout"] if spot else "", "spot": spot}
            if prof is not None and prof.d.get("cut"):
                fields["shape"] = prof.d["cut"]
            self.plates.update(cid, **fields)
            log.info("cut area %s is %s", cid, area["name"])
        self.dirty()

    def _job_spot_end(self, job: dict, done: bool) -> None:
        cid = job.get("cut_id")
        if cid:
            self.plates.update(cid, state="cut" if done else "started")

    # ------------------------------------------------------------ sending to the machine
    def watch_line(self, line: str) -> None:
        for test, fut in list(self.line_waiters):
            if not fut.done():
                try:
                    if test(line):
                        fut.set_result(line)
                except Exception:
                    pass
        self.line_waiters = [(t, f) for t, f in self.line_waiters if not f.done()]

    def _expect(self, test):
        fut = asyncio.get_running_loop().create_future()
        self.line_waiters.append((test, fut))
        return fut

    async def _send_text(self, text: str) -> str:
        """One console command to the machine on the connection that is open. Returns 'own' or 'app'."""
        payload = text.encode("utf-8")
        sess = self.own
        if sess is not None and not self.clients:
            w = sess["writer"]
            w.write(payload + b"\n" if sess.get("text") else fr.build_frame(fr.CTRL_MULTI, payload))
            await w.drain()
            return "own"
        for sess in list(self.clients):
            w = sess.get("uwriter")
            if w is None:
                continue
            end = time.time() + 3.0
            while time.time() < end and (not sess["cp"].idle() or self.in_transfer(sess)):
                await asyncio.sleep(0.05)
            if not sess["cp"].idle() or self.in_transfer(sess):
                raise RuntimeError("Makera Studio is busy talking to the Z1. Try again in a moment.")
            w.write(fr.build_frame(fr.CTRL_MULTI, payload) if sess["cp"].frames else payload + b"\n")
            await w.drain()
            return "app"
        raise RuntimeError("The Pi isn't connected to the Z1 right now.")

    def _wco(self):
        f = (self.status or {}).get("fields") or {}
        m, w = f.get("MPos"), f.get("WPos")
        if not m or not w or len(m) < 2 or len(w) < 2 or None in (m[0], m[1], w[0], w[1]):
            return None
        return m[0] - w[0], m[1] - w[1]

    async def _origin_is(self, target, timeout: float) -> bool:
        end = time.time() + timeout
        while True:
            wco = self._wco()
            if wco and abs(wco[0] - target[0]) < 0.011 and abs(wco[1] - target[1]) < 0.011:
                return True
            if time.time() > end:
                return False
            await asyncio.sleep(0.2)

    async def start_job(self, path: str, plate_checked: bool, offset=None) -> dict:
        """Start a job the way Makera Studio's Run does, moved by offset (mm) from the file's own place.

        Raises ValueError with a reason for the screen."""
        if self.starting:
            raise ValueError("Already starting a job.")
        self.starting = True
        dx = dy = 0.0
        try:
            if not self.start_enabled():
                raise ValueError("Starting from this screen is turned off (settings page, Setup).")
            prof = self.store.exact(path)
            why = self._why_not(prof)
            if why:
                raise ValueError(why)
            if not plate_checked:
                raise ValueError("Check the plate first.")
            dx, dy = _offset(offset)
            run, _ = self._run_for(prof)
            layout = self._layout_of(prof)
            if layout and (dx or dy):
                raise ValueError("The parts of a plate layout start at their own spot.")
            why = self.machine_ready()
            if why:
                raise ValueError(why)
            unknown = self.plates.unknown()
            if unknown:
                raise ValueError(self._unknown_text(unknown))
            foot, probe, level, path_box = self._geometry(prof, run)
            res = self._checker(layout).check(foot, int(round(dx / pm.RES)), int(round(dy / pm.RES)),
                                              probe, level, path_box, measure=False)
            if not res["ok"]:
                raise ValueError(res["text"] + " Nothing was started.")
            cmds = start_mod.commands(prof, self.anchor1, run, (dx, dy))
            off = run.get("offset") or [0, 0]
            target = (self.anchor1[0] + off[0] + dx, self.anchor1[1] + off[1] + dy)
            log.info("starting %s from the small screen (moved X%s Y%s)", path, dx, dy)
            self._yield_own()              # a file read for its picture stops first: nothing else may be mid-transfer
            async with self._own_lock():
                pass
            try:
                if not await self._origin_is(target, 0):
                    await self._send_text(cmds[0])
                    if not await self._origin_is(target, 4.0):
                        raise ValueError("The Z1 didn't take the work origin. Nothing was started.")
                buffered = self._expect(lambda l: l.startswith("Command buffered") and "M495" in l)
                await self._send_text(cmds[1])
                try:
                    await asyncio.wait_for(buffered, 4.0)
                except asyncio.TimeoutError:
                    raise ValueError("The Z1 didn't confirm the run settings. Nothing was started; "
                                     "start this job from Makera Studio.")
                answer = self._expect(lambda l: "File size" in l or "not found" in l.lower()
                                      or "abort print first" in l or l.lower().startswith("error"))
                self.play_path, self.play_path_t = path, time.time()
                await self._send_text(cmds[2])
                try:
                    line = await asyncio.wait_for(answer, 8.0)
                except asyncio.TimeoutError:
                    line = ""
                if "File size" not in line and not (self.status or {}).get("playing"):
                    raise ValueError("The Z1 didn't start the file%s. Its run settings may still be waiting on the "
                                     "Z1: switch the Z1 off and on before starting this or another job."
                                     % ((" (" + line.strip() + ")") if line else ""))
            except RuntimeError as exc:
                raise ValueError(str(exc))
            self.last_start = {"path": path, "t": time.time(), "ok": True, "commands": cmds, "offset": [dx, dy]}
            log.info("started %s", path)
            self.dirty()
            return {"ok": True, "path": path, "offset": [dx, dy]}
        except ValueError as exc:
            self.last_start = {"path": path, "t": time.time(), "ok": False, "error": str(exc), "offset": [dx, dy]}
            log.info("did not start %s: %s", path, exc)
            raise
        finally:
            self.starting = False

    # ------------------------------------------------------------ setup section of the settings page
    def setup_info(self) -> dict:
        mc = self.machine_cfg or {}
        a = self.cfg.get("anchor1")
        run = dict(start_mod.DEFAULT_RUN, **(self.cfg.get("run_defaults") or {}))
        fresh = self.status is not None and time.time() - self.status_t < 10
        return {
            "version": __version__,
            "machine": {"name": self.m_name, "ip": self.m_ip, "connected": fresh},
            "anchor": {"mode": "auto" if self.anchor_auto else "manual", "value": self.anchor1,
                       "source": self.anchor_source, "manual": a if isinstance(a, (list, tuple)) else None,
                       "read": mc.get("anchor1"), "read_t": mc.get("t"), "read_error": self._cfg_error,
                       "tries": self._cfg_tries, "reading_allowed": bool(self.cfg.get("read_machine_config", True))},
            "app_origin": self.app_origin,
            "start_from_screen": self.start_enabled(),
            "start_ok": self.prefs.get("start_ok"),
            "run_defaults": {"probe": run.get("probe"), "margin": bool(run.get("margin")), "level": run.get("level")},
            "plate_layouts": sorted(self.layout_folders),
            "plate": dict(self.plates.plate), "plate_set": self.plates.is_set,
            "pin": bool(self.settings_pin()),
            "display": str(self.cfg.get("display", "tft")),
            "touch_calibrated": self.touch_calibrated(),
            "bits_saved": self.tools_saved,
            "settings_url": self.settings_url(),
            "notify": self.notifier.info(self.cfg),
        }

    def _save_prefs(self, **changes) -> None:
        new = dict(self.prefs, **changes)
        prefs_mod.save(self.state_dir, new)
        self.prefs = new

    def set_setup(self, data: dict) -> dict:
        """One change from the setup section. Raises ValueError with a reason for the page."""
        if "anchor1" in data:
            a = prefs_mod.clean_anchor(data["anchor1"])
            self._save_prefs(anchor1=a)
            self.cfg["anchor1"] = a
            if a == "auto" and not (self.machine_cfg or {}).get("anchor1"):
                self.machine_cfg, self._cfg_tries, self._cfg_next = {}, 0, 0.0     # read config.txt again soon
            self._set_anchor(a)
            log.info("Anchor 1: %s", "read from the machine" if a == "auto" else "X%s Y%s" % tuple(a))
        elif data.get("read_anchor"):
            self.machine_cfg, self._cfg_tries, self._cfg_next, self._cfg_error = {}, 0, 0.0, ""
            if self.anchor_auto:
                self._set_anchor("auto")
        elif "start_from_screen" in data:
            on = data["start_from_screen"] is True
            if on:
                if data.get("accept") is not True:
                    raise ValueError("Tick the box first.")
                if not self.plates.is_set:
                    raise ValueError("Set the plate size first.")
            self._save_prefs(start_from_screen=on, start_ok=time.time() if on else None)
            self.cfg["start_from_screen"] = on
            log.info("starting jobs from the small screen: %s", "on" if on else "off")
        elif "run_defaults" in data:
            run = prefs_mod.clean_run(data["run_defaults"])
            self._save_prefs(run_defaults=run)
            self.cfg["run_defaults"] = run
        elif "plate_layouts" in data:
            folders = prefs_mod.clean_folders(data["plate_layouts"])
            self._save_prefs(plate_layouts=folders)
            self.cfg["plate_layouts"] = folders
            self.layout_folders = set(folders)
        elif "pin" in data:
            pin = str(data.get("pin") or "").strip()
            self._save_prefs(settings_pin=prefs_mod.pin_hash(pin) if pin else "")
            log.info("settings page PIN %s", "set" if pin else "removed")
        elif "notify" in data:
            conf = notify_mod.clean(data["notify"], notify_mod.settings(self.cfg))
            self._save_prefs(notify=conf)
            self.cfg["notify"] = conf
            log.info("phone notifications: %s", ("topic " + conf["topic"]) if conf["topic"] else "off")
        else:
            raise ValueError("Unknown setup change.")
        self.dirty()
        return self.setup_info()

    # ------------------------------------------------------------ touch screen calibration
    # Each screen's touch layer reads differently (the PiTFT's from 0 to 4095, the 3.5inch DPI screen's in a range of
    # its own), so a calibration is kept with the framebuffer size of the screen it was made on, and only that screen
    # gets it. A screen with none saved for it gets the built-in one for its size; with none of those either, the
    # dots come up at the first touch.
    def _load_touch(self):
        p = os.path.join(self.state_dir, TOUCH_FILE) if self.state_dir else ""
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            return d if isinstance(d, dict) and isinstance(d.get("ax"), list) else None
        except Exception:
            return None

    def touch_cal(self):
        """The small screen's calibration and where it comes from: ("saved", cal) if it was made on the screen the
        small screen program last named (on any screen until it has named one; one saved without its screen was made
        on the PiTFT, the only touch screen before), else ("built-in", cal) for that size, else ("", None)."""
        if self.touch and (self.screen_size is None or (self.touch.get("screen") or PITFT_SCREEN) == self.screen_size):
            return "saved", self.touch
        cal = BUILT_IN_TOUCH.get(tuple(self.screen_size or ()))
        return ("built-in", cal) if cal else ("", None)

    def touch_calibrated(self) -> bool:
        """True if the small screen has a calibration: one saved for it, or the built-in one for its size."""
        return self.touch_cal()[1] is not None

    def touch_info(self, screen: str = "") -> dict:
        """The calibration for the small screen program, which names its framebuffer when it asks (640x480)."""
        size = _screen_size(screen)
        if size and size != self.screen_size:
            self.screen_size = size
            log.info("small screen %dx%d, touch calibration: %s", size[0], size[1],
                     self.touch_cal()[0] or "none yet (the dots come up at the first touch)")
        return {"cal": self.touch_cal()[1], "request": self.touch_request}

    def set_touch(self, data: dict) -> dict:
        if data.get("action") == "calibrate":
            self.touch_request += 1
            self.dirty()
            return self.touch_info()
        cal = data.get("cal")
        if not (isinstance(cal, dict) and isinstance(cal.get("ax"), list) and isinstance(cal.get("ay"), list)
                and len(cal["ax"]) == 3 and len(cal["ay"]) == 3
                and all(isinstance(v, (int, float)) for v in cal["ax"] + cal["ay"])):
            raise ValueError("Not a touch calibration.")
        cal = {"ax": [float(v) for v in cal["ax"]], "ay": [float(v) for v in cal["ay"]],
               "flip": bool(cal.get("flip")), "screen": _screen_size(cal.get("screen")) or self.screen_size,
               "t": time.time()}
        p = os.path.join(self.state_dir, TOUCH_FILE)
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(cal, fh)
        os.replace(p + ".tmp", p)
        self.touch = cal
        log.info("touch screen calibrated")
        return self.touch_info()


def _screen_size(v):
    """A framebuffer's size [width, height] from "640x480" or [640, 480], or None."""
    try:
        w, h = (int(n) for n in (v.split("x") if isinstance(v, str) else v))
    except (TypeError, ValueError):
        return None
    return [w, h] if w > 0 and h > 0 else None


def _offset(v):
    """A move on the plate [x, y] in mm, in whole 0.5 mm steps."""
    if v in (None, "", [], (0, 0)):
        return 0.0, 0.0
    try:
        x, y = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError):
        raise ValueError("The move on the plate must be two numbers.")
    if abs(x) > 1000 or abs(y) > 1000:
        raise ValueError("The move on the plate is too large.")
    if abs(x / pm.RES - round(x / pm.RES)) > 1e-6 or abs(y / pm.RES - round(y / pm.RES)) > 1e-6:
        raise ValueError("The move on the plate goes in 0.5 mm steps.")
    return round(x, 3), round(y, 3)
