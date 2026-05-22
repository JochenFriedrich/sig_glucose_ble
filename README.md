# 🩸 SIG Glucose Meter BLE — Home Assistant Integration

A **local_push** custom integration for Home Assistant supporting any BLE glucose meter
implementing the [Bluetooth SIG Glucose Service](https://www.bluetooth.com/specifications/specs/glucose-service/)
(UUID `0x1808`).

## Compatible Devices

Any device advertising the Glucose Service (`0x1808`) should work, including:

| Brand        | Example models                    |
|--------------|-----------------------------------|
| Contour Next | Contour Next One, Contour Plus    |
| Accu-Chek    | Guide, Instant                    |
| OneTouch     | Verio Reflect, Verio Flex         |
| Beurer       | GL 50 evo, GL 44                  |
| iHealth      | Smart Gluco-Monitoring System     |

---

## Installation

### Manual

```bash
cp -r custom_components/sig_glucose_ble /config/custom_components/
```

Restart Home Assistant.

---

## Setup

1. Take a measurement on your device — it advertises briefly after each reading.
2. HA discovers the device and shows a notification → click **Configure**.

Or go to **Settings → Devices & Services → Add Integration → SIG Glucose Meter BLE**.

---

## How It Works (RACP Protocol)

Unlike blood pressure monitors, glucose meters use an explicit command/response
protocol called the **Record Access Control Point (RACP)**:

```
Connect + pair/bond
        │
        ▼
Subscribe to 0x2A18 (Glucose Measurement) notifications
Subscribe to 0x2A34 (Context) notifications    [optional]
Subscribe to 0x2A52 (RACP) indications
        │
        ▼
Write RACP command: [0x01, 0x01] = Report All Stored Records
        │
        ▼
Device streams all records as 0x2A18 notifications
Each record optionally followed by matching 0x2A34 context
(linked by Sequence Number)
        │
        ▼
Device sends RACP indication [0x06, 0x00, 0x01, 0x01] = Success
        │
        ▼
Link contexts → measurements by Sequence Number
Publish most-recent record to HA sensors
```

---

## Sensors Created

| Entity | Unit | Notes |
|--------|------|-------|
| `sensor.<name>_glucose` | mmol/L | Primary reading |
| `sensor.<name>_glucose_mg_dl` | mg/dL | Always present |
| `sensor.<name>_record_sequence_number` | — | Monotonic counter |
| `sensor.<name>_last_measurement_time` | — | Device timestamp |
| `sensor.<name>_sample_type` | — | e.g. Capillary Whole Blood |
| `sensor.<name>_sample_location` | — | e.g. Finger |
| `sensor.<name>_device_battery_low` | — | Status flag |
| `sensor.<name>_sensor_malfunction` | — | Status flag |
| `sensor.<name>_result_too_high` | — | Status flag |
| `sensor.<name>_result_too_low` | — | Status flag |
| `sensor.<name>_meal` | — | Context: preprandial/postprandial/fasting |
| `sensor.<name>_tester` | — | Context: self/HCP/lab |
| `sensor.<name>_health_status` | — | Context: health notes |
| `sensor.<name>_hba1c` | % | Context: if reported |
| `sensor.<name>_medication` | — | Context: insulin type |

Context sensors are only `available` when the device reports context data.

---

## Automation Example

```yaml
alias: Notify high glucose
trigger:
  - platform: numeric_state
    entity_id: sensor.my_glucometer_glucose
    above: 10.0
action:
  - service: notify.mobile_app_my_phone
    data:
      title: "⚠️ High Glucose"
      message: >
        {{ states('sensor.my_glucometer_glucose') }} mmol/L
        ({{ states('sensor.my_glucometer_glucose_mg_dl') }} mg/dL)
        — {{ states('sensor.my_glucometer_meal') }}
```

---

## Running Tests

```bash
pip install pytest
cd sig_glucose_ble
pytest tests/ -v
```

---

## Key Differences from Blood Pressure Integration

| | Blood Pressure (0x1810) | Glucose (0x1808) |
|---|---|---|
| Record retrieval | Automatic on connect | Must write RACP command |
| Transfer end signal | Idle timeout / disconnect | RACP success indication |
| Context data | None | Optional 0x2A34 linked by seq# |
| Units | mmHg / kPa | mmol/L and mg/dL (both always exposed) |
| History stored | Yes (auto-sent) | Yes (requested via RACP) |

---

## License

MIT
