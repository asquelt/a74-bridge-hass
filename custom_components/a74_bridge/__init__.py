"""A74 Bridge: one Sony A7 IV behind a Raspberry Pi bridge, polled over REST."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BridgeClient, BridgeError
from .rcv import RCVClient
from .studio import Studio
from .const import (
    CONF_GUARD_ENTITY, CONF_TOKEN, DEFAULT_GUARD_ENTITY, DOMAIN, EVENT_CAMERA_DISCONNECTED, EVENT_POWER_LOSS, EVENT_PRESET_APPLIED, EVENT_PRESET_FAILED,
    LOAD_TIMEOUT_S, PRESET_DIR, SCAN_INTERVAL_S, SERVICE_CLOCK_SYNC, SERVICE_PRESET_LOAD, SERVICE_PRESET_SAVE,
    SERVICE_PRESET_DELETE, SERVICE_PRESET_LIST, SERVICE_RECORD, SERVICE_SET_PROPERTIES, SERVICE_SYNC_FROM, SERVICE_TIMECODE_JAM, TYPE_CONSOLE, TYPE_STUDIO,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SWITCH, Platform.TEXT, Platform.SELECT]
type A74ConfigEntry = ConfigEntry[A74Coordinator | RCVClient | Studio]


def is_console(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_TYPE) == TYPE_CONSOLE


def is_studio(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_TYPE) == TYPE_STUDIO


def get_studio(hass: HomeAssistant) -> Studio:
    return hass.data.setdefault(DOMAIN, {}).setdefault("studio", Studio(hass))


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
    if is_studio(entry):
        studio = get_studio(hass)
        studio.guard_entity = entry.options.get(CONF_GUARD_ENTITY, entry.data.get(CONF_GUARD_ENTITY, DEFAULT_GUARD_ENTITY)) or None
        await studio.refresh()
        entry.runtime_data = studio
        entry.async_on_unload(entry.add_update_listener(_options_updated))
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _register_services(hass)
        return True
    if is_console(entry):
        rcv = RCVClient(entry.data[CONF_HOST], entry.data[CONF_PORT])
        rcv.start()
        entry.runtime_data = rcv
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _register_services(hass)
        return True
    client = BridgeClient(async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_TOKEN])
    coordinator = A74Coordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    st = get_studio(hass)
    st.guard_entity = entry.options.get(CONF_GUARD_ENTITY, entry.data.get(CONF_GUARD_ENTITY, DEFAULT_GUARD_ENTITY)) or None
    st.notify()


async def async_unload_entry(hass: HomeAssistant, entry: A74ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and is_console(entry):
        await entry.runtime_data.stop()
    return ok


# ---------------------------------------------------------------- services
def _coordinators_for(hass: HomeAssistant, call: ServiceCall) -> list[A74Coordinator]:
    """Resolve device_id list (or all entries when none given)."""
    device_ids = call.data.get("device_id") or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED and not is_console(e) and not is_studio(e)]
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
    return out


def _consoles_for(hass: HomeAssistant, call: ServiceCall) -> list[RCVClient]:
    device_ids = call.data.get("device_id") or []
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED and is_console(e)]
    if not device_ids:
        return [e.runtime_data for e in entries]
    reg = dr.async_get(hass)
    out = []
    for did in device_ids:
        dev = reg.async_get(did)
        if dev:
            out.extend(e.runtime_data for e in entries if e.entry_id in dev.config_entries)
    return out


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_PRESET_SAVE):
        return

    async def preset_save(call: ServiceCall) -> ServiceResponse:
        return await get_studio(hass).save(call.data.get("preset") or None, _coordinators_for(hass, call), call.data.get("note"))

    async def preset_load(call: ServiceCall) -> ServiceResponse:
        return await get_studio(hass).load(call.data["preset"], _coordinators_for(hass, call))

    async def preset_list(call: ServiceCall) -> ServiceResponse:
        st = get_studio(hass)
        await st.refresh()
        return {"presets": st.presets}

    async def preset_delete(call: ServiceCall) -> ServiceResponse:
        await get_studio(hass).delete(call.data["preset"])
        return {"deleted": call.data["preset"]}

    async def sync_from(call: ServiceCall) -> ServiceResponse:
        st = get_studio(hass)
        targets = _coordinators_for(hass, call) if call.data.get("device_id") else None
        if call.data.get("mode", "parameters") == "full":
            return await st.sync_full(call.data["source"], targets)
        return await st.sync_parameters(call.data["source"], targets)

    async def clock_sync(call: ServiceCall) -> ServiceResponse:
        out = {}
        for co in _coordinators_for(hass, call):
            try:
                out[co.camera_id] = await co.client.clock_sync(bool(call.data.get("force")))
            except BridgeError as e:
                out[co.camera_id] = {"error": e.body}
            except Exception as e:  # noqa: BLE001 - bridge unreachable etc.
                out[co.camera_id] = {"error": str(e)}
        return out

    async def set_properties(call: ServiceCall) -> ServiceResponse:
        out = {}
        for co in _coordinators_for(hass, call):
            try:
                out[co.camera_id] = await co.client.set_properties(dict(call.data["properties"]))
            except BridgeError as e:
                out[co.camera_id] = {"error": e.body}
            except Exception as e:  # noqa: BLE001 - bridge unreachable etc.
                out[co.camera_id] = {"error": str(e)}
        return out

    async def timecode_jam(call: ServiceCall) -> ServiceResponse:
        out = {}
        fps = int(call.data.get("fps", 25))
        for co in _coordinators_for(hass, call):
            try:
                out[co.camera_id] = await co.client.timecode_jam(fps)
            except BridgeError as e:
                out[co.camera_id] = {"error": e.body}
            except Exception as e:  # noqa: BLE001 - bridge unreachable etc.
                out[co.camera_id] = {"error": str(e)}
        return out

    async def record(call: ServiceCall) -> ServiceResponse:
        """Start/stop on every camera and console at the same time (gather), so the studio rolls together."""
        import asyncio  # noqa: PLC0415
        out = {}
        start = call.data["action"] == "start"
        cos = _coordinators_for(hass, call)
        consoles = _consoles_for(hass, call)
        if start and call.data.get("jam", True):
            async def jam(co):
                try:
                    return f"{co.camera_id} jam", await co.client.timecode_jam(int(call.data.get("fps", 25)))
                except BridgeError as e:
                    return f"{co.camera_id} jam", {"error": e.body}
                except Exception as e:  # noqa: BLE001
                    return f"{co.camera_id} jam", {"error": str(e)}
            for k, v in await asyncio.gather(*(jam(co) for co in cos)):
                out[k] = v

        async def console_rec(rcv):
            try:
                await rcv.set_record(start)
                return f"console {rcv.host}", {"sent": True}
            except Exception as e:  # noqa: BLE001
                return f"console {rcv.host}", {"error": str(e)}

        async def camera_rec(co):
            try:
                if start:
                    await co.client.quiet(True)   # session: no settings traffic on this camera until stop
                r = await co.client.record(start)
                if not start:
                    await co.client.quiet(False)
            except BridgeError as e:
                r = {"error": e.body}
            except Exception as e:  # noqa: BLE001
                r = {"error": str(e)}
            await co.async_request_refresh()
            return co.camera_id, r

        for k, v in await asyncio.gather(*(console_rec(r) for r in consoles), *(camera_rec(co) for co in cos)):
            out[k] = v
        return out

    target = {vol.Optional("device_id"): vol.Any(cv.string, [cv.string])}
    hass.services.async_register(DOMAIN, SERVICE_PRESET_SAVE, preset_save, vol.Schema({**target, vol.Optional("preset"): cv.string, vol.Optional("note"): cv.string}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_PRESET_LIST, preset_list, vol.Schema({}), SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_PRESET_DELETE, preset_delete, vol.Schema({vol.Required("preset"): cv.string}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_SYNC_FROM, sync_from, vol.Schema({**target, vol.Required("source"): cv.string, vol.Optional("mode", default="parameters"): vol.In(["parameters", "full"])}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_PRESET_LOAD, preset_load, vol.Schema({**target, vol.Required("preset"): cv.string}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_CLOCK_SYNC, clock_sync, vol.Schema({**target, vol.Optional("force", default=False): cv.boolean}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_SET_PROPERTIES, set_properties, vol.Schema({**target, vol.Required("properties"): dict}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_RECORD, record, vol.Schema({**target, vol.Required("action"): vol.In(["start", "stop"]), vol.Optional("jam", default=True): cv.boolean, vol.Optional("fps", default=25): vol.In([24, 25, 30, 50, 60])}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_TIMECODE_JAM, timecode_jam, vol.Schema({**target, vol.Optional("fps", default=25): vol.In([24, 25, 30, 50, 60])}), SupportsResponse.OPTIONAL)
