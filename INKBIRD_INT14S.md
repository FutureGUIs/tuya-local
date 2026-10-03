# Inkbird INT-14S-BW LAN readings and brightness control

This fork adds a first implementation of local Wi-Fi readings for the
INT-14S-BW. It has been checked against published protocol captures and synthetic
packets. The owner has confirmed temperature and battery readings over LAN; individual channel accuracy and long-term reliability still need checking.

## Entities

- Four food temperatures and one ambient temperature per probe (20 total).
- Station temperature.
- Station battery and four probe battery percentages.
- Reported display brightness, as a sensor.

The 27 sensor entities remain read-only. A Display brightness number entity
adds an experimental writable control for DP104, from 1 to 100 percent.
There are no target-temperature controls, BLE connections or cloud-history requests.
Battery and brightness sensors are diagnostic entities.

## Install and try

1. Back up the existing Tuya Local installation and its configuration.
2. Download this fork and replace the complete `custom_components/tuya_local`
   directory in Home Assistant. A YAML-only copy is insufficient: the decoder
   also needs the new packet-validation support in `helpers/device_config.py`.
3. Restart Home Assistant.
4. Add the station through Tuya Local using its LAN address, Tuya device ID and
   local key. Try protocol 3.5, which the reference integration uses.
5. Select the configuration `inkbird_int14sbw_thermometer`
   (Multisensor BBQ thermometer).
6. Compare all channels with the Inkbird app while heating one probe at a time.

Do not run another local Tuya integration against the same station concurrently;
many devices only allow one LAN connection.

The owner's setup capture contains DP101 (`F`), DP102 (`true`) and DP104 (`81`),
without temperatures or batteries. Only brightness DP104 is required for matching;
DP109 and DP103 are optional so setup can finish before temperature packets arrive.
The product ID is `bozmpl04yva3x0sa`, reported by the owner. If this configuration is not offered, capture the
`LOCAL DPS` warning during setup. DP109 and DP103 request
explicit read updates (`updatedps`); only an explicit brightness change writes a setting. Refresh requests deduplicate shared datapoints, so the 27 entities request only `[109, 103]`.

## Decoding

DP109 must be 55 decoded bytes: four 13-byte probe blocks, a two-byte station
temperature and a trailing CRC-8/ATM byte. Food channels use signed little-endian
Fahrenheit hundredths. Ambient and station readings use Fahrenheit tenths.
Home Assistant can display these in Celsius using its normal unit conversion.
Probe sentinel values are mapped to unknown rather than extreme temperatures.
DP103 must be six bytes: five battery values and a CRC byte; 127 means unknown.
Invalid lengths, bad Base64 and incorrect checksums return unknown values.

Protocol layout and the published test capture come from the MIT-licensed
[zampix1/ha-inkbird-int14](https://github.com/zampix1/ha-inkbird-int14), specifically
`protocol.py` and `tests/test_int11i_protocol.py`. This is an independent device
configuration with a generic read-only CRC validation helper.

The product ID is registered as Inkbird INT-14S-BW.
Charging flags, alarm states and target-temperature controls remain outside this version.

## Validation

The device configuration and decoder tests cover the published LAN capture,
all 20 channel offsets, signed readings, disconnected probes, battery values,
missing values, bad encoding, packet lengths, checksum corruption and the
read-only command maps. Lint uses the repository's Ruff and yamllint settings.

On Windows, the normal Home Assistant pytest plugin cannot load because it
requires the Unix `fcntl` module. The configuration and decoder tests can run
with plugin autoload disabled and `pytest_mock` plus `pytest_asyncio.plugin`
loaded explicitly. Temperature refresh requests currently alternate with status polling at 30-second intervals, so requested readings can update about once per minute. Full integration tests should run in the repository's Linux
GitHub Actions environment before treating this as hardware-validated support.

## Test the brightness control

After updating the full integration folder and restarting Home Assistant, open
the existing device and find the Display brightness number entity under
configuration controls. Record its current value (81 in the owner capture),
set it to 50, confirm the physical display changes, then restore the original
value. Allow the separate brightness sensor to update from a later device
report; Tuya Local may show a pending requested value immediately, so the UI
alone does not prove the station accepted the write. Brightness writes have
not yet been hardware validated on this station.
