"""Food-high commands and rolling time-to-target estimates."""

import json
from base64 import b64decode, b64encode

import pytest

from custom_components.tuya_local.helpers.device_config import get_config
from custom_components.tuya_local.helpers.inkbird import (
    TemperatureTrend,
    crc8_atm,
    targets_packet_text,
)
from custom_components.tuya_local.number import InkbirdFoodHighNumber, _number_entity
from custom_components.tuya_local.sensor import InkbirdTimeToTempSensor, _sensor_entity


def number_config(probe=1):
    return next(
        e
        for e in get_config("inkbird_int14sbw_thermometer").all_entities()
        if e.name == f"Probe {probe} food high target"
    )


@pytest.mark.parametrize("probe", range(1, 5))
def test_number_sends_full_packet_with_only_food_high(mocker, probe):
    device = mocker.MagicMock()
    device.get_reported_property.return_value = None
    dp = number_config(probe).find_dps("value")
    raw = b64decode(dp.get_values_to_set(device, 165)[str(121 + probe)])
    assert len(raw) == 20
    assert raw[0] == 16 and raw[11] == 0
    assert raw[3:5] == b"\x00\x00"
    assert raw[12:16] == b"\x00" * 4
    report = raw + bytes([crc8_atm(raw)])
    assert json.loads(targets_packet_text(report)) == {
        "food_high": 165,
        "food_low": None,
        "ambient_high": None,
        "ambient_low": None,
    }
    device.get_property.return_value = b64encode(report).decode()
    assert dp.get_value(device) == 165
    with pytest.raises(ValueError):
        dp.get_values_to_set(device, 573)


@pytest.mark.asyncio
async def test_number_restores_native_value_and_shares_target(mocker):
    device = mocker.MagicMock()
    device._inkbird_food_targets = {}
    device.get_property.return_value = None
    device.get_reported_property.return_value = None
    device.async_set_properties = mocker.AsyncMock()
    entity = _number_entity(device, number_config())
    assert isinstance(entity, InkbirdFoodHighNumber)
    mocker.patch(
        "homeassistant.components.number.RestoreNumber.async_added_to_hass",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        entity,
        "async_get_last_number_data",
        new=mocker.AsyncMock(return_value=mocker.Mock(native_value=165)),
    )
    await entity.async_added_to_hass()
    assert entity.native_value == 165
    assert device._inkbird_food_targets == {"122": 165}
    mocker.patch.object(entity, "async_write_ha_state")
    await entity.async_set_native_value(180)
    assert entity.native_value == 180
    assert device._inkbird_food_targets == {"122": 180}
    assert entity.extra_state_attributes["value_source"] == "last_requested"


def test_linear_trend_estimates_minutes_and_target_changes():
    trend = TemperatureTrend()
    for minute in range(6):
        trend.add(minute * 60, 100 + minute * 2)
    assert trend.minutes_to_target(130, 300) == 10
    assert trend.minutes_to_target(150, 300) == 20
    assert trend.minutes_to_target(110, 300) == 0
    assert trend.minutes_to_target(130, 391) is None
    assert trend.minutes_to_target(None, 300) is None


@pytest.mark.parametrize("delta", [0, -1, 0.001])
def test_flat_cooling_and_near_zero_trends_have_no_eta(delta):
    trend = TemperatureTrend()
    for minute in range(6):
        trend.add(minute * 60, 100 + minute * delta)
    assert trend.minutes_to_target(150, 300) is None


def test_disconnect_and_long_gap_reset_history():
    trend = TemperatureTrend()
    trend.add(0, 100)
    trend.add(30, 101)
    assert trend.minutes_to_target(150, 30) is None
    trend.add(60, 102)
    assert trend.minutes_to_target(150, 60) == 24
    trend.add(61, None)
    assert not trend.samples
    trend.add(62, 102)
    trend.add(200, 104)
    assert len(trend.samples) == 1
    assert trend.minutes_to_target(150, 200) is None


def test_trend_uses_only_last_five_minutes():
    trend = TemperatureTrend()
    for minute in range(16):
        trend.add(minute * 60, 100 + minute if minute >= 10 else 30)
    assert len(trend.samples) == 6
    assert trend.minutes_to_target(125, 900) == 10


def test_eta_sensor_reads_food_channel_one_and_last_requested_target(mocker):
    config = next(
        e
        for e in get_config("inkbird_int14sbw_thermometer").all_entities()
        if e.name == "Probe 1 time to temp"
    )
    device = mocker.MagicMock()
    device.get_reported_property.return_value = None
    device._inkbird_food_targets = {"122": 130}
    sensor = _sensor_entity(device, config)
    assert isinstance(sensor, InkbirdTimeToTempSensor)
    for minute in range(6):
        raw = bytearray(55)
        raw[2:4] = ((100 + minute * 2) * 100).to_bytes(2, "little", signed=True)
        raw[-1] = crc8_atm(raw[:-1])
        device.get_property.return_value = b64encode(raw).decode()
        mocker.patch(
            "custom_components.tuya_local.sensor.time", return_value=minute * 60
        )
        sensor.on_receive({"109": device.get_property.return_value}, False)
    assert sensor.native_value == 10
    assert sensor.native_unit_of_measurement == "min"
    sensor.on_receive({"104": 80}, False)
    assert len(sensor._trend.samples) == 6
    mocker.patch("custom_components.tuya_local.sensor.time", return_value=391)
    assert sensor.native_value is None


@pytest.mark.parametrize(
    "value,ready",
    [
        ("S2RkZGSO", True),
        (None, False),
        ("not base64!", False),
        ("", False),
        ("AAAA", False),
    ],
)
def test_battery_readiness(value, ready):
    from custom_components.tuya_local.helpers.inkbird import battery_packet_ready

    assert battery_packet_ready(value) is ready


def test_unknown_or_corrupt_battery_is_not_ready():
    from custom_components.tuya_local.helpers.inkbird import battery_packet_ready

    packet = bytes([75, 100, 127, 100, 100])
    assert not battery_packet_ready(
        b64encode(packet + bytes([crc8_atm(packet)])).decode()
    )
    packet = bytes([75, 100, 100, 100, 100])
    assert not battery_packet_ready(
        b64encode(packet + bytes([crc8_atm(packet) ^ 255])).decode()
    )
