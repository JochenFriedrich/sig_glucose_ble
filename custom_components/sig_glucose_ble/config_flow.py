"""Config flow for SIG Glucose Meter BLE."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN, GLUCOSE_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


class SIGGlucoseConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery_info is not None
        info = self._discovery_info
        if user_input is not None:
            return self.async_create_entry(
                title=info.name or info.address,
                data={CONF_ADDRESS: info.address},
            )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": info.name or "Unknown",
                "address": info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current_addresses = self._async_current_ids()
        for service_info in async_discovered_service_info(self.hass, connectable=True):
            address = service_info.address
            if address in current_addresses:
                continue
            if GLUCOSE_SERVICE_UUID in (service_info.service_uuids or []):
                self._discovered_devices[address] = service_info.name or address

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address.upper(), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._discovered_devices.get(address, address)
            return self.async_create_entry(title=name, data={CONF_ADDRESS: address})

        device_options = {
            addr: f"{name} ({addr})"
            for addr, name in self._discovered_devices.items()
        }
        schema = vol.Schema(
            {vol.Required(CONF_ADDRESS): vol.In(device_options)}
            if device_options
            else {vol.Required(CONF_ADDRESS): str}
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={"count": str(len(device_options))},
        )
