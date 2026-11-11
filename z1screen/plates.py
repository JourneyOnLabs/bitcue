"""The plate on the Z1 and the areas already cut on it.

The Pi keeps one record: the plate that sits in the Anchor 1 corner now, and
every area cut on it. A job's area is its footprint (platemap.py) placed where
the job's work origin was. The Pi adds an area when a job gets past its first
cut, whether the job was started on the Pi's screen or in Makera Studio through
the Pi, and marks it cut when the job finishes. Areas can also be added and
removed by hand on the settings page. NEW PLATE clears them, and may give the
new plate its own size and thickness. The record it replaces is kept (when
anything was cut on it): LAST PLATE brings it back, for when the old plate
turns out to be still on the Z1, and keeps the record it replaces in turn.

A layout (a folder of job files for the spots of one plate, each file stating
its part in a header note; see layouts() below) is planned so its parts fit
together: for example, the cut-out of one part may run through the gaps between
its neighbours. So areas cut from a layout's files don't block the other spots
of the same layout; they do block any other job.

A job run again from the start in the same place where it stopped (same file,
same work origin) is recorded on its stopped area, which becomes "cut" when
that run finishes.

Kept in plates.json in the state folder:
  {"version": 2, "seed": "<the kit's starting plate this came from>" or "",
   "plate": {"w": 200, "h": 150, "t": 6, "material": "Plywood", "since": <time>, "set": true},
   "cuts": [{"id": 3, "name": "Bracket", "file": "/sd/gcodes/...nc", "state": "cut" | "started",
             "by": "pi" | "app" | "hand" | "kit", "t": <time>, "shift": [x, y],
             "layout": "<folder of a layout>" or "", "spot": "<spot key>" or "",
             "shape": <footprint grid> or null}],
   "next_id": 4,
   "previous": {"plate": {...}, "cuts": [...]}}    (optional: the record before the last NEW PLATE)
shift is where the file's own origin was on the plate, in mm from the Anchor 1
corner. A cut with no shape is a job the Pi hadn't read; it reads the file
after the job, and won't start another job on the plate until then. "seed" is
emptied at the first change on the Pi; until then a newer kit's version of the
same starting plate replaces the record (a kit can ship a starting record as
library/seed/plate.json). "set" is false while the plate's size is only the
placeholder (no size in the settings yet): jobs can't be placed until the size
is set on the settings page. A record without "set" counts as set.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time

from . import platemap as pm
from . import start as st

log = logging.getLogger("z1screen.plates")
FILE_NAME = "plates.json"
PLACEHOLDER_PLATE = {"w": 200.0, "h": 200.0, "t": 3.0, "material": ""}   # until the plate size is set


def spot_key(cx: float, cy: float) -> str:
    return "%s,%s" % (st.fmt(round(cx, 1)), st.fmt(round(cy, 1)))


def folder_of(path: str) -> str:
    return os.path.dirname(path or "")


def layouts(profiles, folders=None) -> dict:
    """{folder: {"plate", "spots": {key: {...}}, "designs"}} for the layout folders (all if folders is None)."""
    out = {}
    for p in profiles:
        notes = p.get("notes") or {}
        part = notes.get("part")
        path = p.get("path") or ""
        if not part or not path:
            continue
        folder = folder_of(path)
        if folders is not None and folder not in folders:
            continue
        lay = out.setdefault(folder, {"plate": None, "spots": {}, "designs": {}})
        stock = p.get("stock") or {}
        dims = notes.get("plate_note_dims") or [stock.get("length"), stock.get("width"), stock.get("height")]
        if lay["plate"] is None and dims and dims[0] and dims[1]:
            lay["plate"] = {"w": float(dims[0]), "h": float(dims[1]), "t": float(dims[2] or 0),
                            "material": notes.get("plate_note_material") or p.get("material") or "",
                            "note": notes.get("plate_note") or ""}
        key = spot_key(part["cx"], part["cy"])
        box = p.get("box") or [part["cx"] - 30, part["cy"] - 30, part["cx"] + 30, part["cy"] + 30]
        spot = lay["spots"].setdefault(key, {"key": key, "n": part["n"], "of": part["of"], "cx": part["cx"],
                                             "cy": part["cy"], "d": 0.0, "files": {}})
        spot["d"] = max(spot["d"], box[2] - box[0], box[3] - box[1])
        dk = st.design_key(path)
        spot["files"][dk] = path
        lay["designs"][dk] = st.design_name(dk)
    return out


def where(cx: float, cy: float, w: float, h: float) -> str:
    """'front left', 'middle', 'back right' ... for a place on a plate (Y grows toward the back)."""
    col = "left" if cx < w / 3 else ("right" if cx > 2 * w / 3 else "")
    row = "front" if cy < h / 3 else ("back" if cy > 2 * h / 3 else "")
    if not col and not row:
        return "middle"
    return " ".join(x for x in (row, col) if x) or "middle"


def _num(v, lo, hi, what) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError("Type a number for the %s." % what)
    if not lo <= f <= hi:
        raise ValueError("The %s must be between %s and %s mm." % (what, st.fmt(lo), st.fmt(hi)))
    return round(f, 2)


class Plates:
    def __init__(self, state_dir: str, seed_path: str = "", default_plate=None) -> None:
        self.path = os.path.join(state_dir, FILE_NAME) if state_dir else ""
        given = isinstance(default_plate, dict) and bool(default_plate)
        self.default_plate = dict(PLACEHOLDER_PLATE, **(default_plate if given else {}))
        self.default_plate["set"] = given
        self.data = None
        self._occ = {}
        seed = self._read(seed_path, "the kit's starting plate") if seed_path and os.path.isfile(seed_path) else None
        if self.path and os.path.isfile(self.path):
            self.data = self._read(self.path, "the plate record")
        if self.data is not None and seed is not None and self._kit_only(self.data, seed):
            log.info("the plate record is still an older kit's starting plate; using this kit's version of it")
            self.data = None
        if self.data is None and seed is not None:
            self.data = seed
            self.data["seed"] = seed.get("seed") or "kit"
            self.data["plate"]["since"] = time.time()
            log.info("plate record started from %s", seed_path)
            self._save(changed=False)
        if self.data is None:
            self.data = {"version": 2, "plate": dict(self.default_plate, since=time.time()), "cuts": [], "next_id": 1}

    @staticmethod
    def _kit_only(data, seed) -> bool:
        """True if this record came from an earlier kit's starting plate and nothing on it has changed since,
        so a newer kit's version of the same plate can replace it."""
        mark = data.get("seed")
        if mark == "" or mark == (seed.get("seed") or "kit"):
            return False                        # changed on the Pi, or this kit's own version
        if mark is None:                        # (kits before the "seed" mark)
            ids = set(c.get("id") for c in seed.get("cuts") or [])
            sp, dp = seed.get("plate") or {}, data.get("plate") or {}
            return (bool(data["cuts"]) and all(c.get("by") == "kit" and c.get("id") in ids for c in data["cuts"])
                    and all(abs(float(sp.get(k, 0)) - float(dp.get(k, 0))) < 0.01 for k in ("w", "h", "t")))
        return True                             # an earlier kit's version, never changed

    @staticmethod
    def _read(path: str, what: str):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict) and d.get("version") == 2 and isinstance(d.get("plate"), dict) \
                    and isinstance(d.get("cuts"), list):
                prev = d.get("previous")
                if prev is not None and not (isinstance(prev, dict) and isinstance(prev.get("plate"), dict)
                                             and isinstance(prev.get("cuts"), list)):
                    d.pop("previous")
                ids = [int(c.get("id", 0)) for c in d["cuts"] + (d.get("previous") or {}).get("cuts", [])]
                d.setdefault("next_id", 1 + max(ids or [0]))
                return d
            log.warning("ignored %s (not a plate record of this version)", path)
        except Exception as exc:
            log.warning("ignored %s, %s (%s)", what, path, exc)
        return None

    def _save(self, changed: bool = True) -> None:
        self._occ = {}
        if changed:
            self.data["seed"] = ""                  # the Pi's own record now: a newer kit won't replace it
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- reading
    @property
    def plate(self) -> dict:
        return self.data["plate"]

    @property
    def is_set(self) -> bool:
        """False while the plate size is only the placeholder."""
        return self.plate.get("set", True) is not False

    @property
    def cuts(self) -> list:
        return self.data["cuts"]

    def get(self, cid):
        return next((c for c in self.cuts if c.get("id") == cid), None)

    @staticmethod
    def area(cut) -> pm.Grid:
        """A cut's cells on the plate."""
        g = pm.Grid.from_json(cut.get("shape")) if cut.get("shape") else pm.Grid()
        if not g:
            return g
        sx, sy = cut.get("shift") or [0.0, 0.0]
        return pm.stamp(g, float(sx), float(sy))

    def occupancy(self, except_layout: str = "") -> pm.Grid:
        """Every cut cell on the plate (leaving out the cuts of one layout, for that layout's spots)."""
        key = except_layout or ""
        if key not in self._occ:
            g = pm.Grid()
            for c in self.cuts:
                if key and c.get("layout") == key:
                    continue
                g = g.union(self.area(c))
            self._occ[key] = g.trimmed()
        return self._occ[key]

    def stopped(self, file: str, shift):
        """The latest area left by a run of this file that stopped part way at this place, or None."""
        for c in reversed(self.cuts):
            if c.get("state") != "started" or not file or c.get("file") != file:
                continue
            s = c.get("shift") or [0.0, 0.0]
            if abs(s[0] - shift[0]) < 0.01 and abs(s[1] - shift[1]) < 0.01:
                return c
        return None

    def unknown(self) -> list:
        return [c for c in self.cuts if not c.get("shape")]

    def spot_cut(self, layout: str, key: str):
        return next((c for c in self.cuts if c.get("layout") == layout and c.get("spot") == key), None)

    def info(self) -> dict:
        out = []
        for c in self.cuts:
            g = self.area(c)
            out.append({"id": c["id"], "name": c.get("name") or os.path.basename(c.get("file") or "") or "Cut area",
                        "file": c.get("file") or "", "state": c.get("state", "cut"), "by": c.get("by", ""),
                        "t": c.get("t"), "spot": c.get("spot") or "", "layout": c.get("layout") or "",
                        "shift": c.get("shift") or [0, 0], "known": bool(g), "box": g.bbox_mm(),
                        "area": g.to_json() if g else None})
        return {"plate": copy.deepcopy(self.plate), "cuts": out, "previous": self.previous_info()}

    def previous_info(self):
        """The record LAST PLATE would bring back: its size and what was cut on it, or None."""
        prev = self.data.get("previous")
        if not prev:
            return None
        p = prev["plate"]
        return {"w": p.get("w"), "h": p.get("h"), "t": p.get("t"), "material": p.get("material") or "",
                "since": p.get("since"), "cuts": len(prev["cuts"]),
                "names": [c.get("name") or os.path.basename(c.get("file") or "") or "Cut area" for c in prev["cuts"]]}

    # ---------------------------------------------------------------- changing
    def add(self, name: str, file: str, shape, shift, state: str = "cut", by: str = "hand",
            layout: str = "", spot: str = "") -> int:
        cid = int(self.data.get("next_id", 1))
        self.data["next_id"] = cid + 1
        self.cuts.append({"id": cid, "name": name, "file": file or "", "state": state, "by": by, "t": time.time(),
                          "shift": [round(float(shift[0]), 3), round(float(shift[1]), 3)],
                          "layout": layout or "", "spot": spot or "", "shape": shape})
        self._save()
        log.info("plate: added cut area %d, %s (%s, by %s)", cid, name, state, by)
        return cid

    def update(self, cid: int, **fields) -> None:
        c = self.get(cid)
        if c is None:
            return
        if c.get("state") == "cut" and fields.get("state") == "started":
            fields.pop("state")                    # a finished part stays finished
        c.update(fields)
        c["t"] = time.time()
        self._save()

    def remove(self, cid: int) -> bool:
        n = len(self.cuts)
        self.data["cuts"] = [c for c in self.cuts if c.get("id") != cid]
        if len(self.cuts) != n:
            self._save()
            log.info("plate: removed cut area %s", cid)
            return True
        return False

    def remove_spot(self, layout: str, key: str) -> int:
        n = len(self.cuts)
        self.data["cuts"] = [c for c in self.cuts if not (c.get("layout") == layout and c.get("spot") == key)]
        if len(self.cuts) != n:
            self._save()
        return n - len(self.cuts)

    def new_plate(self, size=None) -> None:
        """A bare plate, with the same size or a new one. The record it replaces is kept for restore_previous() if
        anything was cut on it; a bare record isn't worth keeping, so the one kept before stays."""
        plate = dict(self.plate)
        if size:
            plate.update(size)
            plate["set"] = True
        plate["since"] = time.time()
        if self.cuts:
            self.data["previous"] = {"plate": copy.deepcopy(self.plate), "cuts": self.cuts}
        self.data.update({"plate": plate, "cuts": []})
        self._save()
        log.info("plate: new plate, %s x %s x %s mm, nothing cut", plate["w"], plate["h"], plate["t"])

    def restore_previous(self) -> bool:
        """LAST PLATE: the record kept by NEW PLATE comes back with its cut areas, and the one it replaces is kept in
        its place, so doing it again undoes it. False if there is none."""
        prev = self.data.get("previous")
        if not prev:
            return False
        self.data.update({"plate": prev["plate"], "cuts": prev["cuts"],
                          "previous": {"plate": self.plate, "cuts": self.cuts}})
        self._save()
        log.info("plate: back to the last plate, %s x %s x %s mm, %d cut areas", self.plate.get("w"),
                 self.plate.get("h"), self.plate.get("t"), len(self.cuts))
        return True

    def set_size(self, data: dict) -> None:
        self.plate.update({"w": _num(data.get("w"), 10, 400, "width"), "h": _num(data.get("h"), 10, 400, "depth"),
                           "t": _num(data.get("t"), 0.1, 100, "thickness"),
                           "material": str(data.get("material") or "").strip()[:40], "set": True})
        self._save()
