"""Readings land in Home Assistant's statistics at the time they were taken."""

import datetime as dt
from unittest.mock import patch

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sontex.const import DOMAIN
from custom_components.sontex.statistics import build_rows

from .conftest import SAMPLE
from .test_integration import DATA, FETCH, _entity_id

WATER = "sontex:water_meter_10000074_volume"
PULSE = "sontex:pulse_adapter_10000001_volume"
ALLOCATOR = "sontex:heat_allocator_10000014_consumption"


async def _statistics(hass, period):
    await get_instance(hass).async_block_till_done()
    return await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, dt_util.utc_from_timestamp(0), None,
        {WATER, PULSE, ALLOCATOR}, period, None, {"state", "sum", "change"})


def _local(row):
    return dt_util.as_local(dt_util.utc_from_timestamp(row["start"]))


def test_rows_are_filed_under_the_hour_that_ends_at_the_reading(hass):
    rows = build_rows({"2026-02-01T00:00:00": 10.0, "2026-02-20T10:08:00": 12.5,
                       "2026-01-01T00:00:00": 4.0})
    zone = dt_util.get_default_time_zone()
    assert [(start.astimezone(zone).replace(tzinfo=None), state, total)
            for start, state, total in rows] == [
        (dt.datetime(2025, 12, 31, 23), 4.0, 0.0),
        (dt.datetime(2026, 1, 31, 23), 10.0, 6.0),
        (dt.datetime(2026, 2, 20, 10), 12.5, 8.5),
    ]


async def test_statistics_carry_the_meters_own_dates(recorder_mock, hass, sample_text):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(FETCH, return_value=[(f"{SAMPLE}.ini", sample_text)]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    hourly = await _statistics(hass, "hour")
    # The reading from 20 February 10:08 sits in that hour, whenever it was fetched.
    last = hourly[WATER][-1]
    assert _local(last).replace(tzinfo=None) == dt.datetime(2026, 2, 20, 10)
    assert last["state"] == 41.708
    # 15 month-start values and the reading itself.
    assert len(hourly[WATER]) == 16

    monthly = {(_local(row).year, _local(row).month): row for row in await _monthly(hass, WATER)}
    # January 2026 = value on 1 February minus value on 1 January.
    assert round(monthly[2026, 1]["change"], 3) == round(41.681 - 41.652, 3)
    assert round(monthly[2026, 2]["change"], 3) == round(41.708 - 41.681, 3)

    monthly = {(_local(row).year, _local(row).month): row for row in await _monthly(hass, PULSE)}
    assert round(monthly[2026, 1]["change"], 1) == round(2197.2 - 2126.9, 1)

    # The allocator starts again from zero at its set day; the total keeps rising.
    rows = (await _statistics(hass, "hour"))[ALLOCATOR]
    sums = [row["sum"] for row in rows]
    assert sums == sorted(sums)
    assert sums[-1] == 200  # 144 units up to the set day, 56 after it
    monthly = {(_local(row).year, _local(row).month): row for row in await _monthly(hass, ALLOCATOR)}
    assert monthly[2026, 1]["change"] == 56


async def _monthly(hass, statistic_id):
    return (await _statistics(hass, "month"))[statistic_id]


async def test_month_sensors(hass, sample_text):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(FETCH, return_value=[(f"{SAMPLE}.ini", sample_text)]):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    water = "74000010EE4D1607"
    state = hass.states.get(_entity_id(hass, water, "previous_month"))
    assert (state.state, state.attributes["unit_of_measurement"]) == ("0.029", "m³")
    assert hass.states.get(_entity_id(hass, water, "month_to_date")).state == "0.027"
    allocator = "14000010EE4D1608"
    assert hass.states.get(_entity_id(hass, allocator, "previous_month")).state == "56.0"
    assert hass.states.get(_entity_id(hass, allocator, "month_to_date")).state == "0.0"
