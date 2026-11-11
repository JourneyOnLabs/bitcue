"""Bit numbers, names and sticker colors, as set on the settings page.

The starting table comes from the settings (config.py, or "tools" in z1screen.json).
When colors are changed on the settings page (http://z1screen.local:8080/settings),
the whole table is saved on the Pi as bits.json in the state folder, and that
saved table is used from then on. "Go back to the starting colors" deletes it.

The screen picks a bit's color by the bit number the job file uses (T1, T2 ...).
A number with no color shows as a striped gray "no sticker color set" screen.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

log = logging.getLogger("z1screen.bits")

FILE_NAME = "bits.json"
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
MAX_BITS = 60
MAX_NAME = 100      # characters in a bit's name (labels can be long)

# Colors of common dot-sticker packs, as they are drawn on the screen.
# Black is left out: it would vanish on the screen's dark background.
PALETTE = [
    ("White", "#F5F5F5"),
    ("Red", "#D32F2F"),
    ("Orange", "#FF8F00"),
    ("Yellow", "#FFD600"),
    ("Light green", "#7CB342"),
    ("Green", "#2E7D32"),
    ("Light blue", "#00B8D4"),
    ("Blue", "#1565C0"),
    ("Purple", "#7B1FA2"),
    ("Pink", "#D81B60"),
    ("Brown", "#6D4C41"),
    ("Gray", "#9E9E9E"),
]


def clean(table) -> dict:
    """Check a bit table from the settings page. Returns a tidy copy or raises ValueError."""
    if not isinstance(table, dict):
        raise ValueError("The bit list did not arrive in the expected form. Reload the page and try again.")
    if len(table) > MAX_BITS:
        raise ValueError("There can be at most %d bits." % MAX_BITS)
    out = {}
    for key, ent in table.items():
        try:
            n = int(str(key).strip())
        except ValueError:
            raise ValueError("'%s' is not a bit number. Use a whole number such as 7." % key)
        if not 0 <= n <= 9999:
            raise ValueError("Bit numbers go from 0 to 9999 (T%d is outside that)." % n)
        if not isinstance(ent, dict):
            raise ValueError("T%d did not arrive in the expected form." % n)
        color = str(ent.get("color", "")).strip().upper()
        if not HEX.match(color):
            raise ValueError("T%d: the color must be written like #1565C0." % n)
        name = " ".join(str(ent.get("name", "") or "").split())[:MAX_NAME]
        sticker = " ".join(str(ent.get("sticker", "") or "").split())[:30]
        out[str(n)] = {"name": name, "color": color, "sticker": sticker}
    return out


def load(state_dir: str):
    """The saved table, or None if colors were never changed on the settings page."""
    if not state_dir:
        return None
    path = os.path.join(state_dir, FILE_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        tools = clean(data.get("tools", {}))
        log.info("bit colors from the settings page: %d bits", len(tools))
        return tools
    except Exception as exc:
        log.warning("ignored %s (%s); using the starting colors", path, exc)
        return None


def save(state_dir: str, tools: dict) -> None:
    if not state_dir:
        raise ValueError("The screen has no folder to save settings in.")
    path = os.path.join(state_dir, FILE_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "tools": tools}, fh, indent=1,
                  ensure_ascii=False)
    os.replace(tmp, path)
    log.info("saved bit colors: %d bits", len(tools))


def reset(state_dir: str) -> None:
    path = os.path.join(state_dir or "", FILE_NAME)
    if state_dir and os.path.isfile(path):
        os.remove(path)
        log.info("bit colors back to the starting table")
