"""RØDECaster Video client: OSC 1.0 over TCP 10024 with a little-endian length prefix and
zero-length heartbeat frames. Push-based: the console dumps its state after `/show` and then
streams every change. Protocol per miquelbaixas/rodecaster-video-linux and the Companion module."""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)
RCV_PORT = 10024
KEEPALIVE = struct.pack("<I", 0)
# The console emits a zero-length keepalive frame about every 1.1 s even when idle, so a stretch
# of total silence means the link is dead. A hard disconnect (power/network loss, no TCP FIN)
# otherwise leaves the socket half-open and `connected` stuck True for minutes.
RX_TIMEOUT = 5.0


def _enable_tcp_keepalive(writer: asyncio.StreamWriter) -> None:
    """Belt-and-suspenders to the app-level RX watchdog: let the OS probe a silent peer and drop a
    half-open socket, so a write/drain also fails promptly instead of blocking on TCP retransmits."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for opt, val in (("TCP_KEEPIDLE", 3), ("TCP_KEEPINTVL", 2), ("TCP_KEEPCNT", 3)):
            n = getattr(socket, opt, None)
            if n is not None:
                sock.setsockopt(socket.IPPROTO_TCP, n, val)
    except OSError as e:  # noqa: BLE001
        _LOGGER.debug("RCV: could not set TCP keepalive: %s", e)


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4) if len(b) % 4 else b


def pack(address: str, *args: tuple[str, Any]) -> bytes:
    tags = "," + "".join(t for t, _ in args)
    body = _pad(address.encode() + b"\0") + _pad(tags.encode() + b"\0")
    for t, v in args:
        if t == "i":
            body += struct.pack(">i", int(v))
        elif t == "f":
            body += struct.pack(">f", float(v))
        elif t == "s":
            body += _pad(str(v).encode() + b"\0")
    return struct.pack("<I", len(body)) + body


def _str(b: bytes, off: int) -> tuple[str, int]:
    end = b.index(b"\0", off)
    s = b[off:end].decode(errors="replace")
    off = end + 1
    off += (-off) % 4
    return s, off


def unpack(body: bytes) -> tuple[str, list]:
    if body.startswith(b"#bundle"):
        out, off = [], 16
        while off + 4 <= len(body):
            n = struct.unpack(">I", body[off:off + 4])[0]
            out.append(unpack(body[off + 4:off + 4 + n]))
            off += 4 + n
        return "#bundle", out
    addr, off = _str(body, 0)
    if off >= len(body) or body[off:off + 1] != b",":
        return addr, []
    tags, off = _str(body, off)
    vals: list = []
    for t in tags[1:]:
        if t == "i":
            vals.append(struct.unpack(">i", body[off:off + 4])[0]); off += 4
        elif t == "f":
            vals.append(struct.unpack(">f", body[off:off + 4])[0]); off += 4
        elif t == "s":
            s, off = _str(body, off); vals.append(s)
        elif t == "b":
            n = struct.unpack(">I", body[off:off + 4])[0]; vals.append(body[off + 4:off + 4 + n]); off += 4 + n + (-n) % 4
        elif t in "TFN":
            vals.append({"T": True, "F": False, "N": None}[t])
        else:
            break
    return addr, vals


class RCVClient:
    """Keeps one connection to the console, tracks state and notifies listeners."""

    def __init__(self, host: str, port: int = RCV_PORT) -> None:
        self.host, self.port = host, port
        self.state: dict[str, list] = {}
        self.connected = False
        self._listeners: list[Callable[[], None]] = []
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._stop = False
        self._lock = asyncio.Lock()

    # ---- public ----
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def start(self) -> None:
        self._stop = False
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._writer:
            self._writer.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def send(self, address: str, *args: tuple[str, Any]) -> None:
        if not self._writer or self._writer.is_closing():
            raise ConnectionError("console not connected")
        async with self._lock:
            self._writer.write(pack(address, *args))
            await self._writer.drain()

    async def set_record(self, on: bool) -> None:
        await self.send("/show/record", ("i", 1 if on else 0))

    async def press_button(self, button_id: int) -> None:
        await self.send("/device/button", ("i", button_id), ("i", 1))
        await asyncio.sleep(0.1)
        await self.send("/device/button", ("i", button_id), ("i", 0))

    # ---- derived state ----
    @property
    def recording(self) -> bool | None:
        v = self.state.get("/show/record")
        return None if not v else bool(v[0])

    @property
    def record_enabled(self) -> bool | None:
        v = self.state.get("/show/recordEnabled")
        return None if not v else bool(v[0])

    @property
    def live(self) -> bool | None:
        v = self.state.get("/show/live")
        return None if not v else bool(v[0])

    def video_in_state(self, n: int) -> str | None:
        v = self.state.get(f"/videoIn/{n}/state")
        return v[0] if v else None

    def video_in_meta(self, n: int) -> dict:
        v = self.state.get(f"/videoIn/{n}/metadata")
        if not v or not isinstance(v[0], str):
            return {}
        try:
            import json
            return json.loads(v[0])
        except ValueError:
            return {}

    @property
    def program(self) -> str | None:
        v = self.state.get("/show/pgmcurrent") or self.state.get("/show/current")
        return f"{v[0]} {v[1]}" if v and len(v) >= 2 else None

    def disk(self, n: int) -> dict | None:
        v = self.state.get(f"/diskStatus/{n}/disk")
        if not v or len(v) < 4:
            return None
        try:
            remain, used = int(v[2]), int(v[3])
        except (TypeError, ValueError):
            return None
        return {"type": v[0], "name": v[1], "remaining_bytes": remain, "used_bytes": used}

    # ---- connection loop ----
    async def _run(self) -> None:
        backoff = 2
        while not self._stop:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 10)
                _enable_tcp_keepalive(writer)
                self._writer = writer
                self.connected = True
                backoff = 2
                _LOGGER.info("RCV %s connected", self.host)
                self._notify()
                writer.write(pack("/show") + pack("/device/refresh"))
                await writer.drain()
                await self._read_loop(reader, writer)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("RCV %s: %s", self.host, e)
            finally:
                self.connected = False
                self._writer = None
                self._notify()
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _read_loop(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buf = b""
        loop = asyncio.get_running_loop()
        last_ka = loop.time()
        last_rx = loop.time()
        while not self._stop:
            try:
                chunk = await asyncio.wait_for(reader.read(1 << 16), 1.0)
                if not chunk:
                    raise ConnectionError("console closed the connection")
                buf += chunk
                last_rx = loop.time()
            except asyncio.TimeoutError:
                pass
            now = asyncio.get_running_loop().time()
            if now - last_rx > RX_TIMEOUT:
                raise ConnectionError(f"no data from console for {now - last_rx:.1f}s (link dead)")
            if now - last_ka > 1.5:
                writer.write(KEEPALIVE)
                await writer.drain()
                last_ka = now
            changed = False
            while len(buf) >= 4:
                n = struct.unpack("<I", buf[:4])[0]
                if n == 0:
                    buf = buf[4:]
                    continue
                if n > 50_000_000:
                    raise ConnectionError(f"bad frame length {n}")
                if len(buf) < 4 + n:
                    break
                body, buf = buf[4:4 + n], buf[4 + n:]
                try:
                    addr, vals = unpack(body)
                except Exception:  # noqa: BLE001
                    continue
                if addr == "#bundle":
                    for a, v in vals:
                        self.state[a] = v
                else:
                    self.state[addr] = vals
                changed = True
            if changed:
                self._notify()

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("RCV listener failed")
