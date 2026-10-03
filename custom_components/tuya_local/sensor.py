"""
Setup for different kinds of Tuya sensors
"""

import json
import logging
from time import time

from homeassistant.components.sensor import (
    STATE_CLASSES,
    SensorDeviceClass,
    SensorEntity,
)

from .device import TuyaLocalDevice
from .entity import TuyaLocalEntity, unit_from_ascii
from .helpers.config import async_tuya_setup_platform
from .helpers.device_config import TuyaEntityConfig
from .helpers.inkbird import TemperatureTrend, targets_packet_text

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    config = {**config_entry.data, **config_entry.options}
    await async_tuya_setup_platform(
        hass,
        async_add_entities,
        config,
        "sensor",
        _sensor_entity,
    )


def _sensor_entity(device, config):
    if (
        config._device.config_type == "inkbird_int14sbw_thermometer"
        and config.name
        and config.name.endswith(" time to temp")
    ):
        return InkbirdTimeToTempSensor(device, config)
    return TuyaLocalSensor(device, config)


class TuyaLocalSensor(TuyaLocalEntity, SensorEntity):
    """Representation of a Tuya Sensor"""

    def __init__(self, device: TuyaLocalDevice, config: TuyaEntityConfig):
        """
        Initialise the sensor.
        Args:
            device (TuyaLocalDevice): the device API instance.
            config (TuyaEntityConfig): the configuration for this entity
        """
        super().__init__()
        dps_map = self._init_begin(device, config)
        self._sensor_dps = dps_map.pop("sensor", None)
        if self._sensor_dps is None:
            raise AttributeError(f"{config.config_id} is missing a sensor dps")
        self._unit_dps = dps_map.pop("unit", None)

        self._init_end(dps_map)

    @property
    def device_class(self):
        """Return the class of this device"""
        dclass = self._config.device_class
        if dclass:
            try:
                return SensorDeviceClass(dclass)
            except ValueError:
                _LOGGER.warning(
                    "%s/%s: Unrecognized sensor device class of %s ignored",
                    self._config._device.config,
                    self.name or "sensor",
                    dclass,
                )

    @property
    def state_class(self):
        """Return the state class of this entity"""
        sclass = self._sensor_dps.state_class
        if sclass in STATE_CLASSES:
            return sclass

    @property
    def native_value(self):
        """Return the value reported by the sensor"""
        return self._sensor_dps.get_value(self._device)

    @property
    def native_unit_of_measurement(self):
        """Return the unit for the sensor"""
        if self._unit_dps is None:
            unit = self._sensor_dps.unit
        else:
            unit = self._unit_dps.get_value(self._device)

        return unit_from_ascii(unit)

    @property
    def native_precision(self):
        """Return the precision for the sensor"""
        return self._sensor_dps.precision(self._device)

    @property
    def suggested_display_precision(self):
        """Return the suggested display precision for the sensor"""
        precision = self._sensor_dps.suggested_display_precision
        # if not explicitly defined, get based on scale
        # this is in line with older HA default behavior, and avoids
        # having to override the precision for every scaled sensor.
        if precision is None:
            precision = self._sensor_dps.precision(self._device)

        return precision

    @property
    def options(self):
        """Return a set of possible options."""
        # if mappings are all integers,  they are not options to HA
        values = self._sensor_dps.values(self._device)
        if values:
            for val in values:
                if isinstance(val, str):
                    return values


class InkbirdTimeToTempSensor(TuyaLocalSensor):
    """Estimate time to food-high from food channel 1, using native Fahrenheit."""

    def __init__(self, device, config):
        super().__init__(device, config)
        self._target_dp = config.find_dps("target")
        self._trend = TemperatureTrend()

    def on_receive(self, data, full_poll):
        if self._sensor_dps.id in data:
            self._trend.add(time(), self._sensor_dps.get_value(self._device))

    @property
    def native_value(self):
        raw = self._device.get_reported_property(self._target_dp.id)
        if raw is None:
            target = self._device._inkbird_food_targets.get(self._target_dp.id)
        else:
            # Read reported target without a pending command overlay.
            report = targets_packet_text(
                self._target_dp.decode_value(raw, self._device)
            )
            target = json.loads(report)["food_high"] if report else None
        return self._trend.minutes_to_target(target, time())

    @property
    def native_unit_of_measurement(self):
        return "min"

    @property
    def native_precision(self):
        return 1

    @property
    def suggested_display_precision(self):
        return 1
