"""Thin async client for the a74-bridged REST API."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class BridgeError(Exception):
    """Bridge returned an error."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"bridge HTTP {status}: {body}")
        self.status = status
        self.body = body


class BridgeClient:
    """Calls one bridge. All POSTs send a body so the bridge sees a Content-Length."""

    def __init__(self, session: aiohttp.ClientSession, host: str, port: int, token: str) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"
        self._headers = {"X-Auth-Token": token}

    async def _json(self, method: str, path: str, **kw: Any) -> Any:
        async with self._session.request(method, self._base + path, headers=self._headers, timeout=aiohttp.ClientTimeout(total=30), **kw) as r:
            body = await r.json(content_type=None) if r.content_length != 0 else {}
            if r.status >= 400:
                raise BridgeError(r.status, body)
            return body

    async def health(self) -> dict:
        return await self._json("GET", "/health")

    async def status(self) -> dict:
        return await self._json("GET", "/status")

    async def properties(self) -> dict:
        return await self._json("GET", "/camera/properties")

    async def set_properties(self, props: dict[str, int]) -> dict:
        return await self._json("PATCH", "/camera/properties", json=props)

    async def clock_sync(self, force: bool = False) -> dict:
        return await self._json("POST", "/clock/sync" + ("?force=1" if force else ""), data=b"")

    async def reconnect(self) -> dict:
        return await self._json("POST", "/camera/reconnect", data=b"")

    async def record(self, start: bool) -> dict:
        return await self._json("POST", f"/camera/record/{'start' if start else 'stop'}", data=b"")

    async def timecode_jam(self, fps: int = 25) -> dict:
        return await self._json("POST", f"/timecode/jam?fps={fps}", data=b"")

    async def preset_drift(self) -> dict:
        return await self._json("GET", "/preset/drift")

    async def timecode(self) -> dict:
        return await self._json("GET", "/timecode")

    async def preset_save(self, name: str | None = None) -> tuple[bytes, dict]:
        """Returns the settings file bytes and the bridge metadata (from X-Meta). `name` marks the preset active on the bridge."""
        async with self._session.post(self._base + "/preset/save", headers=self._headers, data=b"", params={"name": name} if name else None, timeout=aiohttp.ClientTimeout(total=60)) as r:
            if r.status >= 400:
                raise BridgeError(r.status, await r.json(content_type=None))
            data = await r.read()
            import json

            meta = json.loads(r.headers.get("X-Meta", "{}"))
            return data, meta

    async def preset_load(self, data: bytes, name: str) -> dict:
        async with self._session.post(
            self._base + "/preset/load", headers={**self._headers, "Content-Type": "application/octet-stream"},
            params={"name": name}, data=data, timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            body = await r.json(content_type=None)
            if r.status >= 400:
                raise BridgeError(r.status, body)
            return body

    async def job(self, job_id: str) -> dict:
        return await self._json("GET", f"/jobs/{job_id}")

    async def wait_job(self, job_id: str, timeout_s: int) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            j = await self.job(job_id)
            if j.get("state") in ("verified", "failed"):
                return j
            if loop.time() > deadline:
                j["state"] = "failed"
                j["error"] = f"HA timeout after {timeout_s} s (last state {j.get('state')})"
                return j
            await asyncio.sleep(2)
