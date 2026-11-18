"""A74 Bridge: one Sony A7 IV behind a Raspberry Pi bridge, polled over REST."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BridgeClient, BridgeError
from .rcv import RCVClient
from .studio import Studio
from .const import (
    CONF_GUARD_ENTITY, CONF_TOKEN, DEFAULT_GUARD_ENTITY, DOMAIN, EVENT_CAMERA_DISCONNECTED, EVENT_POWER_LOSS, EVENT_PRESET_APPLIED, EVENT_PRESET_FAILED,
    EVENT_MEDIA_JOB_FINISHED, MEDIA_RECONNECT_WAIT_S, MEDIA_RELEASE_TIMEOUT_S, SERVICE_CONSOLE_CANCEL, SERVICE_CONSOLE_JOB, SERVICE_CONSOLE_LIST, SERVICE_CONSOLE_UPLOAD,
    SERVICE_MEDIA_CANCEL, SERVICE_MEDIA_JOB, SERVICE_MEDIA_LIST, SERVICE_MEDIA_UPLOAD,
    LOAD_TIMEOUT_S, PRESET_DIR, SCAN_INTERVAL_S, SERVICE_CLOCK_SYNC, SERVICE_PRESET_LOAD, SERVICE_PRESET_SAVE,
    SERVICE_PRESET_DELETE, SERVICE_PRESET_LIST, SERVICE_RECORD, SERVICE_SET_PROPERTIES, SERVICE_SYNC_FROM, SERVICE_TIMECODE_JAM, SERVICE_FOCUS_REFRESH, TYPE_CONSOLE, TYPE_STUDIO,
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
        self._media_quiet_until = 0.0   # the SDK session is expected to drop around a card listing or an upload job
        self._job_running: dict[str, str | None] = {"media": None, "console": None}

    def media_quiet(self, seconds: float = 45) -> None:
        """Do not report a camera disconnect for a while: a card listing or an upload job switches the SDK session."""
        self._media_quiet_until = max(self._media_quiet_until, time.monotonic() + seconds)

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
        if data.get("media", {}).get("in_progress"):
            self.media_quiet()
        for kind in ("media", "console"):
            # one event per finished upload job, so automations need not poll
            blk = data.get(kind) or {}
            job = blk.get("job") or {}
            if blk.get("in_progress") and job.get("id") and not job.get("finished_at"):
                self._job_running[kind] = job["id"]
            elif self._job_running[kind] and job.get("id") == self._job_running[kind] and job.get("finished_at"):
                self._job_running[kind] = None
                self.hass.bus.async_fire(EVENT_MEDIA_JOB_FINISHED, {"camera_id": self.camera_id, "serial": self.serial, "source": "camera" if kind == "media" else "console", "job": job})
        if self._was_connected and not connected and not data.get("load_in_progress") and time.monotonic() > self._media_quiet_until:
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

    async def _guard_changed(event) -> None:
        # studio rule: a session starting stops every upload at once (the camera must be back in Remote mode)
        new = event.data.get("new_state")
        if new is None or new.state != "on":
            return
        data = coordinator.data or {}
        for kind, cancel in (("media", client.media_cancel), ("console", client.console_cancel)):
            if (data.get(kind) or {}).get("in_progress"):
                try:
                    await cancel()
                    _LOGGER.warning("%s: %s upload cancelled, %s turned on", coordinator.camera_id, kind, event.data.get("entity_id"))
                except Exception as e:  # noqa: BLE001
                    _LOGGER.error("%s: could not cancel the %s upload: %s", coordinator.camera_id, kind, e)
        await coordinator.async_request_refresh()

    guard = _guard_entity(hass)
    if guard:
        entry.async_on_unload(async_track_state_change_event(hass, [guard], _guard_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


def _guard_entity(hass: HomeAssistant) -> str | None:
    """The studio session switch; taken from the Studio entry when there is one."""
    for e in hass.config_entries.async_entries(DOMAIN):
        if is_studio(e):
            return e.options.get(CONF_GUARD_ENTITY, e.data.get(CONF_GUARD_ENTITY, DEFAULT_GUARD_ENTITY)) or None
    return DEFAULT_GUARD_ENTITY


def _upload_guard(hass: HomeAssistant, what: str, cos: list[A74Coordinator]) -> None:
    """Card listings and uploads take the camera out of Remote mode: never during a session or while it records."""
    guard = _guard_entity(hass)
    st = hass.states.get(guard) if guard else None
    if st is not None and st.state == "on":
        raise HomeAssistantError(f"{what} refused: {guard} is on (studio session in progress)")
    rec = [co.camera_id for co in cos if ((co.data or {}).get("camera") or {}).get("recording")]
    if rec:
        raise HomeAssistantError(f"{what} refused: recording on {', '.join(rec)}")


def _console_bridge(hass: HomeAssistant) -> A74Coordinator:
    """The one bridge that reaches the console's ISO disk (A74_CONSOLE_SSH set there)."""
    for e in hass.config_entries.async_entries(DOMAIN):
        if e.state is ConfigEntryState.LOADED and not is_console(e) and not is_studio(e) and ((e.runtime_data.data or {}).get("console") or {}).get("configured"):
            return e.runtime_data
    raise HomeAssistantError("no bridge has the console upload configured (A74_CONSOLE_SSH)")


async def _each(cos: list[A74Coordinator], call) -> dict:
    """Run one bridge call on every coordinator in parallel; errors become {"error": ...} per camera."""
    async def one(co):
        try:
            return co.camera_id, await call(co)
        except BridgeError as e:
            return co.camera_id, {"error": e.body.get("error", e.body) if isinstance(e.body, dict) else e.body, "status": e.status}
        except Exception as e:  # noqa: BLE001 - bridge unreachable etc.
            return co.camera_id, {"error": str(e)}
    return dict(await asyncio.gather(*(one(co) for co in cos)))


async def release_cameras(hass: HomeAssistant, cos: list[A74Coordinator], still_wanted) -> dict:
    """Before a record start: cameras busy with a card upload get the job cancelled and are waited for.

    A camera in the middle of an upload refuses the start and returns to Remote mode only after rclone has flushed
    (tens of seconds), so starting at once would roll the studio without it. Returns {camera_id: note} for the cameras
    that were busy; `still_wanted()` turns false when a stop arrives meanwhile.
    """
    async def fresh(co):
        try:
            return co, await co.client.status()
        except Exception:  # noqa: BLE001 - an unreachable bridge is not "busy"; the start reports it
            return co, None

    def busy(st: dict | None) -> bool:
        return bool(st) and bool((st.get("media") or {}).get("in_progress"))

    waiting = [co for co, st in await asyncio.gather(*(fresh(co) for co in cos)) if busy(st)]
    if not waiting:
        return {}
    _LOGGER.warning("record start: waiting for %s to leave the upload", ", ".join(co.camera_id for co in waiting))
    for co in waiting:
        co.media_quiet(MEDIA_RELEASE_TIMEOUT_S)
        try:
            await co.client.media_cancel()
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("%s: could not cancel the upload: %s", co.camera_id, e)
    out = {co.camera_id: "waiting" for co in waiting}
    t0 = time.monotonic()
    while waiting and still_wanted() and time.monotonic() - t0 < MEDIA_RELEASE_TIMEOUT_S:
        await asyncio.sleep(2)
        for co, st in await asyncio.gather(*(fresh(co) for co in list(waiting))):
            if st and not busy(st) and (st.get("camera") or {}).get("connected"):
                waiting.remove(co)
                out[co.camera_id] = f"ready after {round(time.monotonic() - t0)} s"
    for co in waiting:
        out[co.camera_id] = "start aborted by a stop" if not still_wanted() else f"still busy after {MEDIA_RELEASE_TIMEOUT_S} s"
        _LOGGER.error("record start: %s %s", co.camera_id, out[co.camera_id])
    return out


async def media_upload(co: A74Coordinator, prefix: str, files: list[dict]) -> dict:
    """Start an upload job; waits while the camera is still returning from a card listing."""
    deadline = time.monotonic() + MEDIA_RECONNECT_WAIT_S
    co.media_quiet()
    while True:
        try:
            r = await co.client.media_upload(prefix, files)
            await co.async_request_refresh()
            return r
        except BridgeError as e:
            err = e.body.get("error") if isinstance(e.body, dict) else None
            if err != "camera not connected" or time.monotonic() > deadline:
                raise
        await asyncio.sleep(2)


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
async def focus_refresh(hass: HomeAssistant, cos: list[A74Coordinator] | None = None) -> dict:
    """Refresh focus on the given (default: all) cameras in parallel. Allowed while recording (studio exception)."""
    import asyncio  # noqa: PLC0415
    if cos is None:
        cos = [e.runtime_data for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED and not is_console(e) and not is_studio(e)]

    async def one(co):
        try:
            return co.camera_id, await co.client.focus_refresh()
        except BridgeError as e:
            return co.camera_id, {"error": e.body}
        except Exception as e:  # noqa: BLE001 - bridge unreachable etc.
            return co.camera_id, {"error": str(e)}
    out = dict(await asyncio.gather(*(one(co) for co in cos)))
    _LOGGER.info("focus refresh: %s", out)
    return out


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
        gen = hass.data[DOMAIN]["record_gen"] = hass.data.setdefault(DOMAIN, {}).get("record_gen", 0) + 1   # a later call supersedes a start that is still waiting
        if start:
            released = await release_cameras(hass, cos, lambda: hass.data[DOMAIN]["record_gen"] == gen)
            if released:
                out["waited_for_upload"] = released
            if hass.data[DOMAIN]["record_gen"] != gen:
                out["aborted"] = "superseded by a later record call while waiting for the cameras"
                return out
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

    async def focus_refresh_service(call: ServiceCall) -> ServiceResponse:
        return await focus_refresh(hass, _coordinators_for(hass, call))

    def _files(call: ServiceCall, key: str) -> list[dict]:
        out = []
        for f in call.data["files"]:
            if isinstance(f, str):
                f = {key: f}
            if not isinstance(f, dict) or not f.get(key):
                raise HomeAssistantError(f"each file needs a {key}")
            out.append(f)
        return out

    async def media_list(call: ServiceCall) -> ServiceResponse:
        cos = _coordinators_for(hass, call)
        _upload_guard(hass, "card listing", cos)
        for co in cos:
            co.media_quiet()
        return await _each(cos, lambda co: co.client.media_list())

    async def media_upload_service(call: ServiceCall) -> ServiceResponse:
        cos = _coordinators_for(hass, call)
        if len(cos) != 1:
            raise HomeAssistantError("media_upload takes exactly one camera: file names differ per card")
        _upload_guard(hass, "upload", cos)
        files = _files(call, "name")
        return await _each(cos, lambda co: media_upload(co, call.data["prefix"], files))

    async def media_job(call: ServiceCall) -> ServiceResponse:
        return await _each(_coordinators_for(hass, call), lambda co: co.client.media_job())

    async def media_cancel(call: ServiceCall) -> ServiceResponse:
        cos = _coordinators_for(hass, call)
        out = await _each(cos, lambda co: co.client.media_cancel())
        for co in cos:
            await co.async_request_refresh()
        return out

    async def console_list(call: ServiceCall) -> ServiceResponse:
        return await _console_bridge(hass).client.console_list()   # reads the disk over SSH; the cameras are not touched

    async def console_upload(call: ServiceCall) -> ServiceResponse:
        co = _console_bridge(hass)
        _upload_guard(hass, "console upload", [])
        out = await _each([co], lambda c: c.client.console_upload(call.data["prefix"], _files(call, "path")))
        await co.async_request_refresh()
        return out[co.camera_id]

    async def console_job(call: ServiceCall) -> ServiceResponse:
        co = _console_bridge(hass)
        return (await _each([co], lambda c: c.client.console_job()))[co.camera_id]

    async def console_cancel(call: ServiceCall) -> ServiceResponse:
        co = _console_bridge(hass)
        out = await _each([co], lambda c: c.client.console_cancel())
        await co.async_request_refresh()
        return out[co.camera_id]

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
    hass.services.async_register(DOMAIN, SERVICE_FOCUS_REFRESH, focus_refresh_service, vol.Schema({**target}), SupportsResponse.OPTIONAL)
    upload = {vol.Required("prefix"): cv.string, vol.Required("files"): vol.All(cv.ensure_list, vol.Length(min=1), [vol.Any(cv.string, dict)])}
    hass.services.async_register(DOMAIN, SERVICE_MEDIA_LIST, media_list, vol.Schema({**target}), SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_MEDIA_UPLOAD, media_upload_service, vol.Schema({**target, **upload}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_MEDIA_JOB, media_job, vol.Schema({**target}), SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_MEDIA_CANCEL, media_cancel, vol.Schema({**target}), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_CONSOLE_LIST, console_list, vol.Schema({}), SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_CONSOLE_UPLOAD, console_upload, vol.Schema(upload), SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, SERVICE_CONSOLE_JOB, console_job, vol.Schema({}), SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_CONSOLE_CANCEL, console_cancel, vol.Schema({}), SupportsResponse.OPTIONAL)
