"""Settings. Defaults below; any of them can be overridden in a JSON file.

Files read, later ones win:
  /etc/z1screen/config.json      (written by the installer: which screen the Pi drives)
  /boot/firmware/z1screen.json   (on the SD card's boot drive, so it can be edited on any computer)
  /boot/z1screen.json
  the file named in the Z1SCREEN_CONFIG environment variable or --config

Choices made on the settings page (bit colors, Anchor 1, starting jobs from the
small screen, run settings, the screen's picture) are kept in the state folder
and win over all of these.
"""

from __future__ import annotations

import copy
import json
import logging
import os

log = logging.getLogger("z1screen.config")

DEFAULTS = {
    # The machine. Leave empty to find it automatically from its Wi-Fi announcement.
    "machine_ip": "",
    "machine_name": "",
    "machine_port": 2222,
    "discovery_port": 3333,
    # The relay Makera Studio connects to. It shows up in Makera Studio as the
    # machine's name with this ending added.
    "relay_port": 2222,
    # "hold": the Pi keeps the machine's connection (Makera Studio connects through the Pi).
    # "polite": the Pi connects only for a moment every polite_gap seconds, so an app
    # can connect to the machine directly; the screen then updates while no app is connected.
    "connection": "hold",
    "polite_gap": 4.0,
    "relay_name_suffix": "_screen",
    "announce_interval": 1.0,
    "announce_to": [],          # extra addresses to send the announcement to
    # Other machine ports passed straight through, so the camera and web pages
    # still work when Makera Studio is connected through the Pi.
    "forward_ports": [80, 82],
    # The screen.
    "web_port": 8080,
    # Which screen the Pi drives: "tft" (a small screen on the Pi) or "hdmi" (a monitor).
    # The installer sets this in /etc/z1screen/config.json.
    "display": "tft",
    # The Pi's small screen (320 x 240, or 640 x 480 drawn at twice the size).
    # device: leave empty to find it by its size, or name it, for example "/dev/fb1".
    # flip: true turns the picture upside down (the settings page has a switch for this).
    # clock_24h: true writes times of day as 14:30 instead of 2:30 PM (also on the settings page).
    "tft": {"device": "", "flip": False, "clock_24h": False},
    "poll_interval": 1.0,
    # Bit colors, one per bit number. Put a sticker of the same color on each bit and its
    # container. A job file's own tool list names each bit by its type and size
    # (gcode.describe_tool); a name here is used only for a bit number a file doesn't describe.
    # The settings page is the easier way to change these.
    "tools": {
        "0": {"name": "Probe", "color": "#F5F5F5", "sticker": "White"},
        "1": {"name": "", "color": "#D32F2F", "sticker": "Red"},
        "2": {"name": "", "color": "#FFD600", "sticker": "Yellow"},
        "3": {"name": "", "color": "#1565C0", "sticker": "Blue"},
        "4": {"name": "", "color": "#FF8F00", "sticker": "Orange"},
        "5": {"name": "", "color": "#D81B60", "sticker": "Pink"},
        "6": {"name": "", "color": "#2E7D32", "sticker": "Green"},
        "7": {"name": "", "color": "#00B8D4", "sticker": "Light blue"},
        "8": {"name": "", "color": "#6D4C41", "sticker": "Brown"},
        "8888": {"name": "Laser", "color": "#9E9E9E", "sticker": "Gray"},
        "9999": {"name": "3D probe", "color": "#F5F5F5", "sticker": "White"},
    },
    # Prefer the bit names written in the job file's header over the names above.
    "names_from_file": True,
    # The machine's Anchor 1 corner in machine coordinates, [X, Y], as Makera Studio's
    # run window uses it (it sends G10L2P0X<x>Y<y> for work origin Anchor 1 with offsets 0).
    # "auto": read it from the machine's own settings file (/sd/config.txt,
    # coordinate.anchor1_x and _y). Used to start jobs from the small screen and to place
    # each job's cut area on the plate. The settings page can set it too.
    "anchor1": "auto",
    # Read the machine's config.txt once while it is idle (for Anchor 1).
    "read_machine_config": True,
    # While the machine is idle, read again the job files the Pi knows but has no picture of, to draw their parts
    # (one at a time; it stops at once for anything asked on the screens).
    "read_for_previews": True,
    # Starting jobs from the Pi's touch screen. Off until it is turned on on the settings page
    # (or here), because a start moves the machine.
    "start_from_screen": False,
    # The plate (stock) in the Anchor 1 corner: {"w": 200, "h": 150, "t": 6, "material": "Plywood"},
    # in mm. null: not set yet; the settings page asks for it before jobs can be placed.
    "plate": None,
    # mm of bare plate kept between a new job's cuts and anything already cut.
    "plate_gap": 1.0,
    # Folders whose files are the parts of one planned plate layout (a spot map on the
    # screen instead of free placing). Each file states its part in a header note, see README.
    "plate_layouts": [],
    # How file names become design names on the screens: regular expressions tried in
    # order on the file name without its extension; the group named "design" is the design.
    # Files of one design at different spots of a layout share it.
    # The default turns "bracket-position-2" into "Bracket".
    "design_names": [r"^(?P<design>.+?)-position-\d+(?:-.*)?$"],
    # Run settings for files that don't state a run window: Z probe in the middle of the
    # part's cutting area, edge scan on, leveling off ({"i": 5, "j": 5, "h": 2} to turn it on).
    "run_defaults": {"probe": "middle", "margin": True, "level": None},
    # Settings page PIN: when set (4 to 12 digits), changes on the settings page need it.
    # The settings page can set it too. "" = no PIN.
    "settings_pin": "",
    # Phone notifications at a bit change, through ntfy (see notify.py). The settings page sets these too.
    # topic: empty = off. soon_min: also warn this many minutes before a stop (0 = no early warning).
    "notify": {"server": "https://ntfy.sh", "topic": "", "token": "", "stops": True, "soon_min": 0},
    "log_level": "INFO",
}

CONFIG_FILES = [
    "/etc/z1screen/config.json",
    "/boot/firmware/z1screen.json",
    "/boot/z1screen.json",
]


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(extra_path: str = "") -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    paths = list(CONFIG_FILES)
    env = os.environ.get("Z1SCREEN_CONFIG", "")
    for p in (env, extra_path):
        if p:
            paths.append(p)
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    _merge(cfg, json.load(fh))
                log.info("read settings from %s", p)
            except Exception as exc:
                log.warning("ignored %s: %s", p, exc)
    return cfg
