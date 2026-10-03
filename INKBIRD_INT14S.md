# INT-14S-BW food-high targets and Time to Temp

## Install

For the complete ZIP, extract it and copy the entire
`custom_components/tuya_local` folder into Home Assistant's
`config/custom_components`, replacing the existing Tuya Local folder. The archive
also includes repository documentation and tests; those do not need installing.

For a manual update from the earlier test build:

Copy these seven runtime files to the matching paths under Home Assistant's
`config/custom_components/tuya_local` directory:

- `device.py`
- `number.py`
- `sensor.py`
- `text.py`
- `helpers/device_config.py`
- `helpers/inkbird.py`
- `devices/inkbird_int14sbw_thermometer.yaml`

Fully restart Home Assistant. Each probe now has a **food high target** number
control and a **time to temp** duration sensor. The previous JSON text controls
are no longer exposed. Home Assistant may retain unavailable registry entries
for the old controls; remove those old entries through entity settings.

## Food-high controls

Set the target temperature normally in the number control. Native values are
Fahrenheit; Home Assistant's temperature conversion handles displayed units.
Each update builds the equivalent of:

```json
{"food_high":165,"food_low":null,"ambient_high":null,"ambient_low":null}
```

Only food-high is enabled. Every write disables food-low and both ambient alarms
for that probe. The integration sends a complete 20-byte command without a CRC;
reported target packets require 21 bytes with a valid CRC. Valid reported packet
metadata is preserved. With no report, metadata defaults to no food presets,
zero degree/pre-alarm/reserved fields, and the current timestamp.

The last requested food-high value is restored across Home Assistant restarts.
The number's `value_source` attribute distinguishes `last_requested` from
`device_report`. The owner confirmed an actual Probe 1 high alarm after setting
a target below the current temperature. Device readback can remain absent.

## Time to Temp

One sensor per probe estimates minutes until its food-high target is reached.
It uses **food temperature channel 1**, consistently in native Fahrenheit, and
linear regression over the latest five minutes of samples. It requires at least
three readings spanning one minute. Estimates adjust when the target changes.

The sensor returns zero when the target is reached. It is unknown during warmup,
missing targets, disconnected probes, flat/cooling trends, or when no temperature
packet has arrived for over 90 seconds. Disconnects and long gaps reset the
trend. The trend starts fresh after restart; it is not reconstructed from history.

These are rolling estimates, not a cooking model: stalls and slowing heating
change the estimate. For example, 110 °F rising 2 °F/min toward 130 °F gives
10 minutes. With no target readback, it uses the restored/requested food-high
value, so edits in another app cannot be detected unless the station reports them.

## Other readings and polling

The existing 27 read-only temperature, battery and brightness sensors remain.
There are now four additional duration sensors, four food-high number controls,
and the existing brightness number control. Only DP104 is required for matching
initial setup. The owner reports product ID `bozmpl04yva3x0sa`; protocol 3.5 works.

Each ordinary Inkbird cycle requests status and forced datapoints, then waits
about ten seconds, plus response time. Writes request readback after about two
seconds. At startup, a battery-only DP103 request precedes the larger forced
request. Missing, unknown, or invalid battery packets trigger extra reads about
every three seconds, bounded to ten attempts within the first minute. Extra
reads stop as soon as all five percentages are valid. Actual response timing
depends on the station; this does not show restored values as fresh battery data.
Other Tuya Local profiles retain their original polling behavior.

## Validation

97 focused tests pass, covering packet layout, disabled alarms, native target
restoration, trend estimates, stale/missing readings, configuration and polling.
Ruff and YAML validation pass, with one pre-existing unrelated YAML warning.
The full Home Assistant pytest plugin needs Unix fcntl and cannot load on this
Windows environment. Time to Temp still needs validation on the real station.

Protocol reference: [zampix1/ha-inkbird-int14](https://github.com/zampix1/ha-inkbird-int14).
This build is published to the owner's fork alongside a complete source ZIP.
