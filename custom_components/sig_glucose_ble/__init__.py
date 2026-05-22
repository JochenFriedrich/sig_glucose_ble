"""Bluetooth SIG-compliant Blood Glucose Meter integration for Home Assistant.

Supports any BLE device implementing the Bluetooth SIG Glucose Meter Service
(UUID 0x2A18).

Architecture: local_push — reacts to BLE advertisements, connects via BleakClient,
subscribes to GATT notifications, requests for results via RACP, parses the
standard SIG characteristic format, and exposes sensors in Home Assistant.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    async_register_callback,
    BluetoothCallbackMatcher,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, GLUCOSE_SERVICE_UUID
from .coordinator import GlucoseCoordinator

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SIG GLUCOSE BLE from a config entry."""
    address: str = entry.data["address"]

    coordinator = GlucoseCoordinator(hass, address, entry.title)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()

    # Register a BLE advertisement callback so we react immediately when the
    # device wakes up after a measurement (devices are only briefly connectable).
    @callback
    def _ble_callback(
        service_info: BluetoothServiceInfoBleak,
        change,  # BluetoothChange
    ) -> None:
        coordinator.handle_advertisement(service_info)

    entry.async_on_unload(
        async_register_callback(
            hass,
            _ble_callback,
            BluetoothCallbackMatcher(address=address),
            BluetoothScanningMode.ACTIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
