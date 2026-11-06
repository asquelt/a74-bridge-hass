"""Text platform: studio preset name."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import A74ConfigEntry, is_studio
from .studio_entities import studio_texts


async def async_setup_entry(hass: HomeAssistant, entry: A74ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    if is_studio(entry):
        async_add_entities(studio_texts(entry, entry.runtime_data))
