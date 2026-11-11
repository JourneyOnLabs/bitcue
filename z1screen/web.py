"""Tiny web server for the screen (no extra packages needed).

  GET  /              the screen page
  GET  /state         the current screen data as JSON
  GET  /events        the same data pushed as Server-Sent Events
  GET  /settings      setup, bit colors, the plate and the demo, for a laptop or phone
  GET  /api/setup     the setup section: Anchor 1, starting from the small screen, run settings, layouts, PIN
  POST /api/setup     one change: {"anchor1": "auto" | [x, y]} | {"read_anchor": true} |
                      {"start_from_screen": true, "accept": true} | {"start_from_screen": false} |
                      {"run_defaults": {...}} | {"plate_layouts": [...]} | {"pin": "1234" | ""}
  GET  /api/bits      the bit table, sticker palette and bits seen in job files
  POST /api/bits      save the bit table          {"tools": {...}}
  POST /api/bits/reset  go back to the starting colors
  GET  /api/demo      demo status and the jobs it can play
  POST /api/demo      {"action": "start" | "press" | "skip" | "speed" | "stop", "path", "speed"}
  POST /api/check     show one bit's color on the screen for 20 s   {"tool": 3, "entry": {...}}
  GET  /api/screen    the small screen's settings
  POST /api/screen    {"flip": true | false, "clock_24h": true | false}  the small screen's picture and clock
  GET  /screen.png    a picture of what the Pi's small screen shows now
  GET  /api/jobs      folders on the Z1 and the files or designs in them (?refresh=1 asks the Z1 again)
  GET  /api/design    ?folder=...&design=...  a design of a plate layout: the plate, its spots and files
  GET  /api/place     ?path=...  one job file on the plate: its cut area, the areas already cut, and a
                      clear place for it
  GET  /api/read      ?path=...  how reading a file from the Z1 is going
  POST /api/read      {"path"}  read a job file from the Z1 (only while it is idle)
  GET  /api/plate     the plate on the Z1 and the areas cut on it
  POST /api/plate     {"action": "new" | "previous" | "size" | "remove" | "add_file" | "add_rect" | "spot", ...}
                      change the record of the plate (never sent to the Z1); "previous" brings back the record
                      the last "new" replaced
  GET  /api/touch     the touch screen's calibration; the small screen names its framebuffer (?screen=640x480)
                      and gets one made on it, else the built-in one for its size, if there is one
  POST /api/touch     {"action": "calibrate"}  show the calibration dots on the small screen
  POST /api/start     {"path", "plate_checked": true, "offset": [x, y]}  start a job, moved x, y mm on the
                      plate; only the small screen on this Pi itself can send this (the request must
                      come from 127.0.0.1)
  GET  /debug         what the Pi sees (machine, app commands, console), for setup
  GET  /health        "ok" and the version

Guards:
  * POST requests must be JSON (Content-Type: application/json, exactly). Browsers
    don't let other websites send that without asking first.
  * A POST that a browser sends from another website (its Origin header names a
    different host) is refused.
  * Requests must name the Pi by a local name (z1screen.local, a name with no
    dot, *.lan, *.home, *.home.arpa, *.internal, *.localdomain, localhost) or an
    IP address, or a name listed in "allowed_hosts" in the settings. That stops
    another website from reaching the Pi by pointing its own name at the Pi's
    address (DNS rebinding).
  * With a settings PIN set, changes need it (header X-Settings-PIN), except from
    the Pi itself (its touch screen).
  * Only /api/start reaches the machine, and only from the Pi itself.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import urllib.parse

from . import __version__
from . import prefs as prefs_mod
from .hub import DemoBlocked

log = logging.getLogger("z1screen.web")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 64 * 1024
REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           409: "Conflict",
           413: "Payload Too Large", 415: "Unsupported Media Type", 500: "Internal Server Error"}
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


def host_name(value: str) -> str:
    """The host part of a Host header or of an Origin URL, lower case, without the port."""
    v = (value or "").strip().lower()
    if "://" in v:
        v = urllib.parse.urlsplit(v).netloc
    if v.startswith("["):                      # [::1]:8080
        return v[1:].split("]", 1)[0]
    if v.count(":") == 1:
        v = v.split(":", 1)[0]
    return v.rstrip(".")


def local_host(name: str, extra=()) -> bool:
    """True for a name that can only mean a machine on the local network (or an IP address)."""
    if not name:
        return True                            # HTTP/1.0 clients without a Host header
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if name == "localhost" or "." not in name or name in [str(x).lower() for x in extra]:
        return True
    return name.endswith(LOCAL_SUFFIXES)


class Web:
    def __init__(self, hub, port: int = 8080, page_dir: str = "") -> None:
        self.hub = hub
        self.port = port
        self.page_dir = page_dir or os.path.join(HERE, "web")

    async def serve(self) -> None:
        server = await asyncio.start_server(self.handle, "0.0.0.0", self.port)
        log.info("screen page on http://0.0.0.0:%d/", self.port)
        async with server:
            await server.serve_forever()

    async def handle(self, reader, writer) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except Exception:
            writer.close()
            return
        try:
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = {}
            for h in lines[1:]:
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            path, _, query = target.partition("?")
            q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
            if not local_host(host_name(headers.get("host", "")), self.hub.cfg.get("allowed_hosts") or ()):
                await self._send(writer, 403, b"Open this page by the Pi's local name (for example "
                                 b"http://z1screen.local:8080) or its IP address. Other names can be allowed with "
                                 b"allowed_hosts in z1screen.json.", "text/plain")
                return
            if method == "POST":
                await self._post(reader, writer, path, headers)
            elif method not in ("GET", "HEAD"):
                await self._send(writer, 405, b"method not allowed", "text/plain")
            elif path in ("/", "/index.html"):
                await self._file(writer, "index.html", "text/html; charset=utf-8")
            elif path in ("/settings", "/settings.html"):
                await self._file(writer, "settings.html", "text/html; charset=utf-8")
            elif path == "/state":
                await self._json(writer, 200, self.hub.view)
            elif path == "/api/bits":
                await self._json(writer, 200, self.hub.bits_info())
            elif path == "/api/bitlife":
                await self._json(writer, 200, self.hub.bitlife_info())
            elif path == "/api/demo":
                st = self.hub.demo_status()
                st["jobs"] = self.hub.demo_jobs()
                await self._json(writer, 200, st)
            elif path == "/api/screen":
                await self._json(writer, 200, self.hub.screen_info())
            elif path == "/screen.png":
                png = await self.hub.screen_png()
                if png is None:
                    await self._send(writer, 404, b"the small screen is not set up on this Pi", "text/plain")
                else:
                    await self._send(writer, 200, png, "image/png")
            elif path.startswith("/preview/") and path.endswith(".png"):
                f = self.hub.store.preview_file(path[len("/preview/"):-len(".png")])
                if not f:
                    await self._send(writer, 404, b"no picture of this part yet", "text/plain")
                else:
                    with open(f, "rb") as fh:
                        await self._send(writer, 200, fh.read(), "image/png", cache=True)
            elif path == "/api/jobs":
                if q.get("refresh"):
                    try:
                        await asyncio.wait_for(self.hub.refresh_listings(), 12)
                    except asyncio.TimeoutError:
                        pass
                await self._json(writer, 200, self.hub.jobs_view())
            elif path == "/api/design":
                d = await asyncio.get_running_loop().run_in_executor(
                    None, self.hub.design_view, q.get("folder", ""), q.get("design", ""))
                await self._json(writer, 200, d)
            elif path == "/api/place":
                d = await asyncio.get_running_loop().run_in_executor(None, self.hub.place_view, q.get("path", ""))
                await self._json(writer, 200, d)
            elif path == "/api/read":
                await self._json(writer, 200, self.hub.read_status(q.get("path", "")))
            elif path in ("/api/plate", "/api/plates"):
                await self._json(writer, 200, self.hub.plates_info())
            elif path == "/api/touch":
                screen = q.get("screen", "") if self._local(writer) else ""     # only the small screen names it
                await self._json(writer, 200, self.hub.touch_info(screen))
            elif path == "/debug":
                await self._send(writer, 200, json.dumps(self.hub.debug_info(), indent=1).encode(),
                                 "application/json")
            elif path == "/health":
                await self._send(writer, 200, ("ok %s" % __version__).encode(), "text/plain")
            elif path == "/api/setup":
                await self._json(writer, 200, self.hub.setup_info())
            elif path.startswith("/fonts/") and "/" not in path[7:] and path.endswith(".woff2"):
                await self._file(writer, "fonts/" + path[7:], "font/woff2", cache=True)
            elif path == "/events":
                await self._events(writer)
                return
            else:
                await self._send(writer, 404, b"not found", "text/plain")
        except Exception:
            log.debug("web request failed", exc_info=True)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _post(self, reader, writer, path: str, headers: dict) -> None:
        try:
            n = int(headers.get("content-length", "0") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            await self._json(writer, 413, {"error": "The request is too large."})
            return
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            await self._json(writer, 415, {"error": "Settings must be sent as JSON."})
            return
        origin = headers.get("origin", "")
        if origin and origin != "null" and host_name(origin) != host_name(headers.get("host", "")):
            await self._json(writer, 403, {"error": "Changes can only be made from the Pi's own settings page."})
            return
        if origin == "null":
            await self._json(writer, 403, {"error": "Changes can only be made from the Pi's own settings page."})
            return
        pin = self.hub.settings_pin()
        if pin and not self._local(writer) and not prefs_mod.pin_ok(pin, headers.get("x-settings-pin", "")):
            await self._json(writer, 403, {"error": "Type the settings PIN to make changes." if not
                                           headers.get("x-settings-pin") else "That PIN is not right.", "pin": True})
            return
        body = await asyncio.wait_for(reader.readexactly(n), 10) if n else b""
        try:
            data = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            await self._json(writer, 400, {"error": "The request was not valid JSON."})
            return
        hub = self.hub
        try:
            if path == "/api/bits":
                hub.set_tools(data.get("tools"))
                await self._json(writer, 200, dict(hub.bits_info(), ok=True))
            elif path == "/api/bitlife":
                await self._json(writer, 200, dict(hub.change_bitlife(data), ok=True))
            elif path == "/api/bits/reset":
                hub.reset_tools()
                await self._json(writer, 200, dict(hub.bits_info(), ok=True))
            elif path == "/api/demo":
                action = str(data.get("action", ""))
                if action == "start":
                    st = await hub.start_demo(str(data.get("path", "") or ""), data.get("speed", 60))
                elif action == "press":
                    st = hub.demo_press()
                elif action == "skip":
                    st = hub.demo_skip()
                elif action == "speed":
                    st = hub.demo_speed(data.get("speed", 60))
                elif action == "stop":
                    st = hub.stop_demo("")
                else:
                    raise ValueError("Unknown demo action.")
                await self._json(writer, 200, st)
            elif path == "/api/check":
                st = await hub.start_check(data.get("tool"), data.get("entry"))
                await self._json(writer, 200, st)
            elif path == "/api/screen":
                await self._json(writer, 200, dict(hub.set_screen(data), ok=True))
            elif path == "/api/setup":
                await self._json(writer, 200, dict(hub.set_setup(data), ok=True))
            elif path == "/api/notify/test":
                await self._json(writer, 200, dict(await hub.notify_test(), ok=True))
            elif path in ("/api/plate", "/api/plates"):
                await self._json(writer, 200, dict(hub.change_plate(data), ok=True))
            elif path == "/api/read":
                await self._json(writer, 200, hub.start_read(str(data.get("path", ""))))
            elif path == "/api/touch":
                if "cal" in data and not self._local(writer):
                    await self._json(writer, 403, {"error": "Only the small screen saves its calibration."})
                    return
                await self._json(writer, 200, dict(hub.set_touch(data), ok=True))
            elif path == "/api/start":
                if not self._local(writer):
                    await self._json(writer, 403, {"error": "Jobs can only be started on the Pi's own screen."})
                    return
                st = await hub.start_job(str(data.get("path", "")), data.get("plate_checked") is True,
                                         data.get("offset"))
                await self._json(writer, 200, st)
            else:
                await self._json(writer, 404, {"error": "Not found."})
        except DemoBlocked as exc:
            await self._json(writer, 409, {"error": str(exc)})
        except ValueError as exc:
            await self._json(writer, 400, {"error": str(exc) or "That was not accepted."})
        except OSError as exc:
            log.warning("could not save settings: %s", exc)
            await self._json(writer, 500, {"error": "The Pi could not save the settings (%s)." % exc.strerror})

    @staticmethod
    def _local(writer) -> bool:
        """True if the request came from this Pi itself (the small screen's program)."""
        peer = writer.get_extra_info("peername") or ("",)
        return str(peer[0]) in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    async def _file(self, writer, name: str, ctype: str, cache: bool = False) -> None:
        p = os.path.join(self.page_dir, name)
        try:
            with open(p, "rb") as fh:
                body = fh.read()
        except OSError:
            await self._send(writer, 404, b"missing file", "text/plain")
            return
        await self._send(writer, 200, body, ctype, cache)

    async def _json(self, writer, code: int, obj) -> None:
        await self._send(writer, code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

    async def _send(self, writer, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
        hdr = ("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
               "Cache-Control: %s\r\nConnection: close\r\n\r\n"
               % (code, REASONS.get(code, "OK"), ctype, len(body), "max-age=86400" if cache else "no-store"))
        writer.write(hdr.encode("latin-1") + body)
        await writer.drain()

    async def _events(self, writer) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n"
                     b"Connection: keep-alive\r\nX-Accel-Buffering: no\r\n\r\nretry: 2000\n\n")
        await writer.drain()
        seq = -1
        idle = 0
        try:
            while True:
                if self.hub.view_seq != seq:
                    seq = self.hub.view_seq
                    writer.write(b"data: " + json.dumps(self.hub.view).encode() + b"\n\n")
                    await writer.drain()
                    idle = 0
                else:
                    idle += 1
                    if idle >= 30:
                        writer.write(b": keep-alive\n\n")
                        await writer.drain()
                        idle = 0
                await asyncio.sleep(0.25)
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
