"""Makera framed protocol used by the Z1 over Wi-Fi (TCP port 2222).

Wire layout, big-endian:
    0x86 0x68 | length (2) | type (1) | payload (N) | crc16 (2) | 0x55 0xAA
length = 1 + N + 2, and the CRC (CRC-16/XMODEM, poly 0x1021, init 0) covers
the two length bytes, the type byte and the payload.

The constants and layout match Makera's published Z1 motion firmware
(src/libs/PublicData.h, SerialConsole::PacketMessage) and the open-source
Carvera Community Controller (carveracontroller/protocols/framing.py).
"""

from __future__ import annotations

import binascii

HEADER = b"\x86\x68"
FOOTER = b"\x55\xaa"
MAX_LEN = 8200

CTRL_SINGLE = 0xA1   # one realtime byte, for example '?'
CTRL_MULTI = 0xA2    # a text command, for example "play /sd/gcodes/x.nc"
FILE_START = 0xB0    # "upload <path>" or "download <path>"
FILE_MD5 = 0xB1
FILE_VIEW = 0xB2
FILE_DATA = 0xB3
FILE_END = 0xB4
FILE_CAN = 0xB5
FILE_RETRY = 0xB6
FIRM_VER = 0x71
STATUS_RES = 0x81    # "<Run|MPos:...|T:..|P:...>"
DIAG_RES = 0x82
LOAD_INFO = 0x83
LOAD_FINISH = 0x84
LOAD_ERROR = 0x85
NORMAL_INFO = 0x90   # console text, possibly split across frames

FILE_TYPES = frozenset({FILE_START, FILE_MD5, FILE_VIEW, FILE_DATA, FILE_END, FILE_CAN, FILE_RETRY})


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM, the same table-driven CRC the firmware uses."""
    return binascii.crc_hqx(data, 0)


def build_frame(ptype: int, payload: bytes = b"") -> bytes:
    payload = bytes(payload)
    length = 1 + len(payload) + 2
    body = length.to_bytes(2, "big") + bytes([ptype]) + payload
    return HEADER + body + crc16(body).to_bytes(2, "big") + FOOTER


class FrameParser:
    """Incremental frame parser for one direction of a TCP stream.

    feed() returns a list of (ptype, payload) tuples. Bytes that are not part
    of a valid frame are counted in ``stray`` so a caller can tell whether the
    peer speaks the framed protocol at all.
    """

    def __init__(self, check_crc: bool = True) -> None:
        self.buf = bytearray()
        self.check_crc = check_crc
        self.frames = 0
        self.bad = 0
        self.stray = 0

    def idle(self) -> bool:
        """True when no partial frame is waiting (safe to inject a frame)."""
        return len(self.buf) == 0

    def reset(self) -> None:
        self.buf.clear()

    def feed(self, data: bytes) -> list:
        buf = self.buf
        buf += data
        out = []
        i = 0
        n = len(buf)
        while i < n:
            j = buf.find(HEADER, i)
            if j < 0:
                # keep a trailing 0x86 that may start the next header
                keep = n - 1 if buf[n - 1] == HEADER[0] else n
                self.stray += keep - i
                i = keep
                break
            if j > i:
                self.stray += j - i
            if n - j < 4:
                i = j
                break
            length = (buf[j + 2] << 8) | buf[j + 3]
            if length < 3 or length > MAX_LEN:
                self.stray += 1
                i = j + 1
                continue
            end = j + 4 + length + 2
            if n < end:
                i = j
                break
            if buf[end - 2] != FOOTER[0] or buf[end - 1] != FOOTER[1]:
                self.bad += 1
                self.stray += 1
                i = j + 1
                continue
            body_end = j + 2 + length
            if self.check_crc:
                crc = (buf[body_end] << 8) | buf[body_end + 1]
                if crc16(bytes(buf[j + 2:body_end])) != crc:
                    self.bad += 1
                    self.stray += 1
                    i = j + 1
                    continue
            ptype = buf[j + 4]
            payload = bytes(buf[j + 5:body_end])
            out.append((ptype, payload))
            self.frames += 1
            i = end
        if i:
            del buf[:i]
        if len(buf) > 4 * (MAX_LEN + 8):
            # never let garbage grow without bound
            self.stray += len(buf) - (MAX_LEN + 8)
            del buf[:len(buf) - (MAX_LEN + 8)]
        return out


class LineBuffer:
    """Joins console text fragments into whole lines."""

    def __init__(self, limit: int = 4096) -> None:
        self.part = bytearray()
        self.limit = limit

    def feed(self, data: bytes) -> list:
        lines = []
        start = 0
        while True:
            k = data.find(b"\n", start)
            if k < 0:
                self.part += data[start:]
                if len(self.part) > self.limit:
                    del self.part[:len(self.part) - self.limit]
                break
            self.part += data[start:k]
            lines.append(self.part.decode("utf-8", "replace").rstrip("\r"))
            self.part = bytearray()
            start = k + 1
        return lines
