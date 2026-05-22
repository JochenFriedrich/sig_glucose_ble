"""Constants for the SIG Glucose Meter BLE integration."""

DOMAIN = "sig_glucose_ble"

# ── Bluetooth SIG standard UUIDs ──────────────────────────────────────────────
# Glucose Service
GLUCOSE_SERVICE_UUID          = "00001808-0000-1000-8000-00805f9b34fb"
# Glucose Measurement characteristic (notify)
GLUCOSE_MEASUREMENT_UUID      = "00002a18-0000-1000-8000-00805f9b34fb"
# Glucose Measurement Context characteristic (notify, optional)
GLUCOSE_CONTEXT_UUID          = "00002a34-0000-1000-8000-00805f9b34fb"
# Glucose Feature characteristic (read)
GLUCOSE_FEATURE_UUID          = "00002a51-0000-1000-8000-00805f9b34fb"
# Record Access Control Point (indicate + write)
RACP_UUID                     = "00002a52-0000-1000-8000-00805f9b34fb"

# ── RACP Op Codes (octet 0 of RACP write) ─────────────────────────────────────
RACP_OP_REPORT_STORED_RECORDS = 0x01
RACP_OP_DELETE_STORED_RECORDS = 0x02
RACP_OP_ABORT                 = 0x03
RACP_OP_REPORT_NUM_RECORDS    = 0x04
RACP_OP_RESPONSE              = 0x06  # device sends this in response
RACP_OP_NUM_RECORDS_RESPONSE  = 0x05

# ── RACP Operators (octet 1) ───────────────────────────────────────────────────
RACP_OPERATOR_NULL            = 0x00
RACP_OPERATOR_ALL             = 0x01
RACP_OPERATOR_LESS_OR_EQUAL   = 0x02
RACP_OPERATOR_GREATER_OR_EQUAL= 0x03
RACP_OPERATOR_WITHIN_RANGE    = 0x04
RACP_OPERATOR_FIRST           = 0x05
RACP_OPERATOR_LAST            = 0x06

# ── RACP Response Codes (octet 3 of RACP_OP_RESPONSE) ─────────────────────────
RACP_RESPONSE_SUCCESS         = 0x01
RACP_RESPONSE_OP_NOT_SUPPORTED= 0x02
RACP_RESPONSE_INVALID_OPERATOR= 0x03
RACP_RESPONSE_OPERATOR_NOT_SUPPORTED = 0x04
RACP_RESPONSE_INVALID_OPERAND = 0x05
RACP_RESPONSE_NO_RECORDS      = 0x06
RACP_RESPONSE_ABORT_FAILED    = 0x07
RACP_RESPONSE_PROCEDURE_INCOMPLETE = 0x08
RACP_RESPONSE_OPERAND_NOT_SUPPORTED = 0x09

# ── Glucose Measurement flag bits (octet 0 of 0x2A18) ─────────────────────────
FLAG_TIME_OFFSET              = 0x01  # Time Offset field present
FLAG_CONCENTRATION_PRESENT    = 0x02  # Glucose Concentration + Type-Sample Location present
FLAG_CONCENTRATION_MOL        = 0x04  # 0=kg/L (mg/dL equiv), 1=mol/L (mmol/L)
FLAG_SENSOR_STATUS            = 0x08  # Sensor Status Annunciation field present
FLAG_CONTEXT_INFO             = 0x10  # Measurement Context follows (on 0x2A34)

# ── Glucose Measurement Context flag bits (octet 0 of 0x2A34) ─────────────────
CTX_FLAG_CARBOHYDRATE         = 0x01
CTX_FLAG_MEAL                 = 0x02
CTX_FLAG_TESTER_HEALTH        = 0x04
CTX_FLAG_EXERCISE             = 0x08
CTX_FLAG_MEDICATION           = 0x10
CTX_FLAG_MEDICATION_LITERS    = 0x20  # 0=kg, 1=liters
CTX_FLAG_HBA1C                = 0x40
CTX_FLAG_EXTENDED             = 0x80

# ── Human-readable lookup tables ──────────────────────────────────────────────
SAMPLE_TYPE = {
    0x01: "Capillary Whole Blood",
    0x02: "Capillary Plasma",
    0x03: "Venous Whole Blood",
    0x04: "Venous Plasma",
    0x05: "Arterial Whole Blood",
    0x06: "Arterial Plasma",
    0x07: "Undetermined Whole Blood",
    0x08: "Undetermined Plasma",
    0x09: "Interstitial Fluid",
    0x0A: "Control Solution",
}

SAMPLE_LOCATION = {
    0x01: "Finger",
    0x02: "Alternate Site Test",
    0x03: "Earlobe",
    0x04: "Control Solution",
    0x0F: "Not Available",
}

MEAL_LABEL = {
    0x01: "Preprandial",
    0x02: "Postprandial",
    0x03: "Fasting",
    0x04: "Casual",
    0x05: "Bedtime",
}

TESTER_LABEL = {
    0x01: "Self",
    0x02: "Health Care Professional",
    0x03: "Lab Test",
    0x0F: "Not Available",
}

HEALTH_LABEL = {
    0x00: "None",
    0x01: "Minor Health Issues",
    0x02: "Major Health Issues",
    0x03: "During Menses",
    0x04: "Under Stress",
    0x05: "No Health Issues",
    0x0F: "Not Available",
}

CARBOHYDRATE_LABEL = {
    0x01: "Breakfast",
    0x02: "Lunch",
    0x03: "Dinner",
    0x04: "Snack",
    0x05: "Drink",
    0x06: "Supper",
    0x07: "Brunch",
}

MEDICATION_LABEL = {
    0x01: "Rapid Acting Insulin",
    0x02: "Short Acting Insulin",
    0x03: "Intermediate Acting Insulin",
    0x04: "Long Acting Insulin",
    0x05: "Pre-Mixed Insulin",
}

# ── Unit strings ───────────────────────────────────────────────────────────────
UNIT_MOL_PER_L  = "mol/L"    # raw SI; HA converts to mmol/L for display
UNIT_KG_PER_L   = "kg/L"     # raw SI; multiply ×100000 → mg/dL

# ── Conversion factors ─────────────────────────────────────────────────────────
# SIG transmits mol/L as SFLOAT ≈ 1e-5 (e.g. 5.5 mmol/L = 5.5e-3 mol/L stored as 0.0055)
# SIG transmits kg/L as SFLOAT ≈ 1e-5 (e.g. 100 mg/dL = 0.001 kg/L)
MOL_TO_MMOL     = 1000.0     # mol/L → mmol/L
KG_L_TO_MG_DL   = 100000.0  # kg/L → mg/dL   (1 kg/L = 1e5 mg/dL)

# ── Timing ─────────────────────────────────────────────────────────────────────
CONNECT_TIMEOUT               = 15.0   # seconds
PAIR_TIMEOUT                  = 30.0   # seconds
RACP_WRITE_TIMEOUT            = 5.0    # seconds to wait for RACP write to complete
FIRST_RECORD_TIMEOUT          = 20.0   # seconds to wait for first glucose notification
RACP_RESPONSE_TIMEOUT         = 30.0   # seconds to wait for RACP success/error indication
IDLE_AFTER_LAST_RECORD_TIMEOUT= 3.0    # seconds of silence → transfer done
MAX_RETRIES                   = 3
