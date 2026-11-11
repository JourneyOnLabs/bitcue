"""Copy job files as they pass through the relay.

When Makera Studio uploads a file to the Z1, or downloads one to show its
preview (it does this when you press Select in File Storage), the file travels
as framed packets. This module watches both directions and rebuilds the file,
so the screen knows the bits and timing of the job without asking the machine
for anything.

Transfer sequence (Makera framed transfer, see XMODEM.send/recv in the Carvera
Community Controller):
  download: app FILE_START "download <path>" -> machine FILE_MD5 -> app FILE_VIEW
            -> machine FILE_VIEW <total packets> -> app FILE_DATA <seq>
            -> machine FILE_DATA <seq><data> ... -> app FILE_END
  upload:   app FILE_START "upload <path>" -> app FILE_MD5 <md5> -> machine FILE_VIEW
            -> app FILE_VIEW <packets><size> -> machine FILE_DATA <seq>
            -> app FILE_DATA <seq><data> ... -> machine FILE_END
"""

from __future__ import annotations

import logging
import time

from . import framing as fr

log = logging.getLogger("z1screen.capture")


def normalize_path(raw: str) -> str:
    """Turn a path as sent on the wire into a plain path."""
    p = raw.replace("\x01", " ").strip().strip('"').strip()
    if p.endswith(".lz"):
        p = p[:-3]
    return p


class TransferCapture:
    def __init__(self, on_complete) -> None:
        self.on_complete = on_complete
        self.cur = None
        self.done = 0
        self.dropped = 0

    def _start(self, kind: str, path: str) -> None:
        self.cur = {"kind": kind, "path": path, "chunks": {}, "total": None, "t": time.time(), "last": time.time()}
        log.info("transfer started: %s %s", kind, path)

    def _drop(self, why: str) -> None:
        if self.cur:
            log.info("transfer dropped (%s): %s", why, self.cur["path"])
            self.dropped += 1
        self.cur = None

    def _finish(self) -> None:
        cur = self.cur
        self.cur = None
        if not cur:
            return
        chunks = cur["chunks"]
        total = cur["total"] or (max(chunks) if chunks else 0)
        if total <= 0 or any(i not in chunks for i in range(1, total + 1)):
            log.info("transfer incomplete, not kept: %s (%d of %s packets)", cur["path"], len(chunks), total)
            self.dropped += 1
            return
        data = b"".join(chunks[i] for i in range(1, total + 1))
        self.done += 1
        log.info("transfer complete: %s %s, %d bytes", cur["kind"], cur["path"], len(data))
        try:
            self.on_complete(cur["kind"], cur["path"], data)
        except Exception:  # never let a capture problem touch the relay
            log.exception("could not store captured file")

    # frames sent by the app (Makera Studio) to the machine
    def client_frame(self, ptype: int, payload: bytes) -> None:
        if self.cur:
            self.cur["last"] = time.time()
        if ptype == fr.FILE_START:
            text = payload.decode("utf-8", "replace").strip()
            cmd, _, rest = text.partition(" ")
            if cmd in ("upload", "download") and rest.strip():
                self._start(cmd, normalize_path(rest))
            else:
                self.cur = None
            return
        cur = self.cur
        if not cur:
            return
        if ptype == fr.FILE_CAN:
            self._drop("cancelled by the app")
        elif cur["kind"] == "upload":
            if ptype == fr.FILE_VIEW and len(payload) >= 4:
                cur["total"] = int.from_bytes(payload[:4], "big")
            elif ptype == fr.FILE_DATA and len(payload) >= 4:
                cur["chunks"][int.from_bytes(payload[:4], "big")] = payload[4:]
        else:
            if ptype == fr.FILE_END:
                self._finish()

    # frames sent by the machine to the app
    def machine_frame(self, ptype: int, payload: bytes) -> None:
        if self.cur:
            self.cur["last"] = time.time()
        cur = self.cur
        if not cur:
            return
        if ptype == fr.FILE_CAN:
            self._drop("cancelled by the machine")
        elif cur["kind"] == "download":
            if ptype == fr.FILE_VIEW and len(payload) >= 4:
                cur["total"] = int.from_bytes(payload[:4], "big")
            elif ptype == fr.FILE_DATA and len(payload) >= 4:
                cur["chunks"][int.from_bytes(payload[:4], "big")] = payload[4:]
        else:
            if ptype == fr.FILE_END:
                self._finish()

    def active(self) -> bool:
        if self.cur and time.time() - self.cur["last"] > 120:
            self._drop("no packets for 2 minutes")
        return self.cur is not None
