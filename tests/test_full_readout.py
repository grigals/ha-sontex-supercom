"""A complete readout, concentrator included, inside Home Assistant.

Needs a complete readout file, which is kept out of git; skipped without one.
"""

from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sontex.const import DOMAIN

from .conftest import FULL_SAMPLES, full_sample_text
from .test_integration import DATA, FETCH

pytestmark = pytest.mark.skipif(not FULL_SAMPLES.exists(),
                                reason="complete readouts are not in git")

# All the concentrator may show; its settings include the network login.
CONCENTRATOR_ENTITIES = {
    "Last reading", "Devices in last readout", "Battery operating time",
    "Fabrication number", "Tamper", "Battery low", "External alarm",
    "Battery cut", "Power low", "Permanent error", "Temporary error",
}


async def test_full_readout(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(FETCH, return_value=[("readout.ini", full_sample_text())]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    concentrators = [d for d in devices if d.name.startswith("Concentrator ")]
    assert len(concentrators) == 1
    concentrator = concentrators[0]
    assert concentrator.sw_version
    assert concentrator.via_device_id is None
    assert any(d.name.startswith("Repeater ") for d in devices)
    # Every other device hangs off the concentrator.
    assert all(device.via_device_id == concentrator.id
               for device in devices if device is not concentrator)

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {e.original_name for e in entities
            if e.device_id == concentrator.id} == CONCENTRATOR_ENTITIES
