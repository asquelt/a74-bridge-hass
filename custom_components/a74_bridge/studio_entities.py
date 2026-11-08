"""Entities of the Studio hub entry: preset name, preset list, action buttons, last action sensor."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_SOURCE_CAMERA, DEFAULT_SOURCE_CAMERA, DOMAIN
from .studio import Studio


class StudioEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, studio: Studio, key: str) -> None:
        self.entry = entry
        self.studio = studio
        self._key = key
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.unique_id or entry.entry_id)}, name=entry.title, manufacturer="a74-bridge", model="Studio presets")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.studio.add_listener(self.async_write_ha_state))

    @property
    def source_camera(self) -> str:
        return self.entry.options.get(CONF_SOURCE_CAMERA) or self.entry.data.get(CONF_SOURCE_CAMERA) or DEFAULT_SOURCE_CAMERA


class PresetNameText(StudioEntity, TextEntity):
    _attr_icon = "mdi:form-textbox"
    _attr_native_max = 60

    def __init__(self, entry, studio) -> None:
        super().__init__(entry, studio, "preset_name")

    @property
    def native_value(self) -> str:
        return self.studio.preset_name

    async def async_set_value(self, value: str) -> None:
        self.studio.preset_name = value
        self.studio.notify()


class PresetSelect(StudioEntity, SelectEntity):
    _attr_icon = "mdi:folder-multiple-image"

    def __init__(self, entry, studio) -> None:
        super().__init__(entry, studio, "preset")

    @property
    def options(self) -> list[str]:
        return [p["name"] for p in self.studio.presets] or ["(none)"]

    @property
    def current_option(self) -> str | None:
        return self.studio.selected or ("(none)" if not self.studio.presets else None)

    @property
    def extra_state_attributes(self):
        p = next((x for x in self.studio.presets if x["name"] == self.studio.selected), None)
        return {"cameras": p.get("cameras"), "created_at": p.get("created_at"), "firmware": p.get("firmware"), "note": p.get("note"), "count": len(self.studio.presets)} if p else {"count": len(self.studio.presets)}

    async def async_select_option(self, option: str) -> None:
        if option != "(none)":
            self.studio.selected = option
            self.studio.notify()


class StudioButton(StudioEntity, ButtonEntity):
    def __init__(self, entry, studio, key: str, icon: str) -> None:
        super().__init__(entry, studio, key)
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return self._key == "focus_refresh_all" or not self.studio.busy

    async def async_press(self) -> None:
        k = self._key
        if k == "preset_save":
            await self.studio.save(self.studio.preset_name or None)
        elif k == "preset_load":
            if not self.studio.selected:
                raise HomeAssistantError("no preset selected")
            await self.studio.load(self.studio.selected)
        elif k == "preset_delete":
            if not self.studio.selected:
                raise HomeAssistantError("no preset selected")
            await self.studio.delete(self.studio.selected)
        elif k == "sync_parameters":
            await self.studio.sync_parameters(self.source_camera)
        elif k == "sync_full":
            await self.studio.sync_full(self.source_camera)
        elif k == "refresh":
            await self.studio.refresh()
        elif k == "refresh_all":
            await self.studio.refresh_all()
        elif k == "focus_refresh_all":
            from . import focus_refresh  # noqa: PLC0415
            await focus_refresh(self.hass)


class LastActionSensor(StudioEntity, SensorEntity):
    _attr_icon = "mdi:history"

    def __init__(self, entry, studio) -> None:
        super().__init__(entry, studio, "last_action")

    @property
    def native_value(self):
        a = self.studio.last_action
        if not a:
            return "none"
        return f"{a.get('action')} {a.get('preset') or a.get('source') or ''} {'ok' if a.get('ok') else 'failed'}".strip()

    @property
    def extra_state_attributes(self):
        return self.studio.last_action


def studio_texts(entry, studio): return [PresetNameText(entry, studio)]
def studio_selects(entry, studio): return [PresetSelect(entry, studio)]
def studio_sensors(entry, studio): return [LastActionSensor(entry, studio)]
def studio_buttons(entry, studio): return [
    StudioButton(entry, studio, "preset_save", "mdi:content-save"),
    StudioButton(entry, studio, "preset_load", "mdi:upload"),
    StudioButton(entry, studio, "preset_delete", "mdi:delete"),
    StudioButton(entry, studio, "sync_parameters", "mdi:camera-switch"),
    StudioButton(entry, studio, "sync_full", "mdi:content-copy"),
    StudioButton(entry, studio, "refresh", "mdi:refresh"),
    StudioButton(entry, studio, "refresh_all", "mdi:refresh-circle"),
    StudioButton(entry, studio, "focus_refresh_all", "mdi:face-recognition"),
]
