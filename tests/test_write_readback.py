"""Read settings back promptly through the existing receive loop after writes."""

import pytest

from custom_components.tuya_local.device import TuyaLocalDevice


@pytest.fixture
def device(mocker):
    hass = mocker.MagicMock()
    hass.data = {"tuya_local": {}}
    hass.is_stopping = False
    api = mocker.patch("tinytuya.Device").return_value
    api.parent = None
    api.receive.return_value = {"dps": {"104": 81}}
    api.status.return_value = {"dps": {"104": 50}}
    api.updatedps.return_value = {"dps": {"122": "packet"}}
    subject = TuyaLocalDevice("Test", "id", "host", "key", 3.5, None, hass)
    subject._running = True
    subject._api_protocol_working = True
    subject._cached_state = {"updated_at": 100, "104": 81, "103": "S2RkZGSO"}
    subject._last_full_poll = 100
    subject._force_dps = [109, 103, 122, 123, 124, 125]

    async def execute(func, *args):
        return func(*args)

    hass.async_add_executor_job.side_effect = execute

    async def retry(func, message):
        return func()

    mocker.patch.object(subject, "_retry_on_failed_connection", side_effect=retry)
    mocker.patch("custom_components.tuya_local.device.time", return_value=100)
    return subject


@pytest.mark.asyncio
@pytest.mark.parametrize("dp,value", [("104", 50), ("122", "packet")])
async def test_write_readback_waits_two_seconds_then_reads_changed_dp(
    device, mocker, dp, value
):
    child = mocker.MagicMock()
    device._children.append(child)
    device._add_properties_to_pending_updates({dp: value})
    device._set_values({dp: value})
    child.schedule_update_ha_state.assert_called_once()
    assert device._write_readback_dps == {int(dp)}
    stream = device.async_receive()
    await anext(stream)
    device._api.status.assert_not_called()
    device._api.updatedps.assert_not_called()
    mocker.patch("custom_components.tuya_local.device.time", return_value=102)
    result = await anext(stream)
    if dp == "122":
        device._api.updatedps.assert_called_once_with([122])
    else:
        device._api.status.assert_called_once()
    assert result[dp] == value
    assert not device._write_readback_dps
    assert device._last_full_poll == 100
    await stream.aclose()


@pytest.mark.asyncio
async def test_mixed_writes_refresh_forced_and_status_properties(device, mocker):
    device._add_properties_to_pending_updates({"104": 50, "122": "packet"})
    device._set_values({"104": 50, "122": "packet"})
    mocker.patch("custom_components.tuya_local.device.time", return_value=102)
    stream = device.async_receive()
    assert (await anext(stream))["122"] == "packet"
    assert device._write_readback_dps == {104}
    assert (await anext(stream))["104"] == 50
    assert not device._write_readback_dps
    await stream.aclose()


@pytest.mark.asyncio
async def test_inkbird_poll_requests_status_and_forced_dps_each_cycle(device, mocker):
    from custom_components.tuya_local.helpers.device_config import get_config

    entity = mocker.MagicMock()
    entity._config = next(get_config("inkbird_int14sbw_thermometer").all_entities())
    device.register_entity(entity)
    assert device._POLLING_INTERVAL == 10
    mocker.patch("custom_components.tuya_local.device.time", return_value=111)
    stream = device.async_receive()
    assert (await anext(stream))["104"] == 50
    assert (await anext(stream))["122"] == "packet"
    device._api.status.assert_called_once()
    device._api.updatedps.assert_called_once_with(device._force_dps)
    assert device._last_full_poll == 111
    device._cached_state["updated_at"] = 111
    mocker.patch("custom_components.tuya_local.device.time", return_value=120)
    await anext(stream)
    device._api.status.assert_called_once()
    mocker.patch("custom_components.tuya_local.device.time", return_value=122)
    await anext(stream)
    await anext(stream)
    assert device._api.status.call_count == 2
    assert device._api.updatedps.call_count == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_other_devices_keep_alternating_thirty_second_poll(device, mocker):
    assert device._POLLING_INTERVAL == 30
    assert not device._poll_forced_with_status
    mocker.patch("custom_components.tuya_local.device.time", return_value=131)
    stream = device.async_receive()
    await anext(stream)
    device._api.updatedps.assert_called_once()
    device._api.status.assert_not_called()
    mocker.patch("custom_components.tuya_local.device.time", return_value=162)
    await anext(stream)
    device._api.status.assert_called_once()
    assert device._api.updatedps.call_count == 1
    await stream.aclose()


@pytest.mark.asyncio
async def test_startup_requests_battery_before_other_forced_datapoints(device, mocker):
    device._poll_forced_with_status = True
    device._POLLING_INTERVAL = 10
    device._cached_state.pop("103")
    device._api.updatedps.side_effect = [
        {"dps": {"103": "S2RkZGSO"}},
        {"dps": {"109": "temperatures"}},
    ]
    mocker.patch("custom_components.tuya_local.device.time", return_value=111)
    stream = device.async_receive()
    assert (await anext(stream))["104"] == 50
    assert (await anext(stream))["103"] == "S2RkZGSO"
    assert (await anext(stream))["109"] == "temperatures"
    assert device._api.updatedps.call_args_list == [
        mocker.call([103]),
        mocker.call(device._force_dps),
    ]
    await stream.aclose()


@pytest.mark.asyncio
async def test_startup_battery_retries_stop_when_complete_or_timed_out(device, mocker):
    device._poll_forced_with_status = True
    device._POLLING_INTERVAL = 10
    device._cached_state.pop("103")
    device._api.updatedps.return_value = {"dps": {"109": "temperatures"}}
    stream = device.async_receive()
    await anext(stream)
    device._api.updatedps.assert_called_once_with([103])
    mocker.patch("custom_components.tuya_local.device.time", return_value=104)
    await anext(stream)
    assert device._api.updatedps.call_count == 2
    device._cached_state["103"] = "S2RkZGSO"
    mocker.patch("custom_components.tuya_local.device.time", return_value=108)
    await anext(stream)
    assert device._api.updatedps.call_count == 2
    device._cached_state.pop("103")
    device._last_full_poll = 160
    device._cached_state["updated_at"] = 160
    mocker.patch("custom_components.tuya_local.device.time", return_value=161)
    await anext(stream)
    assert device._api.updatedps.call_count == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_startup_battery_retries_are_bounded(device, mocker):
    device._poll_forced_with_status = True
    device._cached_state.pop("103")
    device._battery_startup_attempts = 10
    stream = device.async_receive()
    await anext(stream)
    device._api.updatedps.assert_not_called()
    await stream.aclose()
