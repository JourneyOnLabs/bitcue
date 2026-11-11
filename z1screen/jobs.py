"""Keeps the job profiles the screen knows about.

Each profile can have a picture of the finished part next to it, <md5>.png (preview.py), drawn after the
profile so the timing is never kept waiting for it.

Profiles come from two places:
  * library/   profiles shipped with the kit (empty in the release; a kit made
               for one machine can carry the profiles of its job files, made
               with tools/build_library.py)
  * <state>/profiles/  profiles made from files copied as they passed
                       through the relay (uploads, and the downloads Makera
                       Studio does when you select a file), and from files the
                       Pi read from the Z1 itself (fetch.py)
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading

from . import gcode, preview, quicklz

log = logging.getLogger("z1screen.jobs")
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREVIEW_TIMEOUT_S = 900
_preview_lock = threading.Lock()       # one picture at a time


def _key(path: str) -> str:
    return path.replace("\x01", " ").strip()


class JobStore:
    def __init__(self, state_dir: str, library_dirs=()) -> None:
        self.state_dir = state_dir
        self.library_dirs = [d for d in library_dirs if d]
        self.prof_dir = os.path.join(state_dir, "profiles")
        os.makedirs(self.prof_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.by_path = {}   # full path on the machine -> profile dict
        self.by_name = {}   # base name -> list of profile dicts (newest last)
        self.pending = set()
        self.preview_failed = set()   # md5s whose picture couldn't be drawn (not tried again until a restart)
        self.listeners = []
        for d in library_dirs:
            self._load_dir(d, source="library")
        self._load_dir(self.prof_dir, source="captured")

    def _load_dir(self, d: str, source: str) -> None:
        if not d or not os.path.isdir(d):
            return
        files = sorted(glob.glob(os.path.join(d, "*.json")), key=os.path.getmtime)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    prof = json.load(fh)
                if prof.get("version") != gcode.PROFILE_VERSION:
                    continue
                if prof.get("tool_specs"):   # bit names made again from the tool lines (so they follow describe_tool)
                    prof["tools"] = {k: gcode.describe_tool(v) for k, v in prof["tool_specs"].items()}
                prof["source"] = source
                self._index(prof)
            except Exception:
                log.warning("could not read profile %s", f)
        log.info("loaded %d profiles from %s", len(files), d)

    def _index(self, prof: dict) -> None:
        with self.lock:
            if prof.get("path"):
                self.by_path[_key(prof["path"])] = prof
            name = prof.get("name") or os.path.basename(prof.get("path", ""))
            if name:
                lst = self.by_name.setdefault(name, [])
                lst[:] = [p for p in lst if p.get("md5") != prof.get("md5")]
                lst.append(prof)

    def count(self) -> int:
        with self.lock:
            return sum(len(v) for v in self.by_name.values())

    def _all(self) -> list:
        with self.lock:
            seen = {}
            for lst in self.by_name.values():
                for p in lst:
                    seen[p.get("md5") or id(p)] = p
            for p in self.by_path.values():
                seen[p.get("md5") or id(p)] = p
        return list(seen.values())

    def tool_names(self) -> dict:
        """For each bit number: how many known job files use it, and what the files call it."""
        out = {}
        for p in self._all():
            names = p.get("tools") or {}
            for t in p.get("used_tools") or []:
                e = out.setdefault(str(t), {"files": 0, "names": {}})
                e["files"] += 1
                nm = names.get(str(t))
                if nm:
                    e["names"][nm] = e["names"].get(nm, 0) + 1
        return out

    def jobs(self) -> list:
        """Known job files that have tool changes, for the demo's job list."""
        items = {}
        for p in self._all():
            if p.get("changes") and p.get("path"):
                items[_key(p["path"])] = {
                    "path": p["path"],
                    "name": os.path.basename(p["path"]),
                    "total": p.get("total", 0),
                    "changes": len(p["changes"]),
                    "tools": p.get("used_tools", []),
                }
        return sorted(items.values(), key=lambda j: (j["name"].lower(), j["path"]))

    def exact(self, path: str):
        """The profile for exactly this path on the machine, or None (no match by file name alone)."""
        with self.lock:
            return self.by_path.get(_key(path or ""))

    def find(self, path: str = "", size=None):
        """Best profile for a file on the machine, or None."""
        path = _key(path or "")
        name = os.path.basename(path)
        with self.lock:
            cands = []
            p = self.by_path.get(path)
            if p:
                cands.append(p)
            cands.extend(reversed(self.by_name.get(name, [])))
        for p in cands:
            if size is None or int(p.get("size", -1)) == int(size):
                return p
        return None

    def is_pending(self, path: str) -> bool:
        with self.lock:
            return _key(path) in self.pending

    # ------------------------------------------------------------ pictures of the parts
    def preview_file(self, md5: str) -> str:
        """The picture of a profile's part on disk, or "" if there is none (yet)."""
        if not md5 or not all(c in "0123456789abcdef" for c in md5):
            return ""
        for d in [self.prof_dir] + self.library_dirs:
            f = os.path.join(d, md5 + ".png")
            if os.path.isfile(f):
                return f
        return ""

    def needs_preview(self, prof) -> bool:
        md5 = (prof or {}).get("md5") or ""
        return bool(md5) and preview.available() and md5 not in self.preview_failed and not self.preview_file(md5)

    def make_preview(self, md5: str, data: bytes) -> bool:
        """Draws and keeps the picture of a job file's part. It is drawn by a separate program at low priority,
        one at a time, so the relay and the screen never wait for it (it takes a minute on a Pi Zero)."""
        if not md5 or self.preview_file(md5) or not preview.available():
            return bool(self.preview_file(md5))
        out = os.path.join(self.prof_dir, md5 + ".png")
        src = out + ".nc.tmp"
        with _preview_lock:
            try:
                if quicklz.looks_compressed(data):
                    data = quicklz.decompress_container(data)
                with open(src, "wb") as fh:
                    fh.write(data)
                cmd = [sys.executable, "-B", "-m", "z1screen.preview", src, out + ".tmp"]
                if hasattr(os, "nice"):
                    cmd = ["nice", "-n", "10"] + cmd
                r = subprocess.run(cmd, cwd=PACKAGE_PARENT, timeout=PREVIEW_TIMEOUT_S, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONPATH=PACKAGE_PARENT))
                ok = r.returncode == 0 and os.path.isfile(out + ".tmp")
                if not ok and r.returncode != 3:
                    log.warning("could not draw the part of %s: %s", md5, r.stdout.decode("utf-8", "replace")[-300:])
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                log.warning("could not draw the part of %s (%s)", md5, exc)
                ok = False
            finally:
                try:
                    os.remove(src)
                except OSError:
                    pass
        if not ok:
            self.preview_failed.add(md5)
            return False
        os.replace(out + ".tmp", out)
        log.info("picture of the part ready: %s", md5)
        return True

    def add_file_async(self, kind: str, path: str, data: bytes) -> None:
        """Called by the capture; works out the profile on a background thread."""
        path = _key(path)
        low = path.lower()
        if not (low.endswith(".nc") or low.endswith(".gcode") or low.endswith(".ngc")
                or low.endswith(".tap") or low.endswith(".cnc") or low.endswith(".txt")):
            return
        with self.lock:
            self.pending.add(path)
        threading.Thread(target=self._add_file, args=(path, data), daemon=True).start()

    def _add_file(self, path: str, data: bytes) -> None:
        prof = None
        try:
            prof = self.add_file(path, data, notify=False)
        except Exception:
            log.exception("could not read the job file %s", path)
        finally:
            with self.lock:
                self.pending.discard(path)
            self._notify(path)
        if prof is not None and self.needs_preview(prof):
            if self.make_preview(prof["md5"], data):
                self._notify(path)

    def _notify(self, path: str) -> None:
        for cb in list(self.listeners):
            try:
                cb(path)
            except Exception:
                pass

    def add_file(self, path: str, data: bytes, md5: str = "", source: str = "captured", notify: bool = True) -> dict:
        """Profile a whole job file now (on the caller's thread) and keep the profile.

        md5: the checksum the machine gave for the file, if any, checked after unpacking."""
        path = _key(path)
        if quicklz.looks_compressed(data):
            data = quicklz.decompress_container(data)
            if md5 and hashlib.md5(data).hexdigest() != md5:
                raise ValueError("The file didn't arrive whole (its checksum doesn't match). Try again.")
        prof = gcode.analyze(data, path)
        prof["source"] = source
        out = os.path.join(self.prof_dir, prof["md5"] + ".json")
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(prof, fh)
        os.replace(tmp, out)
        self._index(prof)
        log.info("profile ready: %s (%d lines, %.0f s)", path, prof["lines"], prof["total"])
        if notify:
            self._notify(path)
        return prof

    def add_local_file(self, file_path: str, remote_path: str = "") -> dict:
        """Used by the library builder: profile a file on disk."""
        with open(file_path, "rb") as fh:
            data = fh.read()
        prof = gcode.analyze(data, remote_path or os.path.basename(file_path))
        if not remote_path:
            prof["path"] = ""
        return prof
