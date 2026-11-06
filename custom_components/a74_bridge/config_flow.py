"""Config flow: host, port, token; the bridge's camera serial becomes the unique id."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BridgeClient, BridgeError
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN

SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    vol.Required(CONF_TOKEN): str,
})


class A74ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = BridgeClient(async_get_clientsession(self.hass), user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_TOKEN])
            try:
                health = await client.health()
                status = await client.status()
            except BridgeError as e:
                errors["base"] = "invalid_auth" if e.status == 401 else "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                serial = health.get("serial") or status.get("bridge", {}).get("camera_id")
                if not serial:
                    errors["base"] = "no_camera"
                else:
                    await self.async_set_unique_id(serial)
                    self._abort_if_unique_id_configured(updates=user_input)
                    title = status.get("bridge", {}).get("camera_id") or user_input[CONF_HOST]
                    return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)
