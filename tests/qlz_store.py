"""Makera's QuickLZ container for the pretend Z1, with every block stored as is (not compressed).

The container is [4-byte big-endian length][QuickLZ block]... [2-byte big-endian sum of the plain
bytes]. A stored QuickLZ block is a 9-byte header (flags 0x4E: 4-byte sizes, level 3, not compressed;
then the block's size and the plain size, both little-endian) followed by the plain bytes, which is
what QuickLZ itself writes for data it can't make smaller. The decoder's handling of real compressed
blocks is checked against files made with the Z1 firmware's own QuickLZ 1.5.0 (fixtures/qlz,
test_units.py).
"""
import struct


def block(raw: bytes) -> bytes:
    return bytes([0x4E]) + struct.pack("<II", 9 + len(raw), len(raw)) + raw


def container(data: bytes, block_size: int = 4096) -> bytes:
    out = bytearray()
    for i in range(0, len(data), block_size):
        b = block(data[i:i + block_size])
        out += struct.pack(">I", len(b)) + b
    out += struct.pack(">H", sum(data) & 0xFFFF)
    return bytes(out)
