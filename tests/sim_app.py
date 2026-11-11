"""A pretend Makera Studio: connects, polls status, downloads/uploads files, starts jobs."""
from __future__ import annotations
import asyncio, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from z1screen import framing as fr  # noqa: E402
from z1screen.status import parse_status  # noqa: E402


class SimApp:
    def __init__(self):
        self.statuses = []
        self.console = []
        self.q = asyncio.Queue()
        self.polling = True
        self.closed = False

    async def connect(self, host, port):
        self.r, self.w = await asyncio.open_connection(host, port)
        self.w.write(b"echo echo\n")          # protocol probe, like the real apps
        self.reader_task = asyncio.create_task(self._reader())
        self.poll_task = asyncio.create_task(self._poll())
        return self

    async def _reader(self):
        p = fr.FrameParser()
        lb = fr.LineBuffer()
        try:
            while True:
                d = await self.r.read(65536)
                if not d:
                    break
                for ptype, payload in p.feed(d):
                    if ptype == fr.STATUS_RES:
                        st = parse_status(payload.decode())
                        if st:
                            self.statuses.append(st)
                    elif ptype == fr.NORMAL_INFO:
                        self.console.extend(lb.feed(payload))
                    elif ptype in fr.FILE_TYPES:
                        await self.q.put((ptype, payload))
        finally:
            self.closed = True

    async def _poll(self):
        while True:
            if self.polling:
                self.w.write(fr.build_frame(fr.CTRL_SINGLE, b"?"))
                await self.w.drain()
            await asyncio.sleep(0.5)

    async def command(self, text):
        self.w.write(fr.build_frame(fr.CTRL_MULTI, text.replace(" /", " /").encode()))
        await self.w.drain()

    async def _get(self, want, timeout=5):
        while True:
            ptype, payload = await asyncio.wait_for(self.q.get(), timeout)
            if ptype in want:
                return ptype, payload

    async def download(self, path):
        self.polling = False
        await asyncio.sleep(0.6)
        self.w.write(fr.build_frame(fr.FILE_START, ("download %s\n" % path.replace(" ", "\x01")).encode()))
        await self._get({fr.FILE_MD5})
        self.w.write(fr.build_frame(fr.FILE_VIEW, b""))
        _, payload = await self._get({fr.FILE_VIEW})
        total = int.from_bytes(payload[:4], "big")
        data = bytearray()
        for seq in range(1, total + 1):
            self.w.write(fr.build_frame(fr.FILE_DATA, seq.to_bytes(4, "big")))
            _, payload = await self._get({fr.FILE_DATA})
            data += payload[4:]
        self.w.write(fr.build_frame(fr.FILE_END, b""))
        await self.w.drain()
        self.polling = True
        return bytes(data)

    async def upload(self, path, data, packet=8192):
        self.polling = False
        await asyncio.sleep(0.6)
        self.w.write(fr.build_frame(fr.FILE_START, ("upload %s\n" % path.replace(" ", "\x01")).encode()))
        self.w.write(fr.build_frame(fr.FILE_MD5, b"0" * 32))
        await self._get({fr.FILE_VIEW})
        n = (len(data) + packet - 1) // packet
        self.w.write(fr.build_frame(fr.FILE_VIEW, n.to_bytes(4, "big") + packet.to_bytes(2, "big")))
        while True:
            ptype, payload = await self._get({fr.FILE_DATA, fr.FILE_END})
            if ptype == fr.FILE_END:
                break
            seq = int.from_bytes(payload[:4], "big")
            self.w.write(fr.build_frame(fr.FILE_DATA, seq.to_bytes(4, "big") + data[(seq - 1) * packet: seq * packet]))
        self.polling = True

    async def close(self):
        self.poll_task.cancel()
        self.w.close()
        await asyncio.sleep(0.1)
