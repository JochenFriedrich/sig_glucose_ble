"""Unit tests for SIG Glucose Measurement (0x2A18) and Context (0x2A34) parsers."""
from __future__ import annotations

import struct
from datetime import datetime, timedelta

import pytest

from custom_components.sig_glucose_ble.parser import (
    parse_glucose_measurement,
    parse_glucose_context,
    parse_racp_response,
    _sfloat_to_float,
)
from custom_components.sig_glucose_ble.const import (
    FLAG_TIME_OFFSET, FLAG_CONCENTRATION_PRESENT, FLAG_CONCENTRATION_MOL,
    FLAG_SENSOR_STATUS, FLAG_CONTEXT_INFO,
    CTX_FLAG_MEAL, CTX_FLAG_TESTER_HEALTH, CTX_FLAG_HBA1C,
    RACP_OP_RESPONSE, RACP_RESPONSE_SUCCESS, RACP_RESPONSE_NO_RECORDS,
)


# ── SFLOAT helpers ──────────────────────────────────────────────────────────────

def _sfloat(mantissa: int, exponent: int) -> int:
    return ((exponent & 0x0F) << 12) | (mantissa & 0x0FFF)


def _encode_base_time(dt: datetime) -> bytes:
    return struct.pack("<HBBBBB", dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


# ── Packet builders ─────────────────────────────────────────────────────────────

def _build_glucose_packet(
    seq: int = 1,
    base_time: datetime | None = None,
    time_offset: int | None = None,
    concentration: float | None = None,  # in mmol/L if mol=True, mg/dL otherwise
    use_mol: bool = True,
    sample_type: int = 0x1,    # Capillary Whole Blood
    sample_location: int = 0x1,  # Finger
    sensor_status: int | None = None,
) -> bytes:
    if base_time is None:
        base_time = datetime(2025, 6, 1, 8, 0, 0)

    flags = 0
    if time_offset is not None:
        flags |= FLAG_TIME_OFFSET
    if concentration is not None:
        flags |= FLAG_CONCENTRATION_PRESENT
        if use_mol:
            flags |= FLAG_CONCENTRATION_MOL
    if sensor_status is not None:
        flags |= FLAG_SENSOR_STATUS

    data = bytearray()
    data.append(flags)
    data += struct.pack("<H", seq)
    data += _encode_base_time(base_time)

    if time_offset is not None:
        data += struct.pack("<h", time_offset)

    if concentration is not None:
        if use_mol:
            # mmol/L → mol/L stored as SFLOAT (e.g. 5.5 mmol/L = 0.0055 mol/L)
            raw_val = concentration / 1000.0
            # Encode as mantissa × 10^-5: val = mantissa × 1e-5
            mantissa = round(raw_val / 1e-5)
            conc_raw = _sfloat(mantissa, -5)  # exponent = -5
        else:
            # mg/dL → kg/L (e.g. 100 mg/dL = 0.001 kg/L)
            raw_val = concentration / 100000.0
            mantissa = round(raw_val / 1e-5)
            conc_raw = _sfloat(mantissa, -5)
        data += struct.pack("<H", conc_raw)
        type_loc = ((sample_type & 0xF) << 4) | (sample_location & 0xF)
        data.append(type_loc)

    if sensor_status is not None:
        data += struct.pack("<H", sensor_status)

    return bytes(data)


def _build_context_packet(
    seq: int = 1,
    meal: int | None = None,
    tester: int = 0x1,
    health: int = 0x5,
    hba1c_pct: float | None = None,
) -> bytes:
    flags = 0
    if meal is not None:
        flags |= CTX_FLAG_MEAL
    if tester or health:
        flags |= CTX_FLAG_TESTER_HEALTH
    if hba1c_pct is not None:
        flags |= CTX_FLAG_HBA1C

    data = bytearray()
    data.append(flags)
    data += struct.pack("<H", seq)

    if meal is not None:
        data.append(meal)
    if flags & CTX_FLAG_TESTER_HEALTH:
        data.append(((tester & 0xF) << 4) | (health & 0xF))
    if hba1c_pct is not None:
        # HbA1c fraction: 5.5% → 0.055, stored as SFLOAT mantissa × 1e-3
        frac = hba1c_pct / 100.0
        mantissa = round(frac / 1e-3)
        data += struct.pack("<H", _sfloat(mantissa, -3))

    return bytes(data)


# ── Parser tests ────────────────────────────────────────────────────────────────

class TestBasicGlucoseParsing:
    def test_mol_l_conversion(self):
        """5.5 mmol/L should round-trip through mol/L encoding."""
        pkt = _build_glucose_packet(concentration=5.5, use_mol=True)
        m = parse_glucose_measurement(pkt)
        assert m.is_valid
        assert m.glucose_mmol_l == pytest.approx(5.5, abs=0.05)
        assert m.glucose_mg_dl  == pytest.approx(5.5 * 18.016, abs=1.0)
        assert m.concentration_unit_raw == "mol/L"

    def test_kg_l_conversion(self):
        """100 mg/dL should round-trip through kg/L encoding."""
        pkt = _build_glucose_packet(concentration=100, use_mol=False)
        m = parse_glucose_measurement(pkt)
        assert m.is_valid
        assert m.glucose_mg_dl   == pytest.approx(100.0, abs=1.0)
        assert m.glucose_mmol_l  == pytest.approx(100 / 18.016, abs=0.1)
        assert m.concentration_unit_raw == "kg/L"

    def test_sequence_number(self):
        pkt = _build_glucose_packet(seq=42)
        m = parse_glucose_measurement(pkt)
        assert m.sequence_number == 42

    def test_base_time_parsed(self):
        ts = datetime(2025, 3, 15, 7, 30, 0)
        pkt = _build_glucose_packet(base_time=ts)
        m = parse_glucose_measurement(pkt)
        assert m.base_time == ts

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_glucose_measurement(b"\x00\x01\x00")

    def test_no_concentration_not_valid(self):
        pkt = _build_glucose_packet(concentration=None)
        m = parse_glucose_measurement(pkt)
        assert not m.is_valid
        assert m.glucose_mmol_l is None
        assert m.glucose_mg_dl is None

    def test_sample_type_and_location(self):
        pkt = _build_glucose_packet(
            concentration=6.0, sample_type=0x1, sample_location=0x1
        )
        m = parse_glucose_measurement(pkt)
        assert m.sample_type == "Capillary Whole Blood"
        assert m.sample_location == "Finger"


class TestTimeOffset:
    def test_positive_offset(self):
        base = datetime(2025, 1, 1, 12, 0, 0)
        pkt = _build_glucose_packet(base_time=base, time_offset=30)
        m = parse_glucose_measurement(pkt)
        assert m.time_offset_minutes == 30
        assert m.timestamp == base + timedelta(minutes=30)

    def test_negative_offset(self):
        base = datetime(2025, 1, 1, 12, 0, 0)
        pkt = _build_glucose_packet(base_time=base, time_offset=-15)
        m = parse_glucose_measurement(pkt)
        assert m.time_offset_minutes == -15
        assert m.timestamp == base + timedelta(minutes=-15)

    def test_no_offset_timestamp_equals_base(self):
        base = datetime(2025, 6, 1, 8, 0, 0)
        pkt = _build_glucose_packet(base_time=base)
        m = parse_glucose_measurement(pkt)
        assert m.timestamp == base


class TestSensorStatus:
    def test_result_too_high_flag(self):
        pkt = _build_glucose_packet(concentration=5.5, sensor_status=0x0020)
        m = parse_glucose_measurement(pkt)
        assert m.sensor_result_too_high is True
        assert m.sensor_result_too_low is False

    def test_battery_low_flag(self):
        pkt = _build_glucose_packet(concentration=5.5, sensor_status=0x0001)
        m = parse_glucose_measurement(pkt)
        assert m.device_battery_low is True

    def test_no_status_when_flag_not_set(self):
        pkt = _build_glucose_packet(concentration=5.5, sensor_status=None)
        m = parse_glucose_measurement(pkt)
        assert m.device_battery_low is None
        assert m.sensor_result_too_high is None


class TestContextParsing:
    def test_meal_parsed(self):
        pkt = _build_context_packet(seq=5, meal=0x01)  # Preprandial
        ctx = parse_glucose_context(pkt)
        assert ctx.sequence_number == 5
        assert ctx.meal == "Preprandial"

    def test_tester_health_parsed(self):
        pkt = _build_context_packet(tester=0x01, health=0x05)
        ctx = parse_glucose_context(pkt)
        assert ctx.tester == "Self"
        assert ctx.health == "No Health Issues"

    def test_hba1c_parsed(self):
        pkt = _build_context_packet(hba1c_pct=6.5)
        ctx = parse_glucose_context(pkt)
        assert ctx.hba1c_pct == pytest.approx(6.5, abs=0.1)

    def test_context_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_glucose_context(b"\x00\x01")


class TestRACPResponse:
    def test_success_response(self):
        data = bytes([RACP_OP_RESPONSE, 0x00, 0x01, RACP_RESPONSE_SUCCESS])
        op, req, code = parse_racp_response(data)
        assert op == RACP_OP_RESPONSE
        assert code == RACP_RESPONSE_SUCCESS

    def test_no_records_response(self):
        data = bytes([RACP_OP_RESPONSE, 0x00, 0x01, RACP_RESPONSE_NO_RECORDS])
        op, req, code = parse_racp_response(data)
        assert code == RACP_RESPONSE_NO_RECORDS

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_racp_response(b"\x06\x00")


class TestContextLinking:
    """Simulate the coordinator's context-linking logic inline."""

    def test_context_linked_by_sequence_number(self):
        from custom_components.sig_glucose_ble.parser import GlucoseMeasurementContext

        m_pkt = _build_glucose_packet(seq=7, concentration=7.2)
        m = parse_glucose_measurement(m_pkt)

        c_pkt = _build_context_packet(seq=7, meal=0x02)  # Postprandial
        ctx = parse_glucose_context(c_pkt)

        # Simulate coordinator linking
        measurements = {m.sequence_number: m}
        contexts = {ctx.sequence_number: ctx}
        for seq, c in contexts.items():
            if seq in measurements:
                measurements[seq].context = c

        assert measurements[7].context is not None
        assert measurements[7].context.meal == "Postprandial"
