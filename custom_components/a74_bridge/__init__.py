"""A74 Bridge: one Sony A7 IV behind a Raspberry Pi bridge, polled over REST."""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BridgeClient, BridgeError
from .const import (
    CONF_TOKEN, DOMAIN, EVENT_CAMERA_DISCONNECTED, EVENT_POWER_LOSS, EVENT_PRESET_APPLIED, EVENT_PRESET_FAILED,
    LOAD_TIMEOUT_S, PRESET_DIR, SCAN_INTERVAL_S, SERVICE_CLOCK_SYNC, SERVICE_PRESET_LOAD, SERVICE_PRESET_SAVE,
    SERVICE_RECORD, SERVICE_SET_PROPERTIES,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
type A74ConfigEntry = ConfigEntry[A74Coordinator]


class A74Coordinator(DataUpdateCoordinator[dict]):
    """Polls GET /status of one bridge."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BridgeClient) -> None:
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {entry.title}", update_interval=timedelta(seconds=SCAN_INTERVAL_S))
        self.client = client
        self.entry = entry
        self.serial: str = entry.unique_id or ""
        self.camera_id: str = entry.title
        self._was_connected: bool | None = None
        self._power_loss_seen = False

    async def _async_update_data(self) -> dict:
        try:
            data = await self.client.status()
        except BridgeError as e:
            raise UpdateFailed(str(e)) from e
        except Exception as e:  # noqa: BLE001 - network errors of all kinds
            raise UpdateFailed(f"bridge unreachable: {e}") from e
        cam = data.get("camera", {})
        self.camera_id = data.get("bridge", {}).get("camera_id", self.camera_id)
        connected = bool(cam.get("connected"))
        if self._was_connected and not connected and not data.get("load_in_progress"):
            self.hass.bus.async_fire(EVENT_CAMERA_DISCONNECTED, {"camera_id": self.camera_id, "serial": self.serial, "usb": data.get("usb")})
        self._was_connected = connected
        reset = bool(data.get("clock", {}).get("power_loss_reset"))
        if reset and not self._power_loss_seen:
            self.hass.bus.async_fire(EVENT_POWER_LOSS, {"camera_id": self.camera_id, "serial": self.serial, "clock": data.get("clock")})
        self._power_loss_seen = reset
        return data

    @property
    def device_info(self) -> dr.DeviceInfo:
        cam = (self.data or {}).get("camera", {})
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.serial)}, name=self.camera_id, manufacturer="Sony / a74-bridge",
            model=cam.get("model") or "ILCE-7M4", serial_number=self.serial, sw_version=cam.get("firmware") or None,
            configuration_url=f"http://{self.entry.data[CONF_HOST]}:{self.entry.data[CONF_PORT]}/status",
        )


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry) -> bool:
    client = BridgeClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN])
    coordinator = A74Coordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: A74ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


# ---------------------------------------------------------------- services
def _coordinators_for(hass: HomeAssistant, call: ServiceCall) -> list[A74Coordinator]:
    """Resolve device_id list (or all entries when none given)."""
    device_ids = call.data.get("device_id") or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if not device_ids:
        return [e.runtime_data for e in entries]
    reg = dr.async_get(hass)
    out = []
    for did in device_ids:
        dev = reg.async_get(did)
        if not dev:
            raise HomeAssistantError(f"unknown device {did}")
        for e in entries:
            if e.entry_id in dev.config_entries:
                out.append(e.runtime_data)
    if not out:
        raise HomeAssistantError("no a74_bridge device in the target")
    return out


def _preset_path(preset: str, camera_id: str) -> str:
    safe = "".join(c for c in preset if c.isalnum() or c in "-_.")
    if not safe:
        raise HomeAssistantError("preset name must contain letters or digits")
    return os.path.join(PRESET_DIR, safe, f"{camera_id}.DAT")


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_PRESET_SAVE):
        return

    async def preset_save(call: ServiceCall) -> ServiceResponse:
        preset = call.data["preset"]
        results = {}
        for co in _coordinators_for(hass, call):
            try:
                data, meta = await co.client.preset_save()
            except BridgeError as e:
                results[co.camera_id] = {"ok": False, "error": e.body}
                continue
            path = _preset_path(preset, co.camera_id)

            def _write() -> None:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
                with open(path + ".json", "w", encoding="utf-8") as f:
                    json.dump(meta, f, indent=1)

            await hass.async_add_executor_job(_write)
            results[co.camera_id] = {"ok": True, "path": path, "sha256": meta.get("sha256"), "firmware": meta.get("firmware"), "size": len(data)}
        return {"preset": preset, "cameras": results}

    async def preset_load(call: ServiceCall) -> ServiceResponse:
        preset = call.data["preset"]
        results = {}
        for co in _coordinators_for(hass, call):
            path = _preset_path(preset, co.camera_id)
            if not os.path.exists(path):
                results[co.camera_id] = {"state": "failed", "error": f"no file {path}"}
                continue
            data = await hass.async_add_executor_job(lambda p=path: open(p, "rb").read())
            try:
                started = await co.client.preset_load(data, preset)
                job = await co.client.wait_job(started["job_id"], LOAD_TIMEOUT_S)
            except BridgeError as e:
                job = {"state": "failed", "error": e.body}
            results[co.camera_id] = job
            event = EVENT_PRESET_APPLIED if job.get("state") == "verified" else EVENT_PRESET_FAILED
            hass.bus.async_fire(event, {"camera_id": co.camera_id, "serial": co.serial, "preset": preset, "job": job})
            await co.async_request_refresh()
        return {"preset": preset, "cameras": results}

    async def clock_sync(call: ServiceCall) -> ServiceResponse:
        out = {}
        for co in _coordinators_for(hass, call):
            try:
                out[co.camera_id] = await co.client.clock_sync(bool(call.data.get("force")))
            except BridgeError as e:
                out[co.camera_id] = {"error": e.body}
        return out

    async def set_properties(call: ServiceCall) -> ServiceResponse:
        out = {}
        for co in _coordinators_for(hass, call):
            try:
                out[co.camera_id] = await co.client.set_properties(dict(call.data["properties"]))
            except BridgeError as e:
                out[co.camera_id] = {"error": e.body}
        return out

    async def record(call: ServiceCall) -> ServiceResponse:
        out = {}
        for co in _coordinators_for(hass, call):
            try:
                out[co.camera_id] = await co.client.record(call.data["action"] == "start")
            except BridgeError as e:
                out[co.camera_id] = {"error": e.body}
            await co.async_request_refresh()
        return out

    target = {vol.Optional("device_id"): vol.Any(cv.string, [cv.string])}
    hass.services.async_register(DOMAIN, SERVICE_PRESET_SAVE, preset_save, vol.Schema({**target, vol.Required("preset"): cv.string}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_PRESET_LOAD, preset_load, vol.Schema({**target, vol.Required("preset"): cv.string}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_CLOCK_SYNC, clock_sync, vol.Schema({**target, vol.Optional("force", default=False): cv.boolean}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_SET_PROPERTIES, set_properties, vol.Schema({**target, vol.Required("properties"): dict}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_RECORD, record, vol.Schema({**target, vol.Required("action"): vol.In(["start", "stop"])}), SupportsResponse.OPTIONAL)
