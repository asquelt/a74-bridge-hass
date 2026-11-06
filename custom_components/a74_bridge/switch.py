"""Recording switch: manual start/stop from the HA panel. Automations must not use it (studio rule)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import A74ConfigEntry, A74Coordinator, is_console
from .console_entities import console_switches

DESC = SwitchEntityDescription(key="record", translation_key="record", icon="mdi:record-rec")


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if is_console(entry):
        async_add_entities(console_switches(entry, entry.runtime_data))
        return
    async_add_entities([A74RecordSwitch(entry.runtime_data)])


class A74RecordSwitch(CoordinatorEntity[A74Coordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = False

    def __init__(self, co: A74Coordinator) -> None:
        super().__init__(co)
        self.entity_description = DESC
        self._attr_unique_id = f"{co.serial}_record"
        self._attr_device_info = co.device_info

    @property
    def available(self) -> bool:
        d = self.coordinator.data or {}
        return super().available and bool(d.get("camera", {}).get("connected")) and not d.get("load_in_progress")

    @property
    def is_on(self) -> bool | None:
        v = (self.coordinator.data or {}).get("camera", {}).get("recording")
        return None if v is None else bool(v)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.client.record(True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.client.record(False)
        await self.coordinator.async_request_refresh()
