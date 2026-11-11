"""How long each bit has cut, so worn bits can be replaced before they fail.

A job file names its bits by kind (the ;@MKR|TOOL lines: "1.5 × 6 mm flat end mill"). Each kind can have several
real bits, labelled A, B, C, ... (mark the holder's holes the same way). The first bit of a kind is made the first
time a job uses that kind, so counting starts with no setup.

At each bit-change stop the screen suggests the least-used bit of that kind ("Use bit B"); a tap on that line on
the Pi's screen, or the settings page, picks another. The time counted is the Z1's own job clock while the bit is in
the spindle, from the stop that put it in to the next stop or the end of the job: the clock stops at bit changes
and pauses, and a job stopped part way counts what it cut. It is kept per material (the plate on record, else the
job file's), as hours in bronze and hours in wood wear a bit differently.

An expected life can be set for a kind and a material (hours); until it is, nothing warns. With one, the stop
screen says how much is used (amber past 80 %, red past 100 %), and a job that would take a bit past its life is
announced before it starts cutting.

Kept in <state>/bitlife.json:
  {"version": 1,
   "kinds": {key: {"label": "1.5 × 6 mm flat end mill", "limits": {"bronze": 3.0}}},
   "bits":  {id: {"kind": key, "label": "A", "added": t, "retired": t | None, "reason": "", "note": "",
                  "secs": {"bronze": 7800.0}, "jobs": 6, "last": t, "last_job": file}},
   "slots": {"2": {"kind": key, "bit": id}},       the bit loaded for each bit number at its last stop
   "events": [{"t", "bit", "kind", "slot", "job", "material", "secs"}]}
"""

from __future__ import annotations

import json
import logging
import os
import re
import string
import threading
import time

log = logging.getLogger("z1screen.bitlife")

FILE_NAME = "bitlife.json"
REASONS = {"worn": "Worn out", "broken_setup": "Broke: setup problem (probe, Z, collar)",
           "broken_cutting": "Broke while cutting normally", "other": "Other"}
MAX_EVENTS = 3000
NEAR = 0.8                       # the share of the expected life where the warning starts
NOT_BITS = (0, 8888, 9999)       # the probe, the laser, the 3D probe


def kind_key(label: str) -> str:
    return " ".join(re.sub(r"[^\w.×x]+", " ", str(label or "").lower()).split())


def material_key(m: str) -> str:
    m = " ".join(str(m or "").lower().split())
    return m or "unknown"


def kind_of(tool: dict) -> str:
    """The kind of bit a tool entry of the screen data names (a bit number the file doesn't describe: "T3 bit")."""
    return tool.get("label") or "T%s bit" % tool.get("tool")


def hm(secs) -> str:
    """45 min, 2 h 10, 3 h"""
    m = int(round(float(secs or 0) / 60.0))
    if m < 60:
        return "%d min" % m
    return "%d h" % (m // 60) if m % 60 == 0 else "%d h %02d" % (m // 60, m % 60)


class BitLife:
    def __init__(self, state_dir: str) -> None:
        self.state_dir = state_dir
        self.lock = threading.Lock()
        self.d = {"version": 1, "kinds": {}, "bits": {}, "slots": {}, "events": []}
        p = self._path()
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                if isinstance(d, dict) and d.get("version") == 1:
                    for k in ("kinds", "bits", "slots", "events"):
                        if isinstance(d.get(k), type(self.d[k])):
                            self.d[k] = d[k]
            except Exception as exc:
                bad = "%s.bad-%d" % (p, time.time())
                log.warning("could not read %s (%s); kept it as %s and started a new one", p, exc, bad)
                try:
                    os.replace(p, bad)
                except OSError:
                    pass

    def _path(self) -> str:
        return os.path.join(self.state_dir, FILE_NAME) if self.state_dir else ""

    def save(self) -> None:
        p = self._path()
        if not p:
            return
        with open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(self.d, fh, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    # ------------------------------------------------------------ kinds and bits
    def ensure_kind(self, label: str) -> str:
        """The key of a kind of bit, made (with its first bit, A) the first time a job uses it."""
        key = kind_key(label)
        if not key:
            return ""
        with self.lock:
            if key not in self.d["kinds"]:
                self.d["kinds"][key] = {"label": str(label).strip(), "limits": {}}
            if not self._active(key):
                self._add_bit(key)
                self.save()
        return key

    def _active(self, key: str) -> list:
        return sorted((dict(b, id=i) for i, b in self.d["bits"].items() if b["kind"] == key and not b.get("retired")),
                      key=lambda b: b["label"])

    def _add_bit(self, key: str, label: str = "") -> str:
        used = {b["label"] for b in self.d["bits"].values() if b["kind"] == key and not b.get("retired")}
        if not label:
            label = next((c for c in string.ascii_uppercase if c not in used),
                         "%d" % (len([b for b in self.d["bits"].values() if b["kind"] == key]) + 1))
        if label in used:
            raise ValueError("There is already a bit %s of this kind." % label)
        n = 1
        while "b%d" % n in self.d["bits"]:
            n += 1
        bid = "b%d" % n
        self.d["bits"][bid] = {"kind": key, "label": label[:6], "added": time.time(), "retired": None, "reason": "",
                               "note": "", "secs": {}, "jobs": 0, "last": None, "last_job": None}
        return bid

    def add_bit(self, key: str, label: str = "") -> str:
        with self.lock:
            if key not in self.d["kinds"]:
                raise ValueError("Unknown kind of bit.")
            bid = self._add_bit(key, (label or "").strip().upper())
            self.save()
        return bid

    def retire(self, bid: str, reason: str, note: str = "") -> None:
        if reason not in REASONS:
            raise ValueError("Say why the bit came out: worn out, broke (setup problem), broke while cutting, or other.")
        with self.lock:
            b = self.d["bits"].get(bid)
            if b is None or b.get("retired"):
                raise ValueError("No such bit in use.")
            b.update(retired=time.time(), reason=reason, note=str(note or "")[:200])
            for s in list(self.d["slots"]):
                if self.d["slots"][s].get("bit") == bid:
                    self.d["slots"][s]["bit"] = None
            self.save()

    def set_limit(self, key: str, material: str, hours) -> None:
        """The expected life of a kind of bit in a material, in hours; None or "" takes it away."""
        with self.lock:
            k = self.d["kinds"].get(key)
            if k is None:
                raise ValueError("Unknown kind of bit.")
            m = material_key(material)
            if hours in (None, ""):
                k["limits"].pop(m, None)
            else:
                h = float(hours)
                if not 0 < h <= 1000:
                    raise ValueError("The expected life is a number of hours, more than 0.")
                k["limits"][m] = round(h, 2)
            self.save()

    # ------------------------------------------------------------ which bit is in which bit number
    def suggest(self, key: str):
        """The least-used bit of a kind (the one to put in next), or None."""
        act = self._active(key)
        return min(act, key=lambda b: (sum(b["secs"].values()), b["label"]))["id"] if act else None

    def loading(self, slot: int, label: str, prefer: str = "") -> str:
        """At a bit-change stop for this bit number: the bit to put in, unless one was picked during this stop
        already. That is prefer (the bit this number had earlier in the same job) if given, else the least used of
        its kind."""
        key = self.ensure_kind(label)
        with self.lock:
            cur = self.d["slots"].get(str(slot)) or {}
            if cur.get("picked") and cur.get("kind") == key and self._is_active(cur.get("bit")):
                return cur["bit"]
            ok = self._is_active(prefer) and self.d["bits"][prefer]["kind"] == key
            bid = prefer if ok else self.suggest(key)
            self.d["slots"][str(slot)] = {"kind": key, "bit": bid, "picked": False}
            self.save()
            return bid

    def in_spindle(self, slot: int, label: str) -> str:
        """The bit counted for this bit number when cutting starts: the one loaded at its last stop if it is of the
        right kind, else the least used of the kind."""
        key = self.ensure_kind(label)
        with self.lock:
            cur = self.d["slots"].get(str(slot)) or {}
            if cur.get("kind") == key and self._is_active(cur.get("bit")):
                return cur["bit"]
            bid = self.suggest(key)
            self.d["slots"][str(slot)] = {"kind": key, "bit": bid, "picked": False}
            self.save()
            return bid

    def _is_active(self, bid) -> bool:
        b = self.d["bits"].get(bid or "")
        return b is not None and not b.get("retired")

    def pick(self, slot: int, bid: str) -> None:
        """This bit number has (or is getting) this bit, not the one suggested."""
        with self.lock:
            b = self.d["bits"].get(bid)
            if b is None or b.get("retired"):
                raise ValueError("No such bit in use.")
            self.d["slots"][str(slot)] = {"kind": b["kind"], "bit": bid, "picked": True}
            self.save()

    def next_bit(self, slot: int) -> str:
        """The next bit of the same kind for this bit number (the tap on the stop screen)."""
        with self.lock:
            cur = self.d["slots"].get(str(slot)) or {}
            act = self._active(cur.get("kind", ""))
            if not act:
                raise ValueError("No bits of this kind.")
            ids = [b["id"] for b in act]
            i = ids.index(cur["bit"]) if cur.get("bit") in ids else -1
            bid = ids[(i + 1) % len(ids)]
            self.d["slots"][str(slot)] = {"kind": cur["kind"], "bit": bid, "picked": True}
            self.save()
            return bid

    def done_picking(self, slot: int) -> None:
        with self.lock:
            cur = self.d["slots"].get(str(slot))
            if cur:
                cur["picked"] = False
                self.save()

    # ------------------------------------------------------------ counting
    def record(self, bid: str, secs: float, material: str, job: str, slot) -> None:
        if not bid or secs <= 0:
            return
        m = material_key(material)
        with self.lock:
            b = self.d["bits"].get(bid)
            if b is None:
                return
            b["secs"][m] = round(b["secs"].get(m, 0.0) + secs, 1)
            b["last"] = time.time()
            if b.get("last_job") != job:
                b["jobs"] += 1
                b["last_job"] = job
            ev = self.d["events"]
            ev.append({"t": round(time.time(), 1), "bit": bid, "kind": b["kind"], "slot": slot, "job": job,
                       "material": m, "secs": round(secs, 1)})
            del ev[:-MAX_EVENTS]
            self.save()
        log.info("bit %s (%s): %s more in %s", b["label"], b["kind"], hm(secs), m)

    # ------------------------------------------------------------ what the screens show
    def status(self, bid: str, material: str, job_adds: float = 0.0) -> dict:
        """One bit, for the stop screen: its label, the time used in this material, the expected life if set."""
        b = self.d["bits"].get(bid or "")
        if b is None:
            return {}
        m = material_key(material)
        kind = self.d["kinds"].get(b["kind"], {})
        used = b["secs"].get(m, 0.0)
        limit_h = kind.get("limits", {}).get(m)
        limit = limit_h * 3600.0 if limit_h else None
        state = "ok"
        if limit:
            state = "over" if used >= limit else "near" if used >= NEAR * limit else "ok"
        return {"id": bid, "label": b["label"], "kind": b["kind"], "kind_label": kind.get("label", ""),
                "material": m, "used": round(used), "total": round(sum(b["secs"].values())),
                "limit": round(limit) if limit else None, "state": state, "job_adds": round(job_adds or 0),
                "choices": len(self._active(b["kind"]))}

    def short_text(self, st: dict) -> str:
        """For the stop screen: "Bit B · 2 h 10 used", with the expected life when one is set."""
        if not st:
            return ""
        if st.get("limit"):
            t = "Bit %s · %s of %s used" % (st["label"], hm(st["used"]), hm(st["limit"]))
            if st["state"] == "over":
                t += ": past its life"
            return t
        return "Bit %s · %s used" % (st["label"], hm(st["used"]))

    def plan(self, needs: dict, material: str) -> list:
        """Before a job: the kinds of bit it would take past their expected life. needs: {slot: (label, secs)}.
        Each kind is checked with the bit that would be suggested (its least used), so a warning means every bit of
        that kind is near the end. Kinds with no expected life set never warn."""
        out = []
        for slot, (label, need) in sorted(needs.items()):
            key = kind_key(label)
            bid = self.suggest(key) if key in self.d["kinds"] else None
            st = self.status(bid, material) if bid else {}
            if st.get("limit") and st["used"] + need > st["limit"]:
                out.append(dict(st, slot=slot, need=round(need)))
        return out

    def plan_text(self, w: dict, short: bool = False) -> str:
        if short:
            return "This job takes T%s past its expected life: bit %s has %s of %s used, the job adds about %s." % (
                w["slot"], w["label"], hm(w["used"]), hm(w["limit"]), hm(w["need"]))
        return "T%s %s: bit %s has %s of %s used; this job adds about %s." % (
            w["slot"], w["kind_label"], w["label"], hm(w["used"]), hm(w["limit"]), hm(w["need"]))

    def info(self) -> dict:
        """Everything for the settings page."""
        kinds = []
        for key, k in sorted(self.d["kinds"].items(), key=lambda kv: kv[1]["label"].lower()):
            bits = [dict(b, id=i) for i, b in self.d["bits"].items() if b["kind"] == key]
            active = sorted((b for b in bits if not b.get("retired")), key=lambda b: b["label"])
            gone = sorted((b for b in bits if b.get("retired")), key=lambda b: -b["retired"])
            mats = sorted({m for b in bits for m in b["secs"]} | set(k.get("limits", {})))
            lives = {}
            for m in mats:
                worn = [b["secs"].get(m, 0) for b in gone if b.get("reason") == "worn" and b["secs"].get(m)]
                if worn:
                    lives[m] = {"bits": len(worn), "shortest": round(min(worn)), "average": round(sum(worn) / len(worn)),
                                "suggested_hours": round(0.75 * min(worn) / 3600.0, 1) if len(worn) >= 2 else None}
            kinds.append({"key": key, "label": k["label"], "limits": dict(k.get("limits", {})), "materials": mats,
                          "bits": active, "history": gone, "lives": lives})
        slots = {s: dict(v, label=(self.d["bits"].get(v.get("bit") or "") or {}).get("label"))
                 for s, v in self.d["slots"].items()}
        return {"kinds": kinds, "slots": slots, "reasons": REASONS}


class Tracker:
    """Follows the screen data and counts each bit's time in the spindle (see the module's notes)."""

    def __init__(self, life: BitLife) -> None:
        self.life = life
        self.cur = None            # {"slot", "bit", "e0", "e", "job", "material"} while a bit cuts
        self.stop_slot = None      # the bit number of the stop on the screen now
        self.job = None            # the job file being cut, and the bit each number had in it
        self.job_bits = {}
        self.run = ""              # this run of the job (a file run again is another job)
        self.resume_e = None       # the job clock at the last stop (cutting starts from there)

    def cutting(self):
        """The bit in the spindle now and the time it has cut in this stretch: (bit, seconds), or (None, 0)."""
        c = self.cur
        return (c["bit"], c["e"] - c["e0"]) if c else (None, 0.0)

    def next_for(self, slot: int, label: str) -> str:
        """The bit the coming stop for this bit number will name: the one it had earlier in this job, else the
        least used of its kind."""
        bid = self.job_bits.get(slot, "")
        key = self.life.ensure_kind(label)
        if self.life._is_active(bid) and self.life.d["bits"][bid]["kind"] == key:
            return bid
        return self.life.suggest(key)

    def _job(self, job: dict) -> None:
        if job.get("file") != self.job:
            self.job, self.job_bits = job.get("file"), {}
            self.run = "%s@%d" % (job.get("file") or "", time.time())

    def _close(self) -> None:
        c, self.cur = self.cur, None
        if c and c["e"] > c["e0"]:
            self.life.record(c["bit"], c["e"] - c["e0"], c["material"], c["run"], c["slot"])

    def watch(self, view: dict, material: str) -> None:
        mode = view.get("mode")
        job = view.get("job") or {}
        e = float(job.get("elapsed") or 0)
        if mode == "toolchange":
            if self.cur and job.get("file") == self.cur["job"]:
                self.cur["e"] = max(self.cur["e"], e)      # the job clock stops while the Z1 waits
            self._close()                                  # the bit coming out is done cutting
            self.resume_e = e if job else None
            tc = view.get("toolchange") or {}
            slot = tc.get("tool")
            self._job(job)
            if isinstance(slot, int) and slot not in NOT_BITS:
                if slot != self.stop_slot:
                    self.life.loading(slot, kind_of(tc), prefer=self.job_bits.get(slot, ""))
                self.stop_slot = slot
            return
        if self.stop_slot is not None:                     # the stop is over: what was picked is what went in
            self.life.done_picking(self.stop_slot)
            self.stop_slot = None
        if mode in ("running", "paused") and job:
            now = job.get("now") or {}
            slot = now.get("tool")
            if self.cur and (self.cur["slot"] != slot or self.cur["job"] != job.get("file") or e < self.cur["e"]):
                self._close()
            self._job(job)
            if self.cur is None and isinstance(slot, int) and slot not in NOT_BITS:
                bid = self.life.in_spindle(slot, kind_of(now))
                if bid:
                    self.job_bits[slot] = bid
                    r = self.resume_e
                    e0 = r if r is not None and r <= e else e      # counted from the stop, not the first look
                    self.cur = {"slot": slot, "bit": bid, "e0": e0, "e": e, "job": job.get("file") or "",
                                "run": self.run, "material": material}
            self.resume_e = None
            if self.cur:
                self.cur["e"] = max(self.cur["e"], e)
        elif mode in ("idle", "alarm"):
            self._close()
            self.resume_e = None
            self.job, self.job_bits = None, {}
