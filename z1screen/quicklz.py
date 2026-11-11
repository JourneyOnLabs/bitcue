"""Pure-Python QuickLZ 1.5 (level 3, no streaming buffer) decompression.

Makera's apps may send G-code to and from the machine compressed. The format
(see compress_file/decompress_file in the Carvera Community Controller) is a
series of blocks, each a 4-byte big-endian length followed by one QuickLZ
block, then a 2-byte big-endian sum of all decompressed bytes. The Z1 firmware
builds QuickLZ with level 3 and no streaming buffer (player/quicklz.h).

This is a line-by-line port of qlz_decompress_core() for level 3 from the
QuickLZ 1.5.0 C source, so the Pi needs no compiler or extra packages.
"""

from __future__ import annotations

_BITLUT = (4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0)


def _le(buf, pos: int, n: int) -> int:
    chunk = buf[pos:pos + n]
    if len(chunk) < n:
        chunk = bytes(chunk) + b"\x00" * (n - len(chunk))
    return int.from_bytes(chunk, "little")


def decompress_block(src: bytes) -> bytes:
    """Decompress one QuickLZ block (header included)."""
    if not src:
        return b""
    flags = src[0]
    n = 4 if flags & 2 else 1
    dsiz = _le(src, 1 + n, n)
    hl = 2 * n + 1
    if not flags & 1:
        return bytes(src[hl:hl + dsiz])
    if (flags >> 2) & 3 != 3:
        raise ValueError("QuickLZ level %d is not supported" % ((flags >> 2) & 3))
    s = hl
    n_src = len(src)
    out = bytearray()
    last_dst = dsiz - 1
    last_matchstart = last_dst - 6 - 4
    cword = 1
    while True:
        if s >= n_src or len(out) > dsiz:
            raise ValueError("corrupt or truncated QuickLZ data")
        if cword == 1:
            cword = _le(src, s, 4)
            s += 4
        fetch = _le(src, s, 4)
        if cword & 1:
            cword >>= 1
            if fetch & 3 == 0:
                off = (fetch & 0xFF) >> 2
                ml = 3
                s += 1
            elif fetch & 2 == 0:
                off = (fetch & 0xFFFF) >> 2
                ml = 3
                s += 2
            elif fetch & 1 == 0:
                off = (fetch & 0xFFFF) >> 6
                ml = ((fetch >> 2) & 15) + 3
                s += 2
            elif fetch & 127 != 3:
                off = (fetch >> 7) & 0x1FFFF
                ml = ((fetch >> 2) & 0x1F) + 2
                s += 3
            else:
                off = fetch >> 15
                ml = ((fetch >> 7) & 255) + 3
                s += 4
            start = len(out) - off
            if start < 0 or off == 0:
                raise ValueError("corrupt QuickLZ data")
            if off >= ml:
                out += out[start:start + ml]
            else:
                for k in range(ml):
                    out.append(out[start + k])
        else:
            if len(out) < last_matchstart:
                k = _BITLUT[cword & 0xF]
                if s + k > n_src:
                    raise ValueError("truncated QuickLZ data")
                out += src[s:s + k]
                s += k
                cword >>= k
            else:
                while len(out) <= last_dst:
                    if cword == 1:
                        s += 4
                        cword = 1 << 31
                    if s >= len(src):
                        raise ValueError("truncated QuickLZ data")
                    out.append(src[s])
                    s += 1
                    cword >>= 1
                if len(out) != dsiz:
                    raise ValueError("QuickLZ size mismatch")
                return bytes(out)


def looks_compressed(data: bytes) -> bool:
    """Makera's container starts with a 4-byte length well under 64 KiB."""
    return len(data) >= 9 and data[0] == 0 and data[1] == 0


def decompress_container(data: bytes, check_sum: bool = True) -> bytes:
    """Undo Makera's block container: [len32][qlz block]... [sum16]."""
    out = bytearray()
    pos = 0
    end = len(data) - 2
    while pos < end:
        blen = int.from_bytes(data[pos:pos + 4], "big")
        pos += 4
        if blen <= 0 or pos + blen > end:
            raise ValueError("bad block length in compressed file")
        out += decompress_block(data[pos:pos + blen])
        pos += blen
    if check_sum:
        want = int.from_bytes(data[-2:], "big")
        if (sum(out) & 0xFFFF) != want:
            raise ValueError("checksum mismatch after decompression")
    return bytes(out)
