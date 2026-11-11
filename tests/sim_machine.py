"""A pretend Makera Z1 for testing the screen without the real machine.

It speaks the same framed protocol, accepts one app connection at a time,
announces itself over UDP, serves file downloads/uploads, plays a job at high
speed (using the job's estimated timing), stops at every tool change in the
"Tool" state until confirm() is called (the front button), and reports status
in the same format as the Z1 firmware.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from z1screen import framing as fr  # noqa: E402
from z1screen import gcode  # noqa: E402


class SimMachine:
    def __init__(self, name="Makera_Z1P_SIM", host="127.0.0.1", port=23222, announce_to=("127.0.0.1", 23333),
                 speed=600.0, probe_first=True, compress_downloads=False, freeze_after_change=0.0,
                 line_zero=False) -> None:
        self.freeze_after_change = freeze_after_change   # real seconds the line stays put after a change
        self.line_zero = line_zero                         # file with no G1 moves: the firmware reports line 0
        self.frozen_until = 0.0
        self.frozen_line = 0
        self.name = name
        self.host = host
        self.port = port
        self.announce_to = announce_to
        self.speed = speed
        self.probe_first = probe_first
        self.compress_downloads = compress_downloads
        self.files = {}          # path -> bytes
        self.client = None
        self.connections = 0
        self.refused = 0
        # machine state
        self.state = "Idle"
        self.tool = 1
        self.tlo = -1.25
        self.target = -1
        self.halt = 0
        self.playing = False
        self.play_path = ""
        self.prof = None
        self.sim_t = 0.0          # estimated-seconds position in the job
        self.elapsed = 0.0
        self.last_tick = time.time()
        self.waiting = False
        self.queue = []           # remaining tool changes [(line, tool, t)]
        self.line = 0
        self.done_report = 0
        self.log = []
        self.wco = [0.0, 0.0]        # work origin (G10 L2 P0) in machine coordinates
        self.buffered = []           # "buffer" commands waiting for the next play
        self.played_with = []        # the buffered commands the last play started with
        self.listed = []             # folders listed with "ls"
        self.transfers = []          # ("download" | "done" | "cancel", path) for each file transfer
        self.packet_delay = 0.0      # seconds before each packet of a download (to make a read take a while)

    # -------------------------------------------------------------- job
    def start_play(self, path):
        data = self.files.get(path)
        if data is None:
            return "File not found\r\n"
        self.prof = gcode.Profile(gcode.analyze(data, path))
        self.play_path = path
        self.playing = True
        self.sim_t = 0.0
        self.elapsed = 0.0
        self.line = 0
        self.queue = list(self.prof.changes)
        self.last_tick = time.time()
        self.state = "Run"
        self.halt = 0
        if self.probe_first:
            self._enter_tool(0)
        return "  File size %d\r\n" % len(data)

    def _enter_tool(self, t):
        self.waiting = True
        self.state = "Tool"
        self.target = t

    def confirm(self):
        """The front button."""
        if self.waiting:
            self.waiting = False
            self.tool = self.target
            self.target = -1
            self.state = "Run"
            self.last_tick = time.time()
            if self.freeze_after_change:
                self.frozen_until = time.time() + self.freeze_after_change
                self.frozen_line = self.line

    def tick(self):
        now = time.time()
        dt = now - self.last_tick
        self.last_tick = now
        if not self.playing or self.waiting or self.state in ("Alarm", "Hold", "Pause"):
            return
        if now < self.frozen_until:
            # tool length check and rapid moves after a change: no cutting line yet
            self.elapsed += dt * min(self.speed, 30)
            self.line = self.frozen_line
            return
        self.sim_t += dt * self.speed
        self.elapsed += dt * self.speed
        # find current line from the profile's estimated time
        prof = self.prof
        lo, hi = 1, prof.lines
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if prof.time_at(mid) <= self.sim_t:
                lo = mid
            else:
                hi = mid - 1
        if self.queue and lo >= self.queue[0]["line"]:
            ch = self.queue.pop(0)
            self.line = 0 if self.line_zero else ch["line"] - 3   # firmware keeps the last cutting line
            self.sim_t = prof.time_at(ch["line"])
            self._enter_tool(ch["tool"])
            return
        self.line = 0 if self.line_zero else lo
        if self.sim_t >= prof.total:
            self.playing = False
            self.state = "Idle"
            self.done_report = 3
            self.pending_console.append("Done printing file\r\n")

    def status(self):
        self.tick()
        mx, my = 10.0, 20.0
        s = "<%s|MPos:%.4f,%.4f,-5.0000,0.0000,0.0000|WPos:%.4f,%.4f,3.0000,0.0000,0.0000" % (
            self.state, mx, my, mx - self.wco[0], my - self.wco[1])
        s += "|F:300.0,300.0,100.0|S:12000.0,12000.0,100.0,0,30.1,32.0,1,1,0,0"
        s += "|T:%d,%.3f,%d" % (self.tool, self.tlo, self.target)
        s += "|W:0.00|L:0, 0, 0, 0.0,100.0"
        if self.playing:
            pct = int(100 * self.sim_t / max(1, self.prof.total))
            s += "|P:%d,%d,%d" % (self.line, pct, int(self.elapsed))
        elif self.done_report > 0:
            self.done_report -= 1
            s += "|P:%d,%d,%d" % (self.prof.lines, 100, int(self.elapsed))
        if self.halt:
            s += "|H:%d" % self.halt
        s += "|C:4,0,0,1>\n"
        return s

    # -------------------------------------------------------------- network
    async def start(self):
        self.pending_console = []
        self.server = await asyncio.start_server(self.on_conn, self.host, self.port)
        self.ann = asyncio.create_task(self.announce())
        return self

    async def announce(self):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while True:
            busy = "1" if self.client else "0"
            msg = "%s,%s,%d,%s" % (self.name, self.host, self.port, busy)
            try:
                s.sendto(msg.encode(), self.announce_to)
            except OSError:
                pass
            await asyncio.sleep(0.5)

    async def on_conn(self, reader, writer):
        if self.client is not None:
            self.refused += 1
            writer.close()
            return
        self.connections += 1
        self.client = writer
        parser = fr.FrameParser()
        xfer = {}
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for ptype, payload in parser.feed(data):
                    await self.on_frame(ptype, payload, writer, xfer)
                while self.pending_console:
                    writer.write(fr.build_frame(fr.NORMAL_INFO, self.pending_console.pop(0).encode()))
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.client = None
            try:
                writer.close()
            except Exception:
                pass

    def say(self, writer, text):
        # split like the firmware does (64-byte printf chunks)
        b = text.encode()
        for i in range(0, len(b), 60):
            writer.write(fr.build_frame(fr.NORMAL_INFO, b[i:i + 60]))

    async def on_frame(self, ptype, payload, w, xfer):
        if ptype == fr.CTRL_SINGLE:
            c = payload[:1]
            if c == b"?":
                w.write(fr.build_frame(fr.STATUS_RES, self.status().encode()))
            elif c == b"\x18":
                self.halt = 1
                self.state = "Alarm"
                self.say(w, "ALARM: Abort during cycle\r\n")
            return
        if ptype == fr.CTRL_MULTI:
            text = payload.decode(errors="replace").strip()
            self.log.append(text)
            if text.startswith("buffer "):
                self.say(w, "Command buffered: %s\r\n" % text[7:])
                text = text[7:]
                if not text.startswith("play "):
                    self.buffered.append(text)
                    return
            if text.startswith("play "):
                path = text.split()[1].replace("\x01", " ")
                answer = self.start_play(path)
                if self.playing:
                    self.played_with = list(self.buffered)
                    self.buffered = []
                self.say(w, answer)
            elif text.startswith("G10L2P0"):
                import re as _re
                mx = _re.search(r"X(-?[\d.]+)", text)
                my = _re.search(r"Y(-?[\d.]+)", text)
                if mx:
                    self.wco[0] = float(mx.group(1))
                if my:
                    self.wco[1] = float(my.group(1))
                self.say(w, "ok\r\n")
            elif text.startswith("ls "):
                folder = text.split()[-1].replace("\x01", " ").rstrip("/")
                self.listed.append(folder)
                names = {}
                for fp, data in self.files.items():
                    if fp.startswith(folder + "/"):
                        rest = fp[len(folder) + 1:]
                        if "/" in rest:
                            names[rest.split("/", 1)[0] + "/"] = 0
                        else:
                            names[rest] = len(data)
                text_out = "".join("%s %d 20261003140558\r\n" % (n.replace(" ", "\x01"), size)
                                   for n, size in sorted(names.items()))
                b = text_out.encode()
                for i in range(0, len(b), 200):
                    w.write(fr.build_frame(fr.LOAD_INFO, b[i:i + 200]))
                w.write(fr.build_frame(fr.LOAD_FINISH, b""))
            elif text == "progress":
                if self.playing:
                    pct = int(100 * self.sim_t / max(1, self.prof.total))
                    e = int(self.elapsed)
                    self.say(w, "file: %s, %u %% complete, elapsed time: %02d:%02d:%02d\r\n"
                             % (self.play_path, pct, e // 3600, (e % 3600) // 60, e % 60))
                else:
                    self.say(w, "Not currently playing\r\n")
            elif text == "$X":
                self.halt = 0
                self.state = "Idle"
                self.say(w, "[Caution: Unlocked]\r\nok\r\n")
            else:
                self.say(w, "ok\r\n")
            return
        if ptype == fr.FILE_START:
            text = payload.decode(errors="replace").strip()
            cmd, _, path = text.partition(" ")
            path = path.replace("\x01", " ")
            xfer.clear()
            xfer.update({"cmd": cmd, "path": path})
            if cmd == "download":
                self.transfers.append(("download", path))
                data = self.files.get(path, b"")
                if self.compress_downloads:
                    import qlz_store
                    data = qlz_store.container(data)
                xfer["data"] = data
                xfer["size"] = 8192
                xfer["n"] = (len(data) + 8191) // 8192
                w.write(fr.build_frame(fr.FILE_MD5, b"default_md5_hash_value_32_bytes_"))
            return
        if ptype == fr.FILE_VIEW and xfer.get("cmd") == "download":
            w.write(fr.build_frame(fr.FILE_VIEW, xfer["n"].to_bytes(4, "big")))
            return
        if ptype == fr.FILE_DATA and xfer.get("cmd") == "download":
            if self.packet_delay:
                await asyncio.sleep(self.packet_delay)
            if xfer.get("cmd") != "download":          # cancelled meanwhile
                return
            seq = int.from_bytes(payload[:4], "big")
            chunk = xfer["data"][(seq - 1) * 8192: seq * 8192]
            w.write(fr.build_frame(fr.FILE_DATA, seq.to_bytes(4, "big") + chunk))
            return
        if ptype == fr.FILE_END and xfer.get("cmd") == "download":
            self.transfers.append(("done", xfer.get("path", "")))
            xfer.clear()
            return
        if ptype == fr.FILE_CAN:
            self.transfers.append(("cancel", xfer.get("path", "")))
            xfer.clear()
            return
        if ptype == fr.FILE_MD5 and xfer.get("cmd") == "upload":
            w.write(fr.build_frame(fr.FILE_VIEW, b""))
            return
        if ptype == fr.FILE_VIEW and xfer.get("cmd") == "upload":
            xfer["n"] = int.from_bytes(payload[:4], "big")
            xfer["chunks"] = {}
            w.write(fr.build_frame(fr.FILE_DATA, (1).to_bytes(4, "big")))
            return
        if ptype == fr.FILE_DATA and xfer.get("cmd") == "upload":
            seq = int.from_bytes(payload[:4], "big")
            xfer["chunks"][seq] = payload[4:]
            if seq < xfer["n"]:
                w.write(fr.build_frame(fr.FILE_DATA, (seq + 1).to_bytes(4, "big")))
            else:
                data = b"".join(xfer["chunks"][i] for i in range(1, xfer["n"] + 1))
                p = xfer["path"][:-3] if xfer["path"].endswith(".lz") else xfer["path"]
                self.files[p] = data
                w.write(fr.build_frame(fr.FILE_END, b""))
                xfer.clear()
            return
