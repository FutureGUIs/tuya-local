"""Complete INT-14S target commands can initialize without a device report."""

import json
from base64 import b64decode, b64encode

import pytest

from custom_components.tuya_local.helpers.device_config import (
    TuyaEntityConfig,
    get_config,
)
from custom_components.tuya_local.helpers.inkbird import (
    build_targets_packet,
    crc8_atm,
    targets_packet_text,
)
from custom_components.tuya_local.text import InkbirdTargetsText, _text_entity

TARGETS = {"food_high": 165, "food_low": 40, "ambient_high": 300, "ambient_low": 50}


def config_for(probe):
    # Legacy JSON helper remains supported internally, but is no longer exposed.
    return TuyaEntityConfig(
        get_config("inkbird_int14sbw_thermometer"),
        {
            "entity": "text",
            "name": f"Probe {probe} targets Fahrenheit",
            "dps": [
                {
                    "id": 121 + probe,
                    "type": "base64",
                    "name": "value",
                    "optional": True,
                    "length": 21,
                    "checksum": "crc8_atm",
                    "write_handler": "inkbird_int14s_targets",
                }
            ],
        },
    )


@pytest.mark.parametrize("probe", range(1, 5))
def test_full_targets_initialize_missing_packet(mocker, probe):
    mocker.patch(
        "custom_components.tuya_local.helpers.inkbird.time", return_value=123456
    )
    device = mocker.MagicMock()
    device.get_property.return_value = None
    device.get_reported_property.return_value = None
    dp = config_for(probe).find_dps("value")
    values = dp.get_values_to_set(device, json.dumps(TARGETS))
    raw = b64decode(values[str(121 + probe)])
    assert len(raw) == 20
    assert raw[0] == raw[11] == 17
    assert int.from_bytes(raw[7:11], "little") == 123456
    assert raw[5:7] == b"\x00\x00"
    assert raw[16:20] == b"\x00" * 4
    report = raw + bytes([crc8_atm(raw)])
    assert json.loads(targets_packet_text(report)) == TARGETS
    device.get_property.return_value = b64encode(report).decode()
    assert json.loads(dp.get_value(device)) == TARGETS


@pytest.mark.parametrize("field", TARGETS)
def test_null_disables_corresponding_alarm(field):
    targets = TARGETS | {field: None}
    raw = build_targets_packet(json.dumps(targets))
    assert json.loads(targets_packet_text(raw + bytes([crc8_atm(raw)]))) == targets


def test_preserves_metadata_when_current_packet_is_available():
    raw = bytearray(build_targets_packet(json.dumps(TARGETS)))
    for offset in (5, 6, 16, 17, 18, 19):
        raw[offset] = offset + 10
    raw.append(crc8_atm(raw))
    result = build_targets_packet(json.dumps(TARGETS | {"food_high": 180}), bytes(raw))
    for offset in (5, 6, 7, 8, 9, 10, 16, 17, 18, 19):
        assert result[offset] == raw[offset]
    assert (
        json.loads(targets_packet_text(result + bytes([crc8_atm(result)])))["food_high"]
        == 180
    )


@pytest.mark.parametrize(
    "value",
    [
        "165",
        "{}",
        "not json",
        json.dumps(TARGETS | {"extra": 1}),
        *[
            json.dumps(TARGETS | {"food_high": v})
            for v in (True, "165", 31, 573, float("nan"), float("inf"), 39)
        ],
    ],
)
def test_rejects_incomplete_invalid_or_reversed_targets(value):
    with pytest.raises(ValueError):
        build_targets_packet(value)


def test_rejects_corrupt_current_packet(mocker):
    device = mocker.MagicMock()
    raw = build_targets_packet(json.dumps(TARGETS))
    device.get_reported_property.return_value = b64encode(
        raw + bytes([crc8_atm(raw) ^ 255])
    ).decode()
    with pytest.raises(ValueError, match="Invalid current target packet"):
        config_for(1).find_dps("value").get_values_to_set(device, json.dumps(TARGETS))


@pytest.mark.asyncio
async def test_text_accepts_json_and_keeps_last_request(mocker):
    device = mocker.MagicMock()
    device.get_property.return_value = None
    device.get_reported_property.return_value = None
    device._cached_state = {}
    device.async_set_properties = mocker.AsyncMock()
    entity = _text_entity(device, config_for(1))
    assert isinstance(entity, InkbirdTargetsText)
    assert not hasattr(entity, "_attr_pattern")
    mocker.patch.object(entity, "async_write_ha_state")
    await entity.async_set_value(json.dumps(TARGETS))
    device.async_set_properties.assert_awaited_once()
    assert json.loads(entity.native_value) == TARGETS
    assert entity.extra_state_attributes["value_source"] == "last_requested"


@pytest.mark.asyncio
async def test_text_restores_last_request(mocker):
    device = mocker.MagicMock()
    device.get_property.return_value = None
    device.get_reported_property.return_value = None
    entity = _text_entity(device, config_for(1))
    mocker.patch(
        "homeassistant.helpers.restore_state.RestoreEntity.async_added_to_hass",
        new=mocker.AsyncMock(),
    )
    mocker.patch.object(
        entity,
        "async_get_last_state",
        new=mocker.AsyncMock(return_value=mocker.Mock(state=json.dumps(TARGETS))),
    )
    await entity.async_added_to_hass()
    assert json.loads(entity.native_value) == TARGETS
    device.register_entity.assert_called_once_with(entity)


def test_command_matches_reference_vendor_capture(mocker):
    mocker.patch(
        "custom_components.tuya_local.helpers.inkbird.time", return_value=0x12345678
    )
    packet = build_targets_packet(
        json.dumps(
            {
                "food_high": 158,
                "food_low": None,
                "ambient_high": None,
                "ambient_low": None,
            }
        )
    )
    # Published reference builder fixture; no CRC is transmitted on writes.
    assert packet.hex() == "102c060000000078563412000000000000000000"


def test_pending_twenty_byte_command_does_not_block_next_write(mocker):
    device = mocker.MagicMock()
    device.get_reported_property.return_value = None
    device.get_property.return_value = b64encode(
        build_targets_packet(json.dumps(TARGETS))
    ).decode()
    dp = config_for(1).find_dps("value")
    result = dp.get_values_to_set(device, json.dumps(TARGETS | {"food_high": 180}))
    raw = b64decode(result["122"])
    assert len(raw) == 20
    assert int.from_bytes(raw[1:3], "little") == 1800


def test_reported_property_excludes_pending_overlay(mocker):
    from custom_components.tuya_local.device import TuyaLocalDevice

    hass = mocker.MagicMock()
    hass.data = {"tuya_local": {}}
    mocker.patch("tinytuya.Device")
    device = TuyaLocalDevice("Test", "id", "host", "key", 3.5, None, hass)
    device._cached_state["122"] = "report"
    device._add_properties_to_pending_updates({"122": "command"})
    assert device.get_property("122") == "command"
    assert device.get_reported_property("122") == "report"
