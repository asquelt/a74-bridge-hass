"""Sensors from GET /status."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import A74ConfigEntry, A74Coordinator, is_console
from .console_entities import console_sensors
from .const import DOMAIN


@dataclass(frozen=True, kw_only=True)
class A74SensorDescription(SensorEntityDescription):
    value: Callable[[dict], Any]
    attrs: Callable[[dict], dict] | None = None


def _g(d: dict, *path: str, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


SENSORS: tuple[A74SensorDescription, ...] = (
    A74SensorDescription(key="usb_mode", translation_key="usb_mode", icon="mdi:usb", value=lambda d: _g(d, "usb", "mode"), attrs=lambda d: {"pid": _g(d, "usb", "pid")}),
    A74SensorDescription(key="firmware", translation_key="firmware", icon="mdi:chip", entity_category=EntityCategory.DIAGNOSTIC, value=lambda d: _g(d, "camera", "firmware")),
    A74SensorDescription(key="battery_percent", translation_key="battery_percent", device_class=SensorDeviceClass.BATTERY, native_unit_of_measurement=PERCENTAGE,
                         state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda d: _g(d, "camera", "battery_percent"),
                         attrs=lambda d: {"level_enum": _g(d, "camera", "battery_level")}),
    A74SensorDescription(key="active_preset", translation_key="active_preset", icon="mdi:camera-iris", value=lambda d: _g(d, "preset", "active") or "none",
                         attrs=lambda d: {"sha256": _g(d, "preset", "sha256"), "loaded_at": _g(d, "preset", "loaded_at")}),
    A74SensorDescription(key="last_job", translation_key="last_job", icon="mdi:progress-upload", value=lambda d: _g(d, "last_job", "state") or "none",
                         attrs=lambda d: {"error": _g(d, "last_job", "error"), "preset": _g(d, "last_job", "preset"), "stages": _g(d, "last_job", "stages")}),
    A74SensorDescription(key="clock_delta", translation_key="clock_delta", icon="mdi:clock-alert-outline", native_unit_of_measurement=UnitOfTime.SECONDS,
                         state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda d: _g(d, "clock", "delta_s"),
                         attrs=lambda d: {k: _g(d, "clock", k) for k in ("action", "camera_time", "checked_at", "ntp_synced", "power_loss_reset")}),
    A74SensorDescription(key="pi_temp", translation_key="pi_temp", device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS,
                         state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, value=lambda d: _g(d, "bridge", "pi_temp_c")),
    A74SensorDescription(key="disconnects", translation_key="disconnects", icon="mdi:usb-port", state_class=SensorStateClass.TOTAL_INCREASING,
                         entity_category=EntityCategory.DIAGNOSTIC, value=lambda d: _g(d, "camera", "disconnects")),
    A74SensorDescription(key="timecode_jam", translation_key="timecode_jam", icon="mdi:timer-sync-outline", value=lambda d: _g(d, "timecode", "set_to") or "never",
                         attrs=lambda d: {k: _g(d, "timecode", k) for k in ("at", "fps", "latency_ms", "preset_readback", "ok", "error", "run_free", "make_preset", "format")}),
    A74SensorDescription(key="bridge_uptime", translation_key="bridge_uptime", native_unit_of_measurement=UnitOfTime.SECONDS, entity_category=EntityCategory.DIAGNOSTIC,
                         entity_registry_enabled_default=False, value=lambda d: _g(d, "bridge", "uptime_s")),
)


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if is_console(entry):
        async_add_entities(console_sensors(entry, entry.runtime_data))
        return
    co = entry.runtime_data
    async_add_entities(A74Sensor(co, d) for d in SENSORS)


class A74Sensor(CoordinatorEntity[A74Coordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: A74SensorDescription

    def __init__(self, co: A74Coordinator, desc: A74SensorDescription) -> None:
        super().__init__(co)
        self.entity_description = desc
        self._attr_unique_id = f"{co.serial}_{desc.key}"
        self._attr_device_info = co.device_info

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.data or {})

    @property
    def extra_state_attributes(self):
        return self.entity_description.attrs(self.coordinator.data or {}) if self.entity_description.attrs else None
