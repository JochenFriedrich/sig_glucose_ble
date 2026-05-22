"""Coordinator for SIG Glucose Meter BLE integration.

Protocol flow (SIG Glucose Profile v1.0.1)
──────────────────────────────────────────
The Glucose service uses an explicit command/response protocol
via the Record Access Control Point (RACP, 0x2A52):

  1. Subscribe to notifications on 0x2A18 (Glucose Measurement).
  2. Subscribe to notifications on 0x2A34 (Glucose Measurement Context, optional).
  3. Subscribe to indications  on 0x2A52 (RACP response).
  4. Write RACP command [0x01, 0x01] = "Report All Stored Records" to 0x2A52.
  5. Device streams all records as notifications on 0x2A18, each optionally
     followed by a matching context notification on 0x2A34 (linked by sequence#).
  6. When done, device sends a RACP indication on 0x2A52:
       [0x06, 0x00, 0x01, 0x01] = Response | Null | ReportStoredRecords | Success
  7. We publish the most-recent record (by timestamp or sequence number).

Err-6 / hang fix (same lesson as BP)
─────────────────────────────────────
Never disconnect until the RACP success indication arrives OR the inactivity
timer fires.  Disconnecting early leaves records unacknowledged and causes
the device to display errors or refuse future connections.

Context linking
───────────────
0x2A34 context records reference their parent 0x2A18 record via a shared
Sequence Number (uint16).  We buffer all received measurements and contexts
then link them by sequence number at publish time.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from datetime import timedelta
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_clear_advertisement_history,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    GLUCOSE_MEASUREMENT_UUID,
    GLUCOSE_CONTEXT_UUID,
    GLUCOSE_SERVICE_UUID,
    RACP_UUID,
    RACP_OP_REPORT_STORED_RECORDS,
    RACP_OPERATOR_ALL,
    RACP_OP_RESPONSE,
    RACP_RESPONSE_SUCCESS,
    RACP_RESPONSE_NO_RECORDS,
    CONNECT_TIMEOUT,
    PAIR_TIMEOUT,
    RACP_WRITE_TIMEOUT,
    FIRST_RECORD_TIMEOUT,
    RACP_RESPONSE_TIMEOUT,
    IDLE_AFTER_LAST_RECORD_TIMEOUT,
    MAX_RETRIES,
)
from .parser import (
    GlucoseMeasurement,
    GlucoseMeasurementContext,
    parse_glucose_measurement,
    parse_glucose_context,
    parse_racp_response,
)

_LOGGER = logging.getLogger(__name__)

# Poll interval is very long; real updates arrive via BLE advertisement callbacks.
_POLL_INTERVAL = timedelta(hours=24)

# RACP "Report All Stored Records" command bytes
_RACP_REPORT_ALL = bytes([RACP_OP_REPORT_STORED_RECORDS, RACP_OPERATOR_ALL])


def _is_auth_error(exc: BleakError) -> bool:
    """Return True if the BleakError is an authentication/encryption failure."""
    msg = str(exc).lower()
    return (
        "insufficient authentication" in msg
        or "insufficient encryption" in msg
        or "insufficient authorization" in msg
        or "error=5" in msg
        or "error=8" in msg
        or "error=15" in msg
    )


class GlucoseCoordinator(DataUpdateCoordinator[GlucoseMeasurement | None]):
    """Coordinate BLE connections and data parsing for a single glucose meter."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{address}",
            update_interval=_POLL_INTERVAL,
        )
        self.address = address
        self.device_name = name
        self._connecting = False
        self._last_measurement: GlucoseMeasurement | None = None
        # Track whether we successfully paired this session so we can skip
        # the pair() call on subsequent connects (already bonded in BlueZ).
        self._paired_successfully: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def handle_advertisement(self, service_info: Any) -> None:
        if self._connecting:
            _LOGGER.debug(
                "[%s] Advertisement received but already connecting – skipping",
                self.address,
            )
            return
        _LOGGER.debug("[%s] Advertisement received – scheduling connection", self.address)
        self.hass.async_create_task(self._connect_and_read())

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _connect_and_read(self) -> None:
        """Connect to the device, pair if needed, collect a reading, then clean up."""
        self._connecting = True
        try:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    await self._do_connect_and_read()
                    return
                except (BleakError, asyncio.TimeoutError, OSError) as err:
                    _LOGGER.warning(
                        "[%s] Connection attempt %d/%d failed: %s",
                        self.address, attempt, MAX_RETRIES, err,
                    )
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(2 ** attempt)  # exponential back-off
        finally:
            self._connecting = False
            # Clear HA's advertisement deduplication state so we get called
            # again on the next measurement, even if the adv payload is identical.
            try:
                async_clear_advertisement_history(self.hass, self.address)
            except Exception:  # noqa: BLE001
                pass  # API may not exist on older HA versions — silently ignore

    async def _do_connect_and_read(self) -> None:
        """Full RACP session: subscribe → write command → drain → publish."""
        ble_device: BLEDevice | None = async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise BleakError(f"Device {self.address} not found in HA BLE cache")

        # ── Shared session state ──────────────────────────────────────────────
        received_measurements: dict[int, GlucoseMeasurement] = {}   # seq# → record
        received_contexts:     dict[int, GlucoseMeasurementContext] = {}  # seq# → ctx
        first_record_event: asyncio.Event = asyncio.Event()
        racp_done_event:    asyncio.Event = asyncio.Event()
        idle_handle: list[asyncio.TimerHandle | None] = [None]
        racp_result: list[int] = [RACP_RESPONSE_SUCCESS]  # mutable cell

        def _reschedule_idle() -> None:
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            idle_handle[0] = asyncio.get_event_loop().call_later(
                IDLE_AFTER_LAST_RECORD_TIMEOUT, racp_done_event.set
            )

        def _measurement_handler(sender: Any, data: bytearray) -> None:
            """Handle a 0x2A18 Glucose Measurement notification."""
            _LOGGER.debug(
                "[%s] Glucose notification handle=%s data=%s",
                self.address, sender, data.hex(),
            )
            _reschedule_idle()
            try:
                m = parse_glucose_measurement(bytes(data))
                received_measurements[m.sequence_number] = m
                _LOGGER.debug(
                    "[%s] Record seq#%d: %.2f mmol/L (%.1f mg/dL) @ %s",
                    self.address, m.sequence_number,
                    m.glucose_mmol_l or 0, m.glucose_mg_dl or 0, m.timestamp,
                )
                first_record_event.set()
            except ValueError as exc:
                _LOGGER.warning("[%s] Failed to parse glucose measurement: %s", self.address, exc)

        def _context_handler(sender: Any, data: bytearray) -> None:
            """Handle a 0x2A34 Glucose Measurement Context notification."""
            _LOGGER.debug(
                "[%s] Context notification handle=%s data=%s",
                self.address, sender, data.hex(),
            )
            _reschedule_idle()
            try:
                ctx = parse_glucose_context(bytes(data))
                received_contexts[ctx.sequence_number] = ctx
                _LOGGER.debug(
                    "[%s] Context seq#%d: meal=%s tester=%s",
                    self.address, ctx.sequence_number, ctx.meal, ctx.tester,
                )
            except ValueError as exc:
                _LOGGER.warning("[%s] Failed to parse glucose context: %s", self.address, exc)

        def _racp_handler(sender: Any, data: bytearray) -> None:
            """Handle a 0x2A52 RACP indication — signals end of transfer."""
            _LOGGER.debug(
                "[%s] RACP indication handle=%s data=%s",
                self.address, sender, data.hex(),
            )
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            try:
                op, req_op, response_code = parse_racp_response(bytes(data))
                racp_result[0] = response_code
                _LOGGER.info(
                    "[%s] RACP response: op=0x%02X req=0x%02X code=0x%02X",
                    self.address, op, req_op, response_code,
                )
            except ValueError as exc:
                _LOGGER.warning("[%s] Could not parse RACP response: %s", self.address, exc)
            finally:
                racp_done_event.set()

        def _disconnected_callback(_client: BleakClient) -> None:
            _LOGGER.debug("[%s] Device disconnected – signalling done", self.address)
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            racp_done_event.set()

        # ── Connect ───────────────────────────────────────────────────────────
        _LOGGER.info("[%s] Connecting …", self.address)
        async with BleakClient(
            ble_device, 
            timeout=CONNECT_TIMEOUT,
            disconnected_callback=_disconnected_callback,
        ) as client:
            _LOGGER.info("[%s] Connected. Checking bond/authentication …", self.address)

            # ── Pairing / bonding ─────────────────────────────────────────────
            # Many SIG GLUCOSE meters return GATT error 5 (Insufficient Authentication)
            # if we try to write the CCCD (enable indications) without a bonded,
            # encrypted link.  We call pair() here, before any GATT operation.
            #
            # BlueZ behaviour:
            #   • Already bonded  → pair() is a fast no-op (returns immediately).
            #   • Not bonded      → triggers SMP; device shows "just works" confirm
            #                       or numeric comparison; BlueZ stores the LTK.
            #
            # ESPHome proxy behaviour:
            #   • pair() raises NotImplementedError or BleakError – we catch it
            #     and warn the user to pre-pair via bluetoothctl if needed.
            _LOGGER.info("[%s] Connected – checking bond/auth …", self.address)
            await self._ensure_paired(client)

            # ── Resolve handles from the Glucose Service tree ─────────────────
            # Never pass UUID strings to start_notify / write_gatt_char when the
            # device may expose the same UUID in multiple services (e.g. 0x2A52
            # RACP is shared by Glucose and Continuous Glucose Monitoring).
            # Walking the service tree and using integer handles is unambiguous.
            handles = self._resolve_characteristics(client)
            _LOGGER.debug("[%s] Resolved handles: %s", self.address, handles)

            # ── Step 1: Subscribe to Glucose Measurement notifications ────────
            _LOGGER.info("[%s] Enabling Glucose Measurement notifications …", self.address)
            try:
                await client.start_notify(handles["measurement"], _measurement_handler)
            except BleakError as exc:
                if _is_auth_error(exc):
                    # Pairing succeeded (or was already done) but the link is
                    # still not encrypted on this specific connect — very rare,
                    # but can happen if BlueZ key store is stale.  Raise so the
                    # retry loop can try a fresh connection + pair again.
                    raise BleakError(
                        f"[{self.address}] GATT authentication error after pairing. "
                        "Try removing the device from bluetoothctl and re-pairing: "
                        f"{exc}"
                    ) from exc
                raise

            # ── Step 2: Subscribe to Glucose Context notifications (optional) ─
            context_available = False
            if handles.get("context") is not None:
                try:
                    await client.start_notify(handles["context"], _context_handler)
                    context_available = True
                    _LOGGER.debug("[%s] Glucose Context notifications enabled", self.address)
                except BleakError:
                    _LOGGER.debug(
                        "[%s] Glucose Context subscribe failed (optional)", self.address
                    )
            else:
                _LOGGER.debug("[%s] No Glucose Context characteristic found (optional)", self.address)

            # ── Step 3: Subscribe to RACP indications ─────────────────────────
            _LOGGER.info("[%s] Enabling RACP indications …", self.address)
            await client.start_notify(handles["racp"], _racp_handler)

            # ── Step 4: Write RACP "Report All Stored Records" ────────────────
            # Must write with response (write_gatt_char default) so GATT confirms
            # the write before the device starts streaming.
            _LOGGER.info("[%s] Writing RACP: Report All Stored Records …", self.address)
            try:
                await asyncio.wait_for(
                    client.write_gatt_char(handles["racp"], _RACP_REPORT_ALL, response=True),
                    timeout=RACP_WRITE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise BleakError(
                    f"[{self.address}] Timed out writing RACP command "
                    f"(device may not support write-with-response on RACP)"
                )

            # ── Step 5: Wait for the first glucose record ─────────────────────
            # If no records arrive, the device likely has none stored.
            # The RACP will still send a response (code 0x06 = No Records Found).
            # We wait for EITHER the first record OR the RACP done event,
            # whichever comes first.
            _LOGGER.debug("[%s] Waiting for first glucose record or RACP response …", self.address)
            first_or_done = asyncio.ensure_future(
                asyncio.wait(
                    {
                        asyncio.ensure_future(first_record_event.wait()),
                        asyncio.ensure_future(racp_done_event.wait()),
                    },
                    return_when=asyncio.FIRST_COMPLETED,
                )
            )
            try:
                await asyncio.wait_for(first_or_done, timeout=FIRST_RECORD_TIMEOUT)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "[%s] No glucose records or RACP response within %ds",
                    self.address, FIRST_RECORD_TIMEOUT,
                )
                if idle_handle[0] is not None:
                    idle_handle[0].cancel()
                return

            # ── Step 6: Drain all remaining records until RACP signals done ───
            if not racp_done_event.is_set():
                _LOGGER.debug(
                    "[%s] First record received – draining remaining history …", self.address
                )
                try:
                    await asyncio.wait_for(
                        racp_done_event.wait(), timeout=RACP_RESPONSE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "[%s] RACP response not received within %ds – "
                        "proceeding with %d records collected so far",
                        self.address, RACP_RESPONSE_TIMEOUT, len(received_measurements),
                    )

            if idle_handle[0] is not None:
                idle_handle[0].cancel()

            # ── Step 7: Handle RACP result codes ─────────────────────────────
            if racp_result[0] == RACP_RESPONSE_NO_RECORDS:
                _LOGGER.info("[%s] Device reports no stored records", self.address)
                return
            if racp_result[0] not in (RACP_RESPONSE_SUCCESS, RACP_RESPONSE_NO_RECORDS):
                _LOGGER.warning(
                    "[%s] RACP response code 0x%02X (non-success) – "
                    "proceeding with %d records if any collected",
                    self.address, racp_result[0], len(received_measurements),
                )

            _LOGGER.info(
                "[%s] Transfer complete – %d record(s), %d context(s) received",
                self.address, len(received_measurements), len(received_contexts),
            )

            # Graceful stop (ignored if device already disconnected)
            stop_handles = [handles["measurement"], handles["racp"]]
            if context_available and handles.get("context") is not None:
                stop_handles.append(handles["context"])
            for h in stop_handles:
                try:
                    await client.stop_notify(h)

                except BleakError:
                    pass

        # ── Link contexts to measurements ─────────────────────────────────────
        for seq, ctx in received_contexts.items():
            if seq in received_measurements:
                received_measurements[seq].context = ctx

        # ── Publish the most-recent record ────────────────────────────────────
        if not received_measurements:
            return

        # Sort by effective timestamp; fall back to sequence number (monotonic)
        measurements = list(received_measurements.values())
        measurements_with_ts = [m for m in measurements if m.timestamp is not None]
        latest = (
            max(measurements_with_ts, key=lambda m: m.timestamp)  # type: ignore[arg-type]
            if measurements_with_ts
            else max(measurements, key=lambda m: m.sequence_number)
        )

        _LOGGER.info(
            "[%s] ✓ Publishing latest of %d record(s): "
            "seq#%d %.2f mmol/L (%.1f mg/dL) type=%s loc=%s ts=%s",
            self.address,
            len(received_measurements),
            latest.sequence_number,
            latest.glucose_mmol_l or 0,
            latest.glucose_mg_dl or 0,
            latest.sample_type,
            latest.sample_location,
            latest.timestamp,
        )
        self._last_measurement = latest
        self.async_set_updated_data(latest)

    def _resolve_characteristics(self, client: BleakClient) -> dict[str, int]:
        """Walk the GATT service tree and return integer handles for each characteristic.

        Using integer handles instead of UUID strings with start_notify /
        write_gatt_char avoids the bleak error:
            "Multiple Characteristics with this UUID, refer to your desired
             characteristic by the `handle` attribute instead."

        This happens when a device exposes the same UUID in more than one GATT
        service — for example 0x2A52 (RACP) appears in both Glucose (0x1808) and
        Continuous Glucose Monitoring (0x181F).  By walking to the Glucose Service
        first and picking the handle from within it, we are always unambiguous.

        Returns a dict with keys: "measurement", "racp", and optionally "context".
        Raises BleakError if the mandatory characteristics are not found.
        """
        # Normalise a UUID string to lowercase for comparison
        def _norm(uuid: str) -> str:
            return str(uuid).lower()

        glucose_svc_uuid  = _norm(GLUCOSE_SERVICE_UUID)
        measurement_uuid  = _norm(GLUCOSE_MEASUREMENT_UUID)
        context_uuid      = _norm(GLUCOSE_CONTEXT_UUID)
        racp_uuid         = _norm(RACP_UUID)

        handles: dict[str, int | None] = {
            "measurement": None,
            "context":     None,
            "racp":        None,
        }

        # First pass: look only inside the Glucose Service
        for svc in client.services:
            if _norm(svc.uuid) != glucose_svc_uuid:
                continue
            _LOGGER.debug("[%s] Found Glucose Service (handle 0x%04X)", self.address, svc.handle)
            for char in svc.characteristics:
                u = _norm(char.uuid)
                if u == measurement_uuid:
                    handles["measurement"] = char.handle
                    _LOGGER.debug("[%s]  measurement handle=0x%04X", self.address, char.handle)
                elif u == context_uuid:
                    handles["context"] = char.handle
                    _LOGGER.debug("[%s]  context     handle=0x%04X", self.address, char.handle)
                elif u == racp_uuid:
                    handles["racp"] = char.handle
                    _LOGGER.debug("[%s]  racp        handle=0x%04X", self.address, char.handle)

        # Second pass: if we still missed anything, try all services as a fallback
        # (some devices put RACP in a vendor service but still use the SIG UUID)
        if None in (handles["measurement"], handles["racp"]):
            _LOGGER.debug(
                "[%s] Glucose Service incomplete — scanning all services as fallback",
                self.address,
            )
            for svc in client.services:
                for char in svc.characteristics:
                    u = _norm(char.uuid)
                    if u == measurement_uuid and handles["measurement"] is None:
                        handles["measurement"] = char.handle
                        _LOGGER.debug(
                            "[%s]  fallback measurement handle=0x%04X svc=%s",
                            self.address, char.handle, svc.uuid,
                        )
                    elif u == context_uuid and handles["context"] is None:
                        handles["context"] = char.handle
                    elif u == racp_uuid and handles["racp"] is None:
                        handles["racp"] = char.handle
                        _LOGGER.debug(
                            "[%s]  fallback racp handle=0x%04X svc=%s",
                            self.address, char.handle, svc.uuid,
                        )

        # Validate mandatory characteristics
        missing = [k for k in ("measurement", "racp") if handles[k] is None]
        if missing:
            # Emit a full service dump to help diagnose unusual devices
            svc_dump = "; ".join(
                f"{svc.uuid}:[{', '.join(c.uuid for c in svc.characteristics)}]"
                for svc in client.services
            )
            raise BleakError(
                f"[{self.address}] Required Glucose characteristics not found: "
                f"{missing}. Device services: {svc_dump}"
            )

        return handles  # type: ignore[return-value]  # missing keys already checked above

    async def _ensure_paired(self, client: BleakClient) -> None:
        """Attempt to pair/bond with the device; handle proxies and failures gracefully.

        On BlueZ (Linux / HAOS):
          - If already bonded, pair() returns almost instantly.
          - If not bonded, BlueZ performs the SMP exchange ("Just Works" for most
            BP monitors) and stores the Long Term Key (LTK) for future connections.

        On ESPHome Bluetooth Proxies:
          - pair() raises NotImplementedError or BleakError.  We log a warning
            and continue; if the device is already bonded at the adapter level
            the GATT ops will succeed anyway.
        """
        try:
            _LOGGER.debug("[%s] Calling client.pair() …", self.address)
            await asyncio.wait_for(client.pair(), timeout=PAIR_TIMEOUT)
            _LOGGER.info("[%s] Paired/bonded successfully (or already bonded)", self.address)
            self._paired_successfully = True
        except NotImplementedError:
            # ESPHome proxy backend does not implement pair()
            _LOGGER.debug(
                "[%s] pair() not supported on this backend (ESPHome proxy?). "
                "Continuing without explicit pairing.",
                self.address,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "[%s] Pairing timed out after %ds. "
                "The device may require a button press to confirm pairing. "
                "Attempting to continue — GATT ops may fail with error=5.",
                self.address, PAIR_TIMEOUT,
            )
        except BleakError as exc:
            # pair() itself failed (e.g. already paired and BlueZ returned fast,
            # or the ESPHome proxy raised BleakError instead of NotImplementedError).
            if "already" in str(exc).lower() or "paired" in str(exc).lower():
                _LOGGER.debug("[%s] Device reports already paired: %s", self.address, exc)
                self._paired_successfully = True
            else:
                _LOGGER.warning(
                    "[%s] pair() failed: %s. "
                    "If you see GATT error=5 next, run: "
                    "bluetoothctl; agent on; pair %s",
                    self.address, exc, self.address,
                )

    async def _async_update_data(self) -> GlucoseMeasurement | None:
        return self._last_measurement
