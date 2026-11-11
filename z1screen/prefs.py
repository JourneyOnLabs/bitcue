"""Choices made on the settings page's setup section, kept on the Pi.

Saved as prefs.json in the state folder. Each key that is present wins over the
settings files (config.py, z1screen.json):

  {"version": 1,
   "anchor1": "auto" | [x, y],          Anchor 1 in machine coordinates, or read from the machine
   "start_from_screen": true | false,   whether the touch screen may start jobs
   "start_ok": <time>,                  when that was turned on (with the warning accepted)
   "run_defaults": {"probe": "middle" | null, "margin": true, "level": null | {"i", "j", "h"}},
   "plate_layouts": ["/sd/gcodes/..."],
   "settings_pin": "<salt>:<sha256>",   the settings page PIN, stored as a salted hash
   "notify": {"server", "topic", "token", "stops", "soon_min"}}   phone notifications (notify.py)

The small screen's own choices are kept apart, in screen.json, because the screen
program (tft.py) reads them too when it starts, before the screen service answers:

  {"flip": true | false,       the picture and touches turned upside down (also install.sh --flip)
   "clock_24h": true | false}  times of day as 14:30 instead of 2:30 PM
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time

log = logging.getLogger("z1screen.prefs")

FILE_NAME = "prefs.json"
KEYS = ("anchor1", "start_from_screen", "start_ok", "run_defaults", "plate_layouts", "settings_pin", "notify")
SCREEN_FILE = "screen.json"
SCREEN_KEYS = ("flip", "clock_24h")
_PIN_RE = re.compile(r"^\d{4,12}$")


def load(state_dir: str) -> dict:
    path = os.path.join(state_dir or "", FILE_NAME)
    if not state_dir or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("not a settings object")
        return {k: data[k] for k in KEYS if k in data}
    except Exception as exc:
        log.warning("ignored %s (%s)", path, exc)
        return {}


def save(state_dir: str, prefs: dict) -> None:
    if not state_dir:
        raise ValueError("The screen has no folder to save settings in.")
    path = os.path.join(state_dir, FILE_NAME)
    data = {"version": 1, "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
    data.update({k: prefs[k] for k in KEYS if k in prefs})
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def load_screen(state_dir: str) -> dict:
    """The small screen's choices from screen.json, only those set."""
    path = os.path.join(state_dir or "", SCREEN_FILE)
    if not state_dir or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: data[k] for k in SCREEN_KEYS if isinstance(data.get(k), bool)}
    except Exception as exc:
        log.warning("ignored %s (%s)", path, exc)
        return {}


def save_screen(state_dir: str, screen: dict) -> None:
    if not state_dir:
        raise ValueError("The screen has no folder to save settings in.")
    path = os.path.join(state_dir, SCREEN_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(screen, fh)
    os.replace(path + ".tmp", path)


def apply(cfg: dict, prefs: dict) -> dict:
    """The settings with the settings page's choices put over them (cfg is changed and returned)."""
    for k in ("anchor1", "start_from_screen", "run_defaults", "plate_layouts", "notify"):
        if k in prefs:
            cfg[k] = prefs[k]
    return cfg


# ---------------------------------------------------------------- checking what the page sends
def clean_anchor(v):
    """"auto" or [x, y] in mm (machine coordinates), else ValueError."""
    if v == "auto":
        return "auto"
    try:
        x, y = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ValueError("Type Anchor 1's X and Y as numbers, for example -200.5 and -195.25.")
    if not (-2000 <= x <= 2000 and -2000 <= y <= 2000):
        raise ValueError("Anchor 1's X and Y must be between -2000 and 2000 mm.")
    return [round(x, 3), round(y, 3)]


def clean_run(v) -> dict:
    if not isinstance(v, dict):
        raise ValueError("The run settings did not arrive in the expected form.")
    probe = v.get("probe")
    if probe not in ("middle", None):
        raise ValueError("The Z probe is either in the middle of the job or off.")
    level = v.get("level")
    if level is not None:
        try:
            i, j, h = int(level["i"]), int(level["j"]), float(level["h"])
        except (TypeError, ValueError, KeyError):
            raise ValueError("Leveling needs the points across (X), the points along (Y) and the height.")
        if not (2 <= i <= 10 and 2 <= j <= 10):
            raise ValueError("Leveling takes 2 to 10 points each way.")
        if not 0.5 <= h <= 20:
            raise ValueError("The leveling height must be between 0.5 and 20 mm.")
        level = {"i": i, "j": j, "h": round(h, 2)}
    return {"probe": probe, "margin": bool(v.get("margin")), "level": level}


def clean_folders(v) -> list:
    if isinstance(v, str):
        v = [p for p in re.split(r"[\n,]+", v)]
    if not isinstance(v, list):
        raise ValueError("The layout folders did not arrive in the expected form.")
    out = []
    for p in v:
        p = str(p or "").strip().rstrip("/")
        if not p:
            continue
        if not p.startswith("/sd/gcodes/") or ".." in p.split("/"):
            raise ValueError("A layout folder must be a folder on the machine's card, like /sd/gcodes/my-sheet.")
        if p not in out:
            out.append(p[:200])
    if len(out) > 20:
        raise ValueError("At most 20 layout folders.")
    return out


# ---------------------------------------------------------------- the settings page PIN
def pin_hash(pin: str) -> str:
    if not _PIN_RE.match(pin or ""):
        raise ValueError("The PIN must be 4 to 12 digits.")
    salt = secrets.token_hex(8)
    return salt + ":" + hashlib.sha256((salt + pin).encode()).hexdigest()


def pin_ok(stored: str, given: str) -> bool:
    """True if the PIN given matches: stored as a salted hash (settings page) or as plain digits (z1screen.json)."""
    stored, given = str(stored or ""), str(given or "")
    if not stored:
        return True
    if ":" not in stored:
        return hmac.compare_digest(stored, given)
    salt, want = stored.split(":", 1)
    return hmac.compare_digest(hashlib.sha256((salt + given).encode()).hexdigest(), want)
