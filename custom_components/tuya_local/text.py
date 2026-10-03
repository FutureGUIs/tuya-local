"""
Setup for different kinds of Tuya text entities
"""

import json
import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.components.text.const import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_PATTERN,
)
from homeassistant.const import ATTR_MODE
from homeassistant.helpers.restore_state import RestoreEntity

from .device import TuyaLocalDevice
from .entity import TuyaLocalEntity
from .helpers.config import async_tuya_setup_platform
from .helpers.device_config import TuyaEntityConfig
from .helpers.inkbird import parse_targets, targets_packet_text

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    config = {**config_entry.data, **config_entry.options}
    await async_tuya_setup_platform(
        hass,
        async_add_entities,
        config,
        "text",
        _text_entity,
    )


def _text_entity(device, config):
    if config.find_dps("value").write_handler == "inkbird_int14s_targets":
        return InkbirdTargetsText(device, config)
    return TuyaLocalText(device, config)


class TuyaLocalText(TuyaLocalEntity, TextEntity):
    """Representation of a Tuya Text Entity"""

    def __init__(self, device: TuyaLocalDevice, config: TuyaEntityConfig):
        """
        Initialise the text entity.
        Args:
            device (TuyaLocalDevice): the device API instance
            config (TuyaEntityConfig): the configuration for this entity
        """
        super().__init__()
        dps_map = self._init_begin(device, config)
        self._value_dp = dps_map.pop("value")
        if self._value_dp is None:
            raise AttributeError(f"{config.config_id} is missing value dp")

        self._attr_mode = TextMode.PASSWORD if self._value_dp.hidden else TextMode.TEXT
        self._extra_info = {ATTR_MODE: self._attr_mode}

        rng = self._value_dp.range(device, False)
        if rng:
            self._attr_native_min = rng[0]
            self._attr_native_max = rng[1]
            self._extra_info[ATTR_MIN] = self._attr_native_min
            self._extra_info[ATTR_MAX] = self._attr_native_max

        if self._value_dp.write_handler == "inkbird_int14s_targets":
            pass
        elif self._value_dp.rawtype == "hex":
            self._attr_pattern = "[0-9a-fA-F]*"
        elif self._value_dp.rawtype == "base64":
            self._attr_pattern = (
                "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
            )
        # TODO: general pattern support

        if hasattr(self, "_attr_pattern"):
            self._extra_info[ATTR_PATTERN] = self._attr_pattern

    @property
    def native_value(self) -> str | None:
        """Return the current value"""
        return self._value_dp.get_value(self._device)

    async def async_set_value(self, value: str) -> None:
        """Set the value"""
        _LOGGER.info("%s setting value to %s", self._config.config_id, value)
        await self._value_dp.async_set_value(self._device, value)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """As well as extra attributes specified in the config, also return info about the text."""
        return TuyaLocalEntity.extra_state_attributes.fget(self) | self._extra_info


class InkbirdTargetsText(TuyaLocalText, RestoreEntity):
    """Four targets in a single command, retaining the last request across restarts."""

    def __init__(self, device, config):
        super().__init__(device, config)
        self._last_requested = None

    async def async_added_to_hass(self):
        await RestoreEntity.async_added_to_hass(self)
        previous = await self.async_get_last_state()
        if previous:
            try:
                self._last_requested = json.dumps(
                    parse_targets(previous.state), separators=(",", ":")
                )
            except ValueError:
                pass
        await TuyaLocalText.async_added_to_hass(self)

    @property
    def native_value(self):
        return super().native_value or self._last_requested

    async def async_set_value(self, value):
        targets = parse_targets(value)
        await super().async_set_value(value)
        self._last_requested = json.dumps(targets, separators=(",", ":"))
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        # Pending overlays are requests, not confirmed device reports.
        raw = self._device._cached_state.get(self._value_dp.id)
        report = targets_packet_text(self._value_dp.decode_value(raw, self._device))
        return super().extra_state_attributes | {
            "value_source": "device_report" if report is not None else "last_requested",
            "target_unit": "°F",
        }
