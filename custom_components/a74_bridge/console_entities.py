"""Entities for a RØDECaster Video console entry (push-updated from the OSC link)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory, UnitOfInformation
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .rcv import RCVClient


class RCVEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, client: RCVClient, key: str) -> None:
        self.client = client
        self._key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)}, name=entry.title, manufacturer="RØDE", model="RØDECaster Video",
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.client.add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.client.connected


class RCVRecordSwitch(RCVEntity, SwitchEntity):
    _attr_icon = "mdi:record-rec"

    def __init__(self, entry: ConfigEntry, client: RCVClient) -> None:
        super().__init__(entry, client, "console_record")

    @property
    def is_on(self) -> bool | None:
        return self.client.recording

    @property
    def available(self) -> bool:
        return self.client.connected and self.client.record_enabled is not False

    async def async_turn_on(self, **kwargs) -> None:
        await self.client.set_record(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.client.set_record(False)


class RCVBinary(RCVEntity, BinarySensorEntity):
    def __init__(self, entry: ConfigEntry, client: RCVClient, key: str, getter, device_class=None, diagnostic=False) -> None:
        super().__init__(entry, client, key)
        self._getter = getter
        self._attr_device_class = device_class
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True if self._key == "console_connected" else self.client.connected

    @property
    def is_on(self) -> bool | None:
        return self._getter(self.client)


class RCVSensor(RCVEntity, SensorEntity):
    def __init__(self, entry: ConfigEntry, client: RCVClient, key: str, getter, unit=None, device_class=None, diagnostic=False, icon=None) -> None:
        super().__init__(entry, client, key)
        self._getter = getter
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if device_class == SensorDeviceClass.DATA_SIZE:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 1
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self):
        return self._getter(self.client)


def _gb(client: RCVClient, n: int):
    d = client.disk(n)
    return None if d is None else round(d["remaining_bytes"] / 1e9, 1)


def _hdmi(n: int):
    return lambda c: (c.video_in_state(n) == "connected") if c.video_in_state(n) is not None else None


def console_switches(entry: ConfigEntry, client: RCVClient) -> list:
    return [RCVRecordSwitch(entry, client)]


def console_binary_sensors(entry: ConfigEntry, client: RCVClient) -> list:
    out = [
        RCVBinary(entry, client, "console_connected", lambda c: c.connected, BinarySensorDeviceClass.CONNECTIVITY, diagnostic=True),
        RCVBinary(entry, client, "console_recording", lambda c: c.recording, BinarySensorDeviceClass.RUNNING),
        RCVBinary(entry, client, "console_record_enabled", lambda c: c.record_enabled, diagnostic=True),
        RCVBinary(entry, client, "console_live", lambda c: c.live),
    ]
    out += [RCVBinary(entry, client, f"console_hdmi{n}", _hdmi(n), BinarySensorDeviceClass.PLUG) for n in range(1, 5)]
    return out


def console_sensors(entry: ConfigEntry, client: RCVClient) -> list:
    return [
        RCVSensor(entry, client, "console_program", lambda c: c.program, icon="mdi:television-play"),
        RCVSensor(entry, client, "console_usb_free", lambda c: _gb(c, 1), UnitOfInformation.GIGABYTES, SensorDeviceClass.DATA_SIZE, diagnostic=True),
        RCVSensor(entry, client, "console_emmc_free", lambda c: _gb(c, 2), UnitOfInformation.GIGABYTES, SensorDeviceClass.DATA_SIZE, diagnostic=True),
    ]
