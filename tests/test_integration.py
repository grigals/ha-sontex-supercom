"""Run the integration inside Home Assistant with the FTP server faked."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sontex.const import (
    CONF_DIRECTORY,
    CONF_LOOKBACK_DAYS,
    CONF_PULSE_MEDIUM,
    CONF_SCAN_HOURS,
    DOMAIN,
    PULSE_GAS,
)

from .conftest import SAMPLE

DATA = {CONF_HOST: "ftp.example.com", CONF_PORT: 21, CONF_USERNAME: "user",
        CONF_PASSWORD: "pass", CONF_DIRECTORY: "/readouts/"}
FETCH = "custom_components.sontex.coordinator.fetch_new"


async def _setup(hass, sample_text, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=options or {})
    entry.add_to_hass(hass)
    with patch(FETCH, return_value=[(f"{SAMPLE}.ini", sample_text)]) as fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, fetch


def _entity_id(hass, address, key):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor" if not key.startswith("b:") else "binary_sensor",
        DOMAIN, f"{address}_{key.removeprefix('b:')}")
    assert entity_id, key
    return entity_id


async def test_config_flow(hass):
    with patch("custom_components.sontex.config_flow.count_readouts", return_value=3), \
            patch(FETCH, return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**DATA, CONF_HOST: "ftp://ftp.example.com/"})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "ftp.example.com"


async def test_config_flow_wrong_password(hass):
    import ftplib

    with patch("custom_components.sontex.config_flow.count_readouts",
               side_effect=ftplib.error_perm("530 Login incorrect")):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_devices_and_entities(hass, sample_text):
    entry, _ = await _setup(hass, sample_text)

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 69
    names = {device.name for device in devices}
    assert {"Heat allocator 10000014", "Water meter 10000074",
            "Water meter 10000001"} <= names

    allocator = "14000010EE4D1608"
    state = hass.states.get(_entity_id(hass, allocator, "heatcostallocator-0-0-0-0"))
    assert state.state == "56"
    assert state.attributes["state_class"] == "total_increasing"
    state = hass.states.get(_entity_id(hass, allocator, "flowtemperature-0-0-0-0"))
    assert (state.state, state.attributes["device_class"]) == ("22.03", "temperature")
    assert hass.states.get(_entity_id(hass, allocator, "externaltemperature-0-0-0-0")).state == "19.21"
    assert hass.states.get(_entity_id(hass, allocator, "b:fault")).state == "off"
    state = hass.states.get(_entity_id(hass, allocator, "reading_time"))
    assert state.attributes["device_class"] == "timestamp"
    assert state.state.startswith("2026-02-20T")

    water = "74000010EE4D1607"
    state = hass.states.get(_entity_id(hass, water, "volume-0-0-0-0"))
    assert state.state == "41.708"
    assert state.attributes["device_class"] == "water"
    assert state.attributes["unit_of_measurement"] == "m³"
    assert hass.states.get(_entity_id(hass, water, "b:tamper")).state == "off"
    assert hass.states.get(_entity_id(hass, water, "b:battery_low")).state == "off"
    assert hass.states.get(_entity_id(hass, water, "rssi")).state == "-60"

    # The pulse adapter ("Other") says it counts water.
    pulse = "01000010EE4D0A00"
    state = hass.states.get(_entity_id(hass, pulse, "volume-1-0-0-0"))
    assert (state.state, state.attributes["device_class"]) == ("2248.0", "water")

    # Fault and tamper flags sit under the device as diagnostics.
    registry = er.async_get(hass)
    tamper = registry.async_get(_entity_id(hass, water, "b:tamper"))
    assert tamper.entity_category == "diagnostic"
    assert tamper.device_id == registry.async_get(
        _entity_id(hass, water, "volume-0-0-0-0")).device_id


async def test_pulse_adapter_can_be_set_to_gas(hass, sample_text):
    await _setup(hass, sample_text, {CONF_PULSE_MEDIUM: PULSE_GAS,
                                     CONF_SCAN_HOURS: 6, CONF_LOOKBACK_DAYS: 30})
    state = hass.states.get(_entity_id(hass, "01000010EE4D0A00", "volume-1-0-0-0"))
    assert state.attributes["device_class"] == "gas"
    names = {device.name for device in dr.async_get(hass).devices.values()}
    assert "Gas meter 10000001" in names


async def test_values_survive_restart_and_ftp_outage(hass, sample_text):
    entry, _ = await _setup(hass, sample_text)
    assert await hass.config_entries.async_unload(entry.entry_id)

    with patch(FETCH, side_effect=OSError("network down")):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(_entity_id(hass, "74000010EE4D1607", "volume-0-0-0-0"))
    assert state.state == "41.708"


async def test_handled_files_are_not_fetched_again(hass, sample_text):
    entry, _ = await _setup(hass, sample_text)
    with patch(FETCH, return_value=[]) as fetch:
        await entry.runtime_data.async_refresh()
    already_done = fetch.call_args.args[5]
    assert already_done == {f"{SAMPLE}.ini"}
