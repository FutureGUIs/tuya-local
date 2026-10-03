"""INT-14S LAN decoding, brightness control and binary packet validation."""

from base64 import b64encode

import pytest

from custom_components.tuya_local.device import TuyaLocalDevice
from custom_components.tuya_local.helpers.device_config import TuyaDpsConfig, get_config


def packet(payload):
    """Append CRC-8/ATM using the standard check polynomial."""
    crc = 0
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 0xFF
    return payload + bytes([crc])


def read_sensors(mocker, dps):
    device = mocker.MagicMock()
    device.get_property.side_effect = dps.get
    config = get_config("inkbird_int14sbw_thermometer")
    return {
        entity.name: entity.find_dps("sensor").get_value(device)
        for entity in config.all_entities()
        if entity.entity == "sensor"
    }


def test_validated_capture_and_unavailable_probes(mocker):
    """Decode a published LAN capture, including disconnected probe sentinels."""
    raw = bytes.fromhex(
        "b71efc1eb71ed51ed51f11031cfe7ffe7ffe7ffe7ffe7ffe7f7e"
        "fe7ffe7ffe7ffe7ffe7ffe7f7efe7ffe7ffe7ffe7ffe7ffe7f7e2003c7"
    )
    sensors = read_sensors(mocker, {"109": b64encode(raw).decode()})
    assert [sensors[f"Probe 1 food {i} temperature"] for i in range(1, 5)] == [
        79.32,
        78.63,
        78.93,
        81.49,
    ]
    assert sensors["Probe 1 ambient temperature"] == 78.5
    assert sensors["Station temperature"] == 80
    for probe in range(2, 5):
        assert sensors[f"Probe {probe} ambient temperature"] is None
        for channel in range(1, 5):
            assert sensors[f"Probe {probe} food {channel} temperature"] is None


def test_all_channel_offsets_signed_values_and_batteries(mocker):
    payload = bytearray()
    for probe in range(4):
        for channel in range(6):
            payload.extend(
                (-1000 + probe * 100 + channel).to_bytes(2, "little", signed=True)
            )
        payload.append(0x1C)
    payload.extend((800).to_bytes(2, "little"))
    sensors = read_sensors(
        mocker,
        {
            "109": b64encode(packet(payload)).decode(),
            "103": b64encode(packet(bytes([82, 75, 64, 53, 127]))).decode(),
            "104": 85,
        },
    )
    for probe in range(4):
        for channel in range(1, 5):
            assert (
                sensors[f"Probe {probe + 1} food {channel} temperature"]
                == (-1000 + probe * 100 + channel) / 100
            )
        assert (
            sensors[f"Probe {probe + 1} ambient temperature"]
            == (-995 + probe * 100) / 10
        )
    assert sensors["Station battery"] == 82
    assert [sensors[f"Probe {i} battery"] for i in range(1, 5)] == [75, 64, 53, None]
    assert sensors["Display brightness"] == 85


@pytest.mark.parametrize(
    "value", [None, "!not-base64!", "", "AA==", "MTIzNDU2Nzg5AA=="]
)
def test_invalid_binary_packet_never_returns_raw_string(mocker, value):
    device = mocker.MagicMock()
    device.get_property.return_value = value
    cfg = TuyaDpsConfig(
        mocker.MagicMock(),
        {
            "id": 109,
            "name": "sensor",
            "type": "base64",
            "mask": "FF",
            "checksum": "crc8_atm",
            "length": 10,
        },
    )
    assert cfg.get_value(device) is None


@pytest.mark.parametrize("encoding", ["hex", "base64"])
def test_crc8_standard_check_vector_and_corruption(mocker, encoding):
    device = mocker.MagicMock()
    cfg = TuyaDpsConfig(
        mocker.MagicMock(),
        {
            "id": 1,
            "name": "sensor",
            "type": encoding,
            "mask": "FF",
            "checksum": "crc8_atm",
            "length": 10,
        },
    )
    # Standard CRC-8/ATM check value for ASCII 123456789 is 0xF4.
    raw = b"123456789\xf4"
    for data, expected in [
        (raw, 0xF4),
        (raw[:-1] + b"\x00", None),
        (raw + b"\x00", None),
    ]:
        device.get_property.return_value = (
            data.hex() if encoding == "hex" else b64encode(data).decode()
        )
        assert cfg.get_value(device) == expected


def test_readonly_sensors_and_writable_controls(mocker):
    config = get_config("inkbird_int14sbw_thermometer")
    entities = list(config.all_entities())
    sensors = [entity for entity in entities if entity.entity == "sensor"]
    controls = [entity for entity in entities if entity.entity != "sensor"]
    assert len(sensors) == 31
    brightness = [entity for entity in controls if entity.name == "Display brightness"]
    targets = [entity for entity in controls if entity.name != "Display brightness"]
    assert len(brightness) == 1
    assert len(targets) == 4
    assert all(entity.entity == "number" for entity in targets)
    for entity in sensors:
        dp = entity.find_dps("sensor")
        assert dp.readonly
        assert dp.get_values_to_set(mocker.MagicMock(), 50) == {}
    control = brightness[0]
    assert control.entity == "number"
    dp = control.find_dps("value")
    device = mocker.MagicMock()
    device.get_property.return_value = 81
    assert dp.get_value(device) == 81
    assert dp.get_values_to_set(device, 50) == {"104": 50}
    for value in (0, 101):
        with pytest.raises(ValueError):
            dp.get_values_to_set(device, value)


def test_matches_owners_setup_capture_without_temperature_packet():
    config = get_config("inkbird_int14sbw_thermometer")
    assert config.matches({"101": "F", "102": True, "104": 81}, [])
    assert not config.matches({"101": "F", "102": True}, [])


def test_register_multisensor_refreshes_each_datapoint_once(mocker):
    """Shared packed DPs must not be repeated for each temperature channel."""
    hass = mocker.MagicMock()
    hass.is_running = True
    mocker.patch("tinytuya.Device")
    device = TuyaLocalDevice(
        "Test", "test-id", "test-host", "test-key", "3.5", None, hass
    )
    device._running = True
    device._cached_state = {"104": 81}
    config = get_config("inkbird_int14sbw_thermometer")
    for entity_config in config.all_entities():
        entity = mocker.MagicMock()
        entity._config = entity_config
        device.register_entity(entity)
    assert device._force_dps == [109, 103, 122, 123, 124, 125]
