"""Parse the Z1 status report.

The Z1 motion firmware (Kernel::get_query_string) answers '?' with, for example:
  <Run|MPos:..|WPos:..|F:..|S:..|T:3,-1.474,4|W:0.00|L:0, 0, 0, 0.0,100.0|P:15433,37,6120|C:...>
T: active tool, tool length offset, and (on machines without an automatic tool
changer, like the Z1) the tool the machine is waiting for.
P: line being cut (1-based), percent of the file's bytes played, seconds of
playing time. The seconds stop while the machine waits at a tool change or is
paused, so they match the Elapsed time in Makera Studio.
H: halt reason when the machine is halted.
"""

from __future__ import annotations

HALT_REASONS = {
    1: "Stopped by hand",
    2: "Homing failed",
    3: "Probe failed",
    4: "Tool length check failed",
    9: "Spindle temperature error",
    10: "Soft limit reached",
    11: "Cover opened while running",
    12: "Probe not detected: check the probe cable",
    13: "Emergency stop pressed",
    14: "Electronics temperature error",
    15: "Machine not homed",
    21: "Hard limit reached, reset needed",
    22: "X motor error, reset needed",
    23: "Y motor error, reset needed",
    24: "Z motor error, reset needed",
    25: "Spindle stalled, reset needed",
    26: "Storage read error, reset needed",
    41: "Spindle alarm, turn the machine off and on",
}

STATE_WORDS = {
    "Run": "Running",
    "Home": "Homing",
    "Hold": "Paused",
    "Pause": "Paused",
    "Tool": "Waiting for a tool change",
    "Wait": "Waiting",
    "Idle": "Idle",
    "Alarm": "Stopped",
    "Sleep": "Asleep",
}


def _num(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_status(text: str):
    """Return a dict for one status report, or None if it is not one."""
    s = text.find("<")
    e = text.rfind(">")
    if s < 0 or e <= s:
        return None
    parts = text[s + 1:e].split("|")
    if not parts or not parts[0].strip():
        return None
    st = {"state": parts[0].strip(), "playing": False}
    fields = {}
    for p in parts[1:]:
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        fields[k.strip()] = [_num(x) for x in v.split(",")]
    st["fields"] = fields

    t = fields.get("T")
    if t and t[0] is not None:
        st["tool"] = int(t[0])
        st["tlo"] = t[1] if len(t) > 1 else None
        st["target_tool"] = int(t[2]) if len(t) > 2 and t[2] is not None else None

    p = fields.get("P")
    if p and len(p) >= 3 and None not in p[:3]:
        st["playing"] = True
        st["line"] = int(p[0])
        st["percent"] = int(p[1])
        st["elapsed"] = int(p[2])

    h = fields.get("H")
    if h and h[0] is not None:
        st["halt"] = int(h[0])
        st["halt_text"] = HALT_REASONS.get(int(h[0]), "Halted (code %d)" % int(h[0]))

    c = fields.get("C")
    if c and c[0] is not None:
        st["model"] = int(c[0])
    return st
