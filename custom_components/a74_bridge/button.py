"""Buttons: clock sync, SDK reconnect."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import A74ConfigEntry, A74Coordinator, is_console, is_studio
from .studio_entities import studio_buttons

BUTTONS = (
    ButtonEntityDescription(key="clock_sync", translation_key="clock_sync", icon="mdi:clock-edit-outline", entity_category=EntityCategory.CONFIG),
    ButtonEntityDescription(key="reconnect", translation_key="reconnect", icon="mdi:usb-flash-drive", entity_category=EntityCategory.CONFIG),
    ButtonEntityDescription(key="timecode_jam", translation_key="timecode_jam", icon="mdi:timer-sync-outline"),
    ButtonEntityDescription(key="focus_refresh", translation_key="focus_refresh", icon="mdi:face-recognition"),
    ButtonEntityDescription(key="drift_check", translation_key="drift_check", icon="mdi:file-compare", entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if is_studio(entry):
        async_add_entities(studio_buttons(entry, entry.runtime_data))
        return
    if is_console(entry):
        return
    co = entry.runtime_data
    async_add_entities(A74Button(co, d) for d in BUTTONS)


class A74Button(CoordinatorEntity[A74Coordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, co: A74Coordinator, desc: ButtonEntityDescription) -> None:
        super().__init__(co)
        self.entity_description = desc
        self._attr_unique_id = f"{co.serial}_{desc.key}"
        self._attr_device_info = co.device_info

    async def async_press(self) -> None:
        key = self.entity_description.key
        if key == "clock_sync":
            await self.coordinator.client.clock_sync(force=True)
        elif key == "timecode_jam":
            await self.coordinator.client.timecode_jam(25)
        elif key == "focus_refresh":
            await self.coordinator.client.focus_refresh()
        elif key == "drift_check":
            await self.coordinator.client.preset_drift()
        else:
            await self.coordinator.client.reconnect()
        await self.coordinator.async_request_refresh()
