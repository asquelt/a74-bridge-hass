"""Buttons: clock sync, SDK reconnect."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import A74ConfigEntry, A74Coordinator

BUTTONS = (
    ButtonEntityDescription(key="clock_sync", translation_key="clock_sync", icon="mdi:clock-edit-outline", entity_category=EntityCategory.CONFIG),
    ButtonEntityDescription(key="reconnect", translation_key="reconnect", icon="mdi:usb-flash-drive", entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
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
        if self.entity_description.key == "clock_sync":
            await self.coordinator.client.clock_sync(force=True)
        else:
            await self.coordinator.client.reconnect()
        await self.coordinator.async_request_refresh()
