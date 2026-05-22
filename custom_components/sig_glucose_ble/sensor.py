"""Sensor platform for SIG Glucose Meter BLE integration.

Entities created per device:
  • Glucose (mmol/L)          — primary reading, always present if device reports mol/L
  • Glucose (mg/dL)           — always present
  • Sequence Number           — monotonic record counter from device
  • Last Measurement Time     — effective timestamp (base_time + time_offset)
  • Sample Type               — e.g. "Capillary Whole Blood"
  • Sample Location           — e.g. "Finger"
  • Sensor status flags       — battery low, malfunction, result too high/low, etc.
  • Context: Meal             — preprandial / postprandial / fasting / etc.
  • Context: Tester           — self / HCP / lab
  • Context: HbA1c            — if reported by device
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONCENTRATION_MILLIGRAMS_PER_CUBIC_METER
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GlucoseCoordinator
from .parser import GlucoseMeasurement

_ATTRIBUTION = "Bluetooth SIG Glucose Service (0x1808)"

# HA has no built-in mmol/L device class for glucose; we use a plain sensor.
UNIT_MMOL_L = "mmol/L"
UNIT_MG_DL  = "mg/dL"
UNIT_PERCENT = "%"


@dataclass(frozen=True, kw_only=True)
class GlucoseSensorDescription(SensorEntityDescription):
    value_fn: Callable[[GlucoseMeasurement], Any] = lambda _: None
    # If True, entity is only created when the coordinator has seen at least
    # one measurement that populates this field (avoids permanently-unavailable
    # sensors for optional fields like HbA1c that many devices don't report).
    optional: bool = False


# ── Core glucose concentration ─────────────────────────────────────────────────
_CONCENTRATION_DESCRIPTIONS: tuple[GlucoseSensorDescription, ...] = (
    GlucoseSensorDescription(
        key="glucose_mmol_l",
        name="Glucose",
        icon="mdi:water-percent",
        native_unit_of_measurement=UNIT_MMOL_L,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.glucose_mmol_l,
    ),
    GlucoseSensorDescription(
        key="glucose_mg_dl",
        name="Glucose (mg/dL)",
        icon="mdi:water-percent",
        native_unit_of_measurement=UNIT_MG_DL,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.glucose_mg_dl,
    ),
)

# ── Metadata sensors ───────────────────────────────────────────────────────────
_META_DESCRIPTIONS: tuple[GlucoseSensorDescription, ...] = (
    GlucoseSensorDescription(
        key="sequence_number",
        name="Record Sequence Number",
        icon="mdi:counter",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.sequence_number,
    ),
    GlucoseSensorDescription(
        key="measurement_time",
        name="Last Measurement Time",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: m.timestamp,
    ),
    GlucoseSensorDescription(
        key="sample_type",
        name="Sample Type",
        icon="mdi:water",
        value_fn=lambda m: m.sample_type,
    ),
    GlucoseSensorDescription(
        key="sample_location",
        name="Sample Location",
        icon="mdi:map-marker",
        value_fn=lambda m: m.sample_location,
    ),
)

# ── Sensor status flags ────────────────────────────────────────────────────────
_STATUS_DESCRIPTIONS: tuple[GlucoseSensorDescription, ...] = (
    GlucoseSensorDescription(
        key="battery_low",
        name="Device Battery Low",
        icon="mdi:battery-low",
        value_fn=lambda m: m.device_battery_low,
    ),
    GlucoseSensorDescription(
        key="sensor_malfunction",
        name="Sensor Malfunction",
        icon="mdi:alert-circle",
        value_fn=lambda m: m.sensor_malfunction,
    ),
    GlucoseSensorDescription(
        key="result_too_high",
        name="Result Too High",
        icon="mdi:arrow-up-bold",
        value_fn=lambda m: m.sensor_result_too_high,
    ),
    GlucoseSensorDescription(
        key="result_too_low",
        name="Result Too Low",
        icon="mdi:arrow-down-bold",
        value_fn=lambda m: m.sensor_result_too_low,
    ),
    GlucoseSensorDescription(
        key="strip_insertion_error",
        name="Strip Insertion Error",
        icon="mdi:test-tube-off",
        value_fn=lambda m: m.strip_insertion_error,
    ),
    GlucoseSensorDescription(
        key="general_device_fault",
        name="General Device Fault",
        icon="mdi:alert",
        value_fn=lambda m: m.general_device_fault,
    ),
)

# ── Context sensors (optional — only if device reports them) ───────────────────
_CONTEXT_DESCRIPTIONS: tuple[GlucoseSensorDescription, ...] = (
    GlucoseSensorDescription(
        key="meal",
        name="Meal",
        icon="mdi:food",
        optional=True,
        value_fn=lambda m: m.context.meal if m.context else None,
    ),
    GlucoseSensorDescription(
        key="tester",
        name="Tester",
        icon="mdi:account",
        optional=True,
        value_fn=lambda m: m.context.tester if m.context else None,
    ),
    GlucoseSensorDescription(
        key="health",
        name="Health Status",
        icon="mdi:heart",
        optional=True,
        value_fn=lambda m: m.context.health if m.context else None,
    ),
    GlucoseSensorDescription(
        key="hba1c",
        name="HbA1c",
        icon="mdi:percent",
        native_unit_of_measurement=UNIT_PERCENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        optional=True,
        value_fn=lambda m: m.context.hba1c_pct if m.context else None,
    ),
    GlucoseSensorDescription(
        key="carbohydrate",
        name="Carbohydrate",
        icon="mdi:food-apple",
        optional=True,
        value_fn=lambda m: m.context.carbohydrate_id if m.context else None,
    ),
    GlucoseSensorDescription(
        key="medication",
        name="Medication",
        icon="mdi:needle",
        optional=True,
        value_fn=lambda m: m.context.medication_id if m.context else None,
    ),
)


# ── Platform setup ─────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GlucoseCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[GlucoseSensor] = []

    for desc in _CONCENTRATION_DESCRIPTIONS:
        entities.append(GlucoseSensor(coordinator, entry, desc))
    for desc in _META_DESCRIPTIONS:
        entities.append(GlucoseSensor(coordinator, entry, desc))
    for desc in _STATUS_DESCRIPTIONS:
        entities.append(GlucoseSensor(coordinator, entry, desc))
    for desc in _CONTEXT_DESCRIPTIONS:
        entities.append(GlucoseSensor(coordinator, entry, desc))

    async_add_entities(entities)


# ── Entity class ───────────────────────────────────────────────────────────────

class GlucoseSensor(CoordinatorEntity[GlucoseCoordinator], SensorEntity):
    """A single sensor entity backed by the Glucose coordinator."""

    entity_description: GlucoseSensorDescription
    _attr_has_entity_name = True
    _attr_attribution = _ATTRIBUTION

    def __init__(
        self,
        coordinator: GlucoseCoordinator,
        entry: ConfigEntry,
        description: GlucoseSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.device_name,
            manufacturer="Bluetooth SIG",
            model="Glucose Meter (0x1808)",
        )

    @property
    def native_value(self) -> Any:
        m = self.coordinator.data
        if m is None:
            return None
        return self.entity_description.value_fn(m)

    @property
    def available(self) -> bool:
        """Optional sensors are unavailable until the device reports them."""
        if not super().available or self.coordinator.data is None:
            return False
        if self.entity_description.optional:
            return self.entity_description.value_fn(self.coordinator.data) is not None
        return True

    @property
    def extra_state_attributes(self) -> dict:
        m = self.coordinator.data
        if m is None:
            return {}
        attrs: dict = {
            "sequence_number": m.sequence_number,
            "raw_hex": m.raw.hex(),
            "concentration_unit_from_device": m.concentration_unit_raw,
        }
        if m.timestamp:
            attrs["device_timestamp"] = m.timestamp.isoformat()
        if m.base_time and m.time_offset_minutes is not None:
            attrs["time_offset_minutes"] = m.time_offset_minutes
        if m.context:
            ctx = m.context
            if ctx.exercise_duration_s is not None:
                attrs["exercise_duration_s"] = ctx.exercise_duration_s
                attrs["exercise_intensity_pct"] = ctx.exercise_intensity_pct
            if ctx.medication_amount is not None:
                attrs["medication_amount"] = ctx.medication_amount
                attrs["medication_unit"] = ctx.medication_unit
            if ctx.carbohydrate_kg is not None:
                attrs["carbohydrate_kg"] = ctx.carbohydrate_kg
        return attrs
