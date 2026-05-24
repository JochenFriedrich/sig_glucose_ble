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

## Using ESPHome Bluetooth Proxy
When using an ESPHome Bluetooth Proxy, it might be neccessary to bond the device first.
As bonding is not supported by bluetooth_proxy, we need a temporary ble_client setup:

```yaml
#bluetooth_proxy:
#  active: true

api:
  actions:
    - action: passkey_reply
      variables:
        passkey: int
      then:
        - logger.log: "Authenticating with passkey"
        - ble_client.passkey_reply:
            id: sbm70
            passkey: !lambda return passkey;
    - action: numeric_comparison_reply
      variables:
        accept: bool
      then:
        - logger.log: "Authenticating with numeric comparison"
        - ble_client.numeric_comparison_reply:
            id: sbm70
            accept: !lambda return accept;

esp32_ble:
  io_capability: keyboard_display

ble_client:
  - mac_address: aa:bb:cc:dd:ee:ff
    id: sbm70
    on_passkey_request:
      then:
        - logger.log: "Enter the passkey displayed on your BLE device"
        - logger.log: " Go to https://my.home-assistant.io/redirect/developer_services/ and select passkey_reply"
    on_passkey_notification:
      then:
        - logger.log:
            format: "Enter this passkey on your BLE device: %06d"
            args: [ passkey ]
    on_numeric_comparison_request:
      then:
        - logger.log:
            format: "Compare this passkey with the one on your BLE device: %06d"
            args: [ passkey ]
        - logger.log: " Go to https://my.home-assistant.io/redirect/developer_services/ and select numeric_comparison_reply"
    on_connect:
      then:
        - logger.log: "Connected"
        - lambda: |-
            ESP_LOGE("custom", "Connected to SBM70, trying to pair");
            id(sbm70)->pair();
```

When bonding is completed, the ble_client can be deleted again and bluetooth_proxy enabled. It will now use the security
information stored in the ESP flash.

---

## License

MIT
