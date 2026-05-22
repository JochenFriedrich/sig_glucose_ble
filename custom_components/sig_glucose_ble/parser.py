"""Parser for Bluetooth SIG Glucose Measurement (0x2A18) and Context (0x2A34).

0x2A18 byte layout
──────────────────
  Octet 0        Flags (uint8)
  Octets 1-2     Sequence Number (uint16 LE) — monotonic record counter
  Octets 3-9     Base Time (year uint16, month/day/h/m/s each uint8)
  [Octets 10-11] Time Offset (int16 LE, minutes) — if FLAG_TIME_OFFSET
  [Octets n+0-1] Glucose Concentration (SFLOAT) — if FLAG_CONCENTRATION_PRESENT
  [Octet  n+2]   Type (high nibble) + Sample Location (low nibble) — same condition
  [Octets m+0-1] Sensor Status Annunciation (uint16 LE) — if FLAG_SENSOR_STATUS

Glucose concentration SFLOAT units:
  FLAG_CONCENTRATION_MOL = 0  →  kg/L  (multiply × 100000 → mg/dL)
  FLAG_CONCENTRATION_MOL = 1  →  mol/L (multiply × 1000   → mmol/L)

0x2A34 Glucose Measurement Context byte layout
──────────────────────────────────────────────
  Octet 0        Flags (uint8)
  Octets 1-2     Sequence Number (uint16 LE) — links to matching 0x2A18 record
  [Octet  n]     Extended Flags (uint8) — if CTX_FLAG_EXTENDED
  [Octet  n]     Carbohydrate ID (uint8) — if CTX_FLAG_CARBOHYDRATE
  [Octets n+1-2] Carbohydrate (SFLOAT, kg) — same condition
  [Octet  n]     Meal (uint8) — if CTX_FLAG_MEAL
  [Octet  n]     Tester (high nibble) + Health (low nibble) — if CTX_FLAG_TESTER_HEALTH
  [Octets n+0-1] Exercise Duration (uint16 LE, seconds) — if CTX_FLAG_EXERCISE
  [Octet  n+2]   Exercise Intensity (uint8, %) — same condition
  [Octet  n]     Medication ID (uint8) — if CTX_FLAG_MEDICATION
  [Octets n+1-2] Medication (SFLOAT, kg or L) — same condition
  [Octets n+0-1] HbA1c (SFLOAT, %) — if CTX_FLAG_HBA1C
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import logging

from .const import (
    FLAG_TIME_OFFSET, FLAG_CONCENTRATION_PRESENT, FLAG_CONCENTRATION_MOL,
    FLAG_SENSOR_STATUS, FLAG_CONTEXT_INFO,
    CTX_FLAG_CARBOHYDRATE, CTX_FLAG_MEAL, CTX_FLAG_TESTER_HEALTH,
    CTX_FLAG_EXERCISE, CTX_FLAG_MEDICATION, CTX_FLAG_MEDICATION_LITERS,
    CTX_FLAG_HBA1C, CTX_FLAG_EXTENDED,
    SAMPLE_TYPE, SAMPLE_LOCATION, MEAL_LABEL, TESTER_LABEL, HEALTH_LABEL,
    CARBOHYDRATE_LABEL, MEDICATION_LABEL,
    MOL_TO_MMOL, KG_L_TO_MG_DL,
)

_LOGGER = logging.getLogger(__name__)

# IEEE-11073 SFLOAT special values
_SFLOAT_NAN  = 0x07FF
_SFLOAT_NRES = 0x0800
_SFLOAT_POS_INF = 0x07FE
_SFLOAT_NEG_INF = 0x0802


def _sfloat_to_float(raw: int) -> Optional[float]:
    raw &= 0xFFFF
    if raw in (_SFLOAT_NAN, _SFLOAT_NRES, _SFLOAT_POS_INF, _SFLOAT_NEG_INF):
        return None
    exponent = raw >> 12
    if exponent >= 8:
        exponent -= 16
    mantissa = raw & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    return round(mantissa * (10 ** exponent), 8)


def _parse_base_time(data: bytes, offset: int) -> tuple[Optional[datetime], int]:
    """Parse a 7-byte GATT Date Time field; returns (datetime|None, new_offset)."""
    if len(data) < offset + 7:
        return None, offset
    year, month, day, hour, minute, second = struct.unpack_from("<HBBBBB", data, offset)
    try:
        return datetime(year, month, day, hour, minute, second).astimezone(), offset + 7
    except ValueError:
        _LOGGER.warning("Invalid Date Time at offset %d: %d-%d-%d %d:%d:%d",
                        offset, year, month, day, hour, minute, second)
        return None, offset + 7


@dataclass
class GlucoseMeasurementContext:
    """Optional context record linked to a Glucose Measurement via sequence number."""
    sequence_number: int = 0
    carbohydrate_id: Optional[str] = None
    carbohydrate_kg: Optional[float] = None
    meal: Optional[str] = None
    tester: Optional[str] = None
    health: Optional[str] = None
    exercise_duration_s: Optional[int] = None
    exercise_intensity_pct: Optional[int] = None
    medication_id: Optional[str] = None
    medication_amount: Optional[float] = None
    medication_unit: Optional[str] = None   # "kg" or "L"
    hba1c_pct: Optional[float] = None
    raw: bytes = field(default_factory=bytes, repr=False)


@dataclass
class GlucoseMeasurement:
    """Parsed record from a single Glucose Measurement (0x2A18) notification."""

    sequence_number: int = 0

    # Timestamp: base_time + time_offset (if present)
    base_time: Optional[datetime] = None
    time_offset_minutes: Optional[int] = None  # signed int16

    # Glucose concentration — always converted to user-friendly units
    glucose_mmol_l: Optional[float] = None  # mmol/L (None if device sends kg/L only)
    glucose_mg_dl: Optional[float] = None   # mg/dL
    concentration_unit_raw: Optional[str] = None  # "mol/L" or "kg/L" as sent

    sample_type: Optional[str] = None       # human-readable from lookup table
    sample_location: Optional[str] = None

    # Sensor status annunciation flags
    device_battery_low: Optional[bool] = None
    sensor_malfunction: Optional[bool] = None
    sample_size_insufficient: Optional[bool] = None
    strip_insertion_error: Optional[bool] = None
    strip_type_incorrect: Optional[bool] = None
    sensor_result_too_high: Optional[bool] = None
    sensor_result_too_low: Optional[bool] = None
    sensor_temperature_too_high: Optional[bool] = None
    sensor_temperature_too_low: Optional[bool] = None
    sensor_read_interrupted: Optional[bool] = None
    general_device_fault: Optional[bool] = None
    time_fault: Optional[bool] = None

    # Populated later when a matching 0x2A34 context is received
    context: Optional[GlucoseMeasurementContext] = None

    raw: bytes = field(default_factory=bytes, repr=False)

    @property
    def timestamp(self) -> Optional[datetime]:
        """Effective timestamp = base_time + time_offset."""
        if self.base_time is None:
            return None
        if self.time_offset_minutes is not None:
            return self.base_time + timedelta(minutes=self.time_offset_minutes)
        return self.base_time

    @property
    def is_valid(self) -> bool:
        return self.glucose_mmol_l is not None or self.glucose_mg_dl is not None


def parse_glucose_measurement(data: bytes) -> GlucoseMeasurement:
    """Parse raw bytes from GATT characteristic 0x2A18."""
    if len(data) < 10:
        raise ValueError(
            f"Glucose Measurement data too short: {len(data)} bytes (minimum 10)"
        )

    result = GlucoseMeasurement(raw=data)
    flags = data[0]

    # Sequence number (uint16 LE) — octets 1-2
    (result.sequence_number,) = struct.unpack_from("<H", data, 1)

    # Base Time — octets 3-9
    result.base_time, offset = _parse_base_time(data, 3)

    # Optional Time Offset (int16 LE, minutes)
    if flags & FLAG_TIME_OFFSET:
        if len(data) < offset + 2:
            _LOGGER.warning("Time Offset flag set but data truncated")
        else:
            (result.time_offset_minutes,) = struct.unpack_from("<h", data, offset)
            offset += 2

    # Optional Glucose Concentration + Type/Sample Location
    if flags & FLAG_CONCENTRATION_PRESENT:
        if len(data) < offset + 3:
            _LOGGER.warning("Concentration flag set but data truncated")
        else:
            (conc_raw,) = struct.unpack_from("<H", data, offset)
            offset += 2
            type_location_byte = data[offset]
            offset += 1

            conc_float = _sfloat_to_float(conc_raw)

            if flags & FLAG_CONCENTRATION_MOL:
                # mol/L → convert to mmol/L (display) and mg/dL (approx via 18×)
                result.concentration_unit_raw = "mol/L"
                if conc_float is not None:
                    result.glucose_mmol_l = round(conc_float * MOL_TO_MMOL, 2)
                    result.glucose_mg_dl  = round(result.glucose_mmol_l * 18.016, 1)
            else:
                # kg/L → convert to mg/dL (display) and mmol/L
                result.concentration_unit_raw = "kg/L"
                if conc_float is not None:
                    result.glucose_mg_dl  = round(conc_float * KG_L_TO_MG_DL, 1)
                    result.glucose_mmol_l = round(result.glucose_mg_dl / 18.016, 2)

            # Type in high nibble, Sample Location in low nibble
            type_nibble     = (type_location_byte >> 4) & 0x0F
            location_nibble =  type_location_byte        & 0x0F
            result.sample_type     = SAMPLE_TYPE.get(type_nibble, f"Unknown(0x{type_nibble:X})")
            result.sample_location = SAMPLE_LOCATION.get(location_nibble, f"Unknown(0x{location_nibble:X})")

    # Optional Sensor Status Annunciation (uint16 LE)
    if flags & FLAG_SENSOR_STATUS:
        if len(data) >= offset + 2:
            (status,) = struct.unpack_from("<H", data, offset)
            result.device_battery_low          = bool(status & 0x0001)
            result.sensor_malfunction          = bool(status & 0x0002)
            result.sample_size_insufficient    = bool(status & 0x0004)
            result.strip_insertion_error       = bool(status & 0x0008)
            result.strip_type_incorrect        = bool(status & 0x0010)
            result.sensor_result_too_high      = bool(status & 0x0020)
            result.sensor_result_too_low       = bool(status & 0x0040)
            result.sensor_temperature_too_high = bool(status & 0x0080)
            result.sensor_temperature_too_low  = bool(status & 0x0100)
            result.sensor_read_interrupted     = bool(status & 0x0200)
            result.general_device_fault        = bool(status & 0x0400)
            result.time_fault                  = bool(status & 0x0800)

    return result


def parse_glucose_context(data: bytes) -> GlucoseMeasurementContext:
    """Parse raw bytes from GATT characteristic 0x2A34."""
    if len(data) < 3:
        raise ValueError(f"Glucose Context data too short: {len(data)} bytes")

    ctx = GlucoseMeasurementContext(raw=data)
    flags = data[0]
    (ctx.sequence_number,) = struct.unpack_from("<H", data, 1)
    offset = 3

    if flags & CTX_FLAG_EXTENDED and len(data) > offset:
        offset += 1  # skip extended flags byte (reserved)

    if flags & CTX_FLAG_CARBOHYDRATE and len(data) > offset + 2:
        carb_id = data[offset]
        ctx.carbohydrate_id = CARBOHYDRATE_LABEL.get(carb_id, f"Unknown(0x{carb_id:X})")
        offset += 1
        (carb_raw,) = struct.unpack_from("<H", data, offset)
        ctx.carbohydrate_kg = _sfloat_to_float(carb_raw)
        offset += 2

    if flags & CTX_FLAG_MEAL and len(data) > offset:
        meal = data[offset]
        ctx.meal = MEAL_LABEL.get(meal, f"Unknown(0x{meal:X})")
        offset += 1

    if flags & CTX_FLAG_TESTER_HEALTH and len(data) > offset:
        th = data[offset]
        tester   = (th >> 4) & 0x0F
        health   =  th       & 0x0F
        ctx.tester = TESTER_LABEL.get(tester, f"Unknown(0x{tester:X})")
        ctx.health = HEALTH_LABEL.get(health, f"Unknown(0x{health:X})")
        offset += 1

    if flags & CTX_FLAG_EXERCISE and len(data) >= offset + 3:
        (ctx.exercise_duration_s,) = struct.unpack_from("<H", data, offset)
        offset += 2
        ctx.exercise_intensity_pct = data[offset]
        offset += 1

    if flags & CTX_FLAG_MEDICATION and len(data) >= offset + 3:
        med_id = data[offset]
        ctx.medication_id = MEDICATION_LABEL.get(med_id, f"Unknown(0x{med_id:X})")
        offset += 1
        (med_raw,) = struct.unpack_from("<H", data, offset)
        ctx.medication_amount = _sfloat_to_float(med_raw)
        ctx.medication_unit   = "L" if (flags & CTX_FLAG_MEDICATION_LITERS) else "kg"
        offset += 2

    if flags & CTX_FLAG_HBA1C and len(data) >= offset + 2:
        (hba1c_raw,) = struct.unpack_from("<H", data, offset)
        val = _sfloat_to_float(hba1c_raw)
        # HbA1c is transmitted as a fraction (e.g. 0.055 = 5.5%)
        ctx.hba1c_pct = round(val * 100, 2) if val is not None else None

    return ctx


def parse_racp_response(data: bytes) -> tuple[int, int, int]:
    """Parse an RACP indication from 0x2A52.

    Returns (response_op_code, request_op_code, response_code).
    response_op_code == 0x06 means this is a General Response.
    response_code    == 0x01 means Success.
    """
    if len(data) < 4:
        raise ValueError(f"RACP response too short: {len(data)} bytes")
    return data[0], data[2], data[3]
