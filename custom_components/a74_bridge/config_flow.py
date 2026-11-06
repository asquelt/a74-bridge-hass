"""Config flow with two device types: a camera bridge (REST, token) or a RØDECaster Video console (OSC/TCP)."""
from __future__ import annotations

import asyncio

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TYPE
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BridgeClient, BridgeError
from .const import CONF_SOURCE_CAMERA, CONF_TOKEN, DEFAULT_PORT, DEFAULT_SOURCE_CAMERA, DOMAIN, TYPE_BRIDGE, TYPE_CONSOLE, TYPE_STUDIO
from .rcv import RCV_PORT, RCVClient

BRIDGE_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    vol.Required(CONF_TOKEN): str,
})
CONSOLE_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=RCV_PORT): int,
})


class A74ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["bridge", "console", "studio"])

    async def async_step_bridge(self, user_input=None):
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
                    return self.async_create_entry(title=title, data={**user_input, CONF_TYPE: TYPE_BRIDGE})
        return self.async_show_form(step_id="bridge", data_schema=BRIDGE_SCHEMA, errors=errors)

    async def async_step_console(self, user_input=None):
        errors = {}
        if user_input is not None:
            client = RCVClient(user_input[CONF_HOST], user_input[CONF_PORT])
            client.start()
            try:
                for _ in range(40):  # up to 8 s for the state dump after /show
                    await asyncio.sleep(0.2)
                    if client.state.get("/show/recordEnabled") is not None or client.state.get("/show/record") is not None:
                        break
                ok = client.connected and client.state.get("/show/record") is not None
            finally:
                await client.stop()
            if not ok:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"rcv-{user_input[CONF_HOST]}")
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(title="RØDECaster Video", data={**user_input, CONF_TYPE: TYPE_CONSOLE})
        return self.async_show_form(step_id="console", data_schema=CONSOLE_SCHEMA, errors=errors)

    async def async_step_studio(self, user_input=None):
        await self.async_set_unique_id("studio")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Studio", data={CONF_TYPE: TYPE_STUDIO, CONF_SOURCE_CAMERA: user_input[CONF_SOURCE_CAMERA]})
        return self.async_show_form(step_id="studio", data_schema=vol.Schema({vol.Required(CONF_SOURCE_CAMERA, default=DEFAULT_SOURCE_CAMERA): str}))

    @staticmethod
    def async_get_options_flow(config_entry):
        return StudioOptionsFlow()


class StudioOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if self.config_entry.data.get(CONF_TYPE) != TYPE_STUDIO:
            return self.async_abort(reason="no_options")
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = self.config_entry.options.get(CONF_SOURCE_CAMERA) or self.config_entry.data.get(CONF_SOURCE_CAMERA) or DEFAULT_SOURCE_CAMERA
        return self.async_show_form(step_id="init", data_schema=vol.Schema({vol.Required(CONF_SOURCE_CAMERA, default=cur): str}))
