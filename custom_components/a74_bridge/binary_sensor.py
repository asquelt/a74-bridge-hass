"""Binary sensors from GET /status."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import A74ConfigEntry, A74Coordinator


@dataclass(frozen=True, kw_only=True)
class A74BinaryDescription(BinarySensorEntityDescription):
    value: Callable[[dict], bool | None]


def _g(d: dict, *path: str, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


BINARY: tuple[A74BinaryDescription, ...] = (
    A74BinaryDescription(key="connected", translation_key="connected", device_class=BinarySensorDeviceClass.CONNECTIVITY, value=lambda d: _g(d, "camera", "connected")),
    A74BinaryDescription(key="recording", translation_key="recording", icon="mdi:record-rec", value=lambda d: _g(d, "camera", "recording")),
    A74BinaryDescription(key="usb_remote_shooting", translation_key="usb_remote_shooting", icon="mdi:usb-c-port", entity_category=EntityCategory.DIAGNOSTIC,
                         value=lambda d: (_g(d, "usb", "pid") == "0da7") if _g(d, "usb", "present") else False),
    A74BinaryDescription(key="load_in_progress", translation_key="load_in_progress", device_class=BinarySensorDeviceClass.RUNNING, value=lambda d: _g(d, "load_in_progress")),
    A74BinaryDescription(key="clock_ok", translation_key="clock_ok", icon="mdi:clock-check-outline", entity_category=EntityCategory.DIAGNOSTIC,
                         value=lambda d: _g(d, "clock", "action") in ("ok", "corrected") and not _g(d, "clock", "power_loss_reset", default=False)),
    A74BinaryDescription(key="overheating", translation_key="overheating", device_class=BinarySensorDeviceClass.HEAT, entity_category=EntityCategory.DIAGNOSTIC,
                         value=lambda d: (_g(d, "camera", "overheating") or 0) > 0),
)


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    co = entry.runtime_data
    async_add_entities(A74Binary(co, d) for d in BINARY)


class A74Binary(CoordinatorEntity[A74Coordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    entity_description: A74BinaryDescription

    def __init__(self, co: A74Coordinator, desc: A74BinaryDescription) -> None:
        super().__init__(co)
        self.entity_description = desc
        self._attr_unique_id = f"{co.serial}_{desc.key}"
        self._attr_device_info = co.device_info

    @property
    def is_on(self):
        v = self.entity_description.value(self.coordinator.data or {})
        return None if v is None else bool(v)
