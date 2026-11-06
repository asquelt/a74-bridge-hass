"""Studio hub: preset library on /share/camera_presets and cross-camera sync, shared by all bridges."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import BridgeError
from .const import EVENT_PRESET_APPLIED, EVENT_PRESET_FAILED, LOAD_TIMEOUT_S, PRESET_DIR

if TYPE_CHECKING:
    from . import A74Coordinator

_LOGGER = logging.getLogger(__name__)

# properties copied by "sync parameters" (no camera reboot); raw SDK values, names as the bridge knows them
SYNC_PARAMETERS = [
    "ExposureProgramMode", "IsoSensitivity", "FNumber", "ShutterSpeed", "ExposureBiasCompensation",
    "WhiteBalance", "ColorTemp", "PictureProfile", "CreativeLook", "Movie_File_Format", "Movie_Recording_Setting",
    "Movie_Recording_FrameRateSetting", "FocusMode", "SilentMode", "AudioRecording", "AudioInputMasterLevel",
]
# bound to physical dials / only valid in some modes on the A7 IV: a rejection here is expected, not a failure
SYNC_OPTIONAL = {"ExposureProgramMode", "ExposureBiasCompensation", "ColorTemp"}


def safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]+", "_", name.strip()).strip(" .")
    if not s:
        raise HomeAssistantError("preset name must contain letters or digits")
    return s


def default_name() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def preset_path(preset: str, camera_id: str) -> str:
    return os.path.join(PRESET_DIR, safe_name(preset), f"{camera_id}.DAT")


def list_presets() -> list[dict]:
    """Folders under PRESET_DIR with their metadata, newest first."""
    out = []
    if not os.path.isdir(PRESET_DIR):
        return out
    for name in os.listdir(PRESET_DIR):
        p = os.path.join(PRESET_DIR, name)
        if not os.path.isdir(p):
            continue
        cams = sorted(f[:-4] for f in os.listdir(p) if f.endswith(".DAT"))
        meta = {}
        mp = os.path.join(p, "preset.json")
        if os.path.exists(mp):
            try:
                meta = json.load(open(mp, encoding="utf-8"))
            except ValueError:
                meta = {}
        out.append({"name": name, "cameras": cams, "mtime": os.path.getmtime(p), "created_at": meta.get("created_at"), "firmware": meta.get("firmware"), "note": meta.get("note")})
    out.sort(key=lambda d: d["mtime"], reverse=True)
    return out


class Studio:
    """Shared state for the studio hub entities (one per HA instance)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.presets: list[dict] = []
        self.selected: str | None = None
        self.preset_name: str = ""
        self.last_action: dict = {}
        self.busy = False
        self._listeners: list = []

    def add_listener(self, cb):
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    async def refresh(self) -> None:
        self.presets = await self.hass.async_add_executor_job(list_presets)
        names = [p["name"] for p in self.presets]
        if self.selected not in names:
            self.selected = names[0] if names else None
        self.notify()

    def coordinators(self) -> list[A74Coordinator]:
        from . import DOMAIN, is_console  # noqa: PLC0415
        from homeassistant.config_entries import ConfigEntryState  # noqa: PLC0415
        return [e.runtime_data for e in self.hass.config_entries.async_entries(DOMAIN)
                if e.state is ConfigEntryState.LOADED and not is_console(e) and hasattr(e, "runtime_data") and hasattr(e.runtime_data, "client")]

    def _recording(self, cos) -> list[str]:
        return [co.camera_id for co in cos if (co.data or {}).get("camera", {}).get("recording")]

    def _set_action(self, **kw) -> None:
        self.last_action = {"at": datetime.now().isoformat(timespec="seconds"), **kw}
        self.notify()

    # ---- operations ----
    async def save(self, name: str | None, cos=None, note: str | None = None) -> dict:
        cos = cos or self.coordinators()
        preset = safe_name(name) if name else default_name()
        results, firmware = {}, None
        for co in cos:
            try:
                data, meta = await co.client.preset_save()
            except BridgeError as e:
                results[co.camera_id] = {"ok": False, "error": e.body}
                continue
            except Exception as e:  # noqa: BLE001
                results[co.camera_id] = {"ok": False, "error": str(e)}
                continue
            path = preset_path(preset, co.camera_id)
            firmware = meta.get("firmware") or firmware

            def _write(path=path, data=data, meta=meta):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
                with open(path + ".json", "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=1)

            await self.hass.async_add_executor_job(_write)
            results[co.camera_id] = {"ok": True, "sha256": meta.get("sha256"), "firmware": meta.get("firmware")}
        if any(r.get("ok") for r in results.values()):
            pm = os.path.join(PRESET_DIR, preset, "preset.json")
            await self.hass.async_add_executor_job(lambda: json.dump({"created_at": datetime.now().isoformat(timespec="seconds"), "firmware": firmware, "note": note, "cameras": sorted(results)}, open(pm, "w", encoding="utf-8"), indent=1))
        self.selected = preset
        await self.refresh()
        self._set_action(action="save", preset=preset, cameras=results, ok=all(r.get("ok") for r in results.values()))
        return {"preset": preset, "cameras": results}

    async def load(self, preset: str, cos=None) -> dict:
        cos = cos or self.coordinators()
        preset = safe_name(preset)
        rec = self._recording(cos)
        if rec:
            raise HomeAssistantError(f"recording on {', '.join(rec)}; preset load refused")
        self.busy = True
        self.notify()
        results = {}
        try:
            async def one(co):
                path = preset_path(preset, co.camera_id)
                if not os.path.exists(path):
                    return co.camera_id, {"state": "failed", "error": f"no file for {co.camera_id} in preset {preset}"}
                data = await self.hass.async_add_executor_job(lambda p=path: open(p, "rb").read())
                try:
                    started = await co.client.preset_load(data, preset)
                    job = await co.client.wait_job(started["job_id"], LOAD_TIMEOUT_S)
                except BridgeError as e:
                    job = {"state": "failed", "error": e.body}
                except Exception as e:  # noqa: BLE001
                    job = {"state": "failed", "error": str(e)}
                self.hass.bus.async_fire(EVENT_PRESET_APPLIED if job.get("state") == "verified" else EVENT_PRESET_FAILED,
                                         {"camera_id": co.camera_id, "serial": co.serial, "preset": preset, "job": job})
                await co.async_request_refresh()
                return co.camera_id, job

            for cam, job in await asyncio.gather(*(one(co) for co in cos)):
                results[cam] = job
        finally:
            self.busy = False
        self._set_action(action="load", preset=preset, cameras=results, ok=all(j.get("state") == "verified" for j in results.values()))
        return {"preset": preset, "cameras": results}

    async def delete(self, preset: str) -> None:
        p = os.path.join(PRESET_DIR, safe_name(preset))
        if os.path.isdir(p):
            await self.hass.async_add_executor_job(shutil.rmtree, p)
        await self.refresh()
        self._set_action(action="delete", preset=preset, ok=True)

    async def sync_parameters(self, source_id: str, targets=None) -> dict:
        cos = self.coordinators()
        src = next((co for co in cos if co.camera_id == source_id), None)
        if not src:
            raise HomeAssistantError(f"source camera {source_id} not found")
        targets = targets or [co for co in cos if co.camera_id != source_id]
        props = await src.client.properties()
        values = {k: props[k]["value"] for k in SYNC_PARAMETERS if k in props and isinstance(props[k].get("value"), int)}
        results = {}
        for co in targets:
            try:
                r = await co.client.set_properties(values)
                rej = r.get("rejected") or {}
                r["skipped"] = {k: v for k, v in rej.items() if k in SYNC_OPTIONAL}
                r["rejected"] = {k: v for k, v in rej.items() if k not in SYNC_OPTIONAL}
                results[co.camera_id] = r
            except BridgeError as e:
                results[co.camera_id] = {"error": e.body}
            except Exception as e:  # noqa: BLE001
                results[co.camera_id] = {"error": str(e)}
        ok = all(not r.get("rejected") and not r.get("error") for r in results.values())
        self._set_action(action="sync_parameters", source=source_id, values=values, cameras=results, ok=ok,
                         applied=sum(len(r.get("applied") or {}) for r in results.values()),
                         skipped=sorted({k for r in results.values() for k in (r.get("skipped") or {})}))
        return {"source": source_id, "values": values, "cameras": results, "ok": ok}

    async def sync_full(self, source_id: str, targets=None) -> dict:
        cos = self.coordinators()
        src = next((co for co in cos if co.camera_id == source_id), None)
        if not src:
            raise HomeAssistantError(f"source camera {source_id} not found")
        targets = targets or [co for co in cos if co.camera_id != source_id]
        rec = self._recording(cos)
        if rec:
            raise HomeAssistantError(f"recording on {', '.join(rec)}; full sync refused")
        data, meta = await src.client.preset_save()
        preset = f"sync-from-{source_id}-{default_name()}"
        # keep a copy of the source file for every camera in the preset folder so it can be reloaded later
        for co in [src, *targets]:
            path = preset_path(preset, co.camera_id)

            def _write(path=path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)

            await self.hass.async_add_executor_job(_write)
        result = await self.load(preset, targets)
        result["source"] = source_id
        result["sha256"] = meta.get("sha256")
        return result
