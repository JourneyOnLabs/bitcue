"""Reading a file from the Z1's card on the Pi's own connection.

The same exchange Makera's controllers use to download a file (the Makera
framed transfer; see XMODEM.recv and Controller.downloadCommand in the Carvera
Community Controller, which reads files and config.txt from the Z1 this way):

  Pi  FILE_START "download <path>\\n"
  Z1  FILE_MD5   <md5 of the file, or the Z1's placeholder>
  Pi  FILE_VIEW
  Z1  FILE_VIEW  <number of packets, 4 bytes>
  Pi  FILE_DATA  <packet number, 4 bytes>          for packets 1, 2, ...
  Z1  FILE_DATA  <packet number><up to 8 KB>
  Pi  FILE_END   after the last packet

The data may come QuickLZ-compressed (it then starts with two zero bytes);
the caller unpacks it. The Pi does this only while the Z1 is idle and no app is
connected through the Pi, and stops polling the status during the transfer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time

from . import framing as fr
from .start import escape_path

log = logging.getLogger("z1screen.fetch")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


class ReadError(Exception):
    pass


class FileRead:
    # (A wait of up to 5 s, asked again up to 3 times, stays under the 20 s the hub
    # allows its own connection to go without an answer.)
    def __init__(self, path: str, timeout: float = 5.0, retries: int = 3) -> None:
        self.path = path
        self.timeout = timeout
        self.retries = retries
        self.q = asyncio.Queue()
        self.got = 0          # packets received
        self.total = 0        # packets in the file
        self.bytes = 0
        self.md5 = ""         # what the Z1 advertised ("" if it gave no real digest)
        self.stopped = ""     # set by stop(): the read ends at the next frame, telling the Z1 (FILE_CAN)

    def stop(self, why: str = "The Pi stopped reading the file.") -> None:
        """Ends the read within one round trip, the way an error does (the Z1 is sent FILE_CAN)."""
        self.stopped = why
        self.q.put_nowait((None, b""))

    def on_frame(self, ptype: int, payload: bytes) -> None:
        """File frames from the machine (called by the hub's reader)."""
        self.q.put_nowait((ptype, payload))

    async def _next(self, want: int):
        end = time.time() + self.timeout
        while True:
            left = end - time.time()
            if left <= 0:
                return None
            try:
                ptype, payload = await asyncio.wait_for(self.q.get(), left)
            except asyncio.TimeoutError:
                return None
            if self.stopped:
                raise ReadError(self.stopped)
            if ptype == fr.FILE_CAN:
                raise ReadError("The Z1 stopped the transfer (is the file still there?).")
            if ptype == want:
                return payload

    async def run(self, writer) -> bytes:
        def send(ptype, payload=b""):
            writer.write(fr.build_frame(ptype, payload))

        try:
            send(fr.FILE_START, ("download %s\n" % escape_path(self.path)).encode("utf-8"))
            await writer.drain()
            md5 = None
            for _ in range(self.retries):
                md5 = await self._next(fr.FILE_MD5)
                if md5 is not None:
                    break
                send(fr.FILE_MD5)          # ask again, as the controllers do
                await writer.drain()
            if md5 is None:
                raise ReadError("The Z1 didn't answer the request to read the file.")
            adv = md5.decode("ascii", "replace").strip().lower()
            self.md5 = adv if _HEX32.match(adv) else ""
            view = None
            for _ in range(self.retries):
                send(fr.FILE_VIEW)
                await writer.drain()
                view = await self._next(fr.FILE_VIEW)
                if view is not None and len(view) >= 4:
                    break
            if view is None or len(view) < 4:
                raise ReadError("The Z1 didn't say how long the file is.")
            self.total = int.from_bytes(view[:4], "big")
            chunks = []
            seq = 1
            tries = 0
            while seq <= self.total:
                send(fr.FILE_DATA, seq.to_bytes(4, "big"))
                await writer.drain()
                while True:
                    pk = await self._next(fr.FILE_DATA)
                    if pk is None or len(pk) < 4:
                        break
                    if int.from_bytes(pk[:4], "big") == seq:
                        break
                if pk is None or len(pk) < 4:
                    tries += 1
                    if tries > self.retries:
                        raise ReadError("The Z1 stopped sending the file part way (packet %d of %d)."
                                        % (seq, self.total))
                    continue
                tries = 0
                chunks.append(pk[4:])
                self.got = seq
                self.bytes += len(pk) - 4
                seq += 1
            send(fr.FILE_END)
            await writer.drain()
            data = b"".join(chunks)
        except ReadError:
            try:
                send(fr.FILE_CAN)
                await writer.drain()
            except Exception:
                pass
            raise
        if self.md5 and data[:2] != b"\x00\x00" and hashlib.md5(data).hexdigest() != self.md5:
            raise ReadError("The file didn't arrive whole (its checksum doesn't match). Try again.")
        log.info("read %s from the Z1: %d bytes in %d packets", self.path, len(data), self.total)
        return data

    def progress(self) -> float:
        return (self.got / float(self.total)) if self.total else 0.0
